/* Keeping the source and the rendered document in step.

   Line numbers alone are not enough: a one-line heading may render eighty
   pixels tall and a ten-line paragraph forty. So the two panes are pinned
   together at the points where they are known to correspond — the start of
   every top-level block, which `markdownBlocks` stamps onto the DOM as
   data-src-line — and everything between two anchors is interpolated.

   The functions here are pure and take plain numbers, which is what makes
   them testable without a layout engine. */

/* Read the anchors out of a rendered preview.
   Returns [{line, top}] sorted by line, with a sentinel for the end so that
   a position past the last block still interpolates rather than clamping. */
export function anchorsFrom(container, lastLine) {
  const blocks = Array.from(container.querySelectorAll(".md-block"));
  const anchors = blocks.map((node) => ({
    line: Number(node.dataset.srcLine) || 1,
    top: topWithin(container, node),
  }));
  if (!anchors.length) return [];
  const final = blocks[blocks.length - 1];
  anchors.push({
    line: Math.max(lastLine || 0, anchors[anchors.length - 1].line + 1),
    top: topWithin(container, final) + final.offsetHeight,
  });
  return anchors;
}

/* Coordinates belong to the scrollport, even with nested positioned blocks. */
export function topWithin(container, node) {
  return node.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
}

/* Source pixels include wrapping and partial-line scrolling. Full document
   endpoints keep the first and final stretches moving between blocks. */
export function pixelAnchors(container, editor) {
  const anchors = [{ editor: 0, preview: 0 }];
  const add = (source, rendered) => {
    const previous = anchors[anchors.length - 1];
    if (source > previous.editor && rendered > previous.preview) anchors.push({ editor: source, preview: rendered });
  };
  container.querySelectorAll(".md-block[data-src-line]").forEach((node) => {
    const line = Number(node.dataset.srcLine);
    if (line > 0) add(editor.getTopForLineNumber(line), topWithin(container, node));
  });
  add(editor.getContentHeight(), container.scrollHeight);
  return anchors;
}

export function interpolatePixels(anchors, value, from = "editor", to = "preview") {
  if (!anchors.length) return 0;
  if (value <= anchors[0][from]) return anchors[0][to];
  const last = anchors[anchors.length - 1];
  if (value >= last[from]) return last[to];
  const index = span(anchors, value, from);
  const start = anchors[index], end = anchors[index + 1];
  return start[to] + (value - start[from]) / (end[from] - start[from]) * (end[to] - start[to]);
}

function span(anchors, at, key) {
  /* The last pair whose start is at or before `at`. A plain loop rather than
     a binary search: a document with a thousand blocks is a long one, and
     this runs once per scroll event. */
  let index = 0;
  for (let i = 0; i < anchors.length - 1; i += 1) {
    if (anchors[i][key] <= at) index = i;
    else break;
  }
  return index;
}

/* Where in the preview a given source line sits. */
export function topForLine(anchors, line) {
  if (!anchors.length) return 0;
  if (line <= anchors[0].line) return anchors[0].top;
  const last = anchors[anchors.length - 1];
  if (line >= last.line) return last.top;
  const at = span(anchors, line, "line");
  const a = anchors[at];
  const b = anchors[at + 1];
  const reach = b.line - a.line;
  if (reach <= 0) return a.top;
  return a.top + ((line - a.line) / reach) * (b.top - a.top);
}

/* Which source line corresponds to a given offset in the preview. */
export function lineForTop(anchors, top) {
  if (!anchors.length) return 1;
  if (top <= anchors[0].top) return anchors[0].line;
  const last = anchors[anchors.length - 1];
  if (top >= last.top) return last.line;
  const at = span(anchors, top, "top");
  const a = anchors[at];
  const b = anchors[at + 1];
  const reach = b.top - a.top;
  if (reach <= 0) return a.line;
  return a.line + ((top - a.top) / reach) * (b.line - a.line);
}

/* A re-entrancy guard.

   Both panes are synced, so each one's scroll handler fires the other's,
   which fires the first again. The flag is cleared on the next frame rather
   than immediately, because the scroll event the programmatic change causes
   does not arrive until then. */
export function makeGuard() {
  let held = false;
  return {
    get busy() {
      return held;
    },
    /* Refused while a sync is in flight — for handlers reacting to a scroll. */
    run(fn) {
      if (held) return false;
      held = true;
      try {
        fn();
      } finally {
        requestAnimationFrame(() => {
          held = false;
        });
      }
      return true;
    },
    /* Always runs — for a deliberate jump that must not be dropped because
       a scroll happened to be settling. */
    force(fn) {
      held = true;
      try {
        fn();
      } finally {
        requestAnimationFrame(() => {
          held = false;
        });
      }
    },
  };
}
