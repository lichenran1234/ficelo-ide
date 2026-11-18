/* Compare the loaded file with disk without replacing the reader's buffer. */

/* Bounded Myers diff, with unchanged ends removed first. Very large rewrites
   fall back to one changed span instead of blocking scrolling with a quadratic
   comparison. Indices are zero-based, with exclusive ends in both versions. */
export function changedRanges(before, after, budget = 200000) {
  let first = 0, oldEnd = before.length, newEnd = after.length;
  while (first < oldEnd && first < newEnd && before[first] === after[first]) first++;
  while (oldEnd > first && newEnd > first && before[oldEnd - 1] === after[newEnd - 1]) { oldEnd--; newEnd--; }
  if (first === oldEnd && first === newEnd) return [];
  const fallback = () => [{ start: first, end: oldEnd, newStart: first, newEnd }];
  const n = oldEnd - first, m = newEnd - first;
  if (!n || !m) return fallback();
  const trace = [], frontier = new Map([[1, 0]]);
  let work = 0;
  for (let depth = 0; depth <= n + m; depth++) {
    trace.push(new Map(frontier));
    for (let diagonal = -depth; diagonal <= depth; diagonal += 2) {
      if (++work > budget) return fallback();
      const down = diagonal === -depth || (diagonal !== depth &&
        (frontier.get(diagonal - 1) ?? -1) < (frontier.get(diagonal + 1) ?? -1));
      let x = down ? frontier.get(diagonal + 1) : frontier.get(diagonal - 1) + 1;
      let y = x - diagonal;
      while (x < n && y < m && before[first + x] === after[first + y]) {
        if (++work > budget) return fallback();
        x++; y++;
      }
      frontier.set(diagonal, x);
      if (x < n || y < m) continue;
      const steps = [];
      for (let d = depth; d >= 0; d--) {
        const previous = trace[d], k = x - y;
        const next = k === -d || (k !== d && (previous.get(k - 1) ?? -1) < (previous.get(k + 1) ?? -1)) ? k + 1 : k - 1;
        const px = previous.get(next) ?? 0, py = px - next;
        while (x > px && y > py) { steps.push(0); x--; y--; }
        if (d) {
          if (x === px) { steps.push(1); y--; }
          else { steps.push(-1); x--; }
        }
      }
      const changes = [];
      let oldAt = first, newAt = first, change = null;
      for (const step of steps.reverse()) {
        if (!step) { change = null; oldAt++; newAt++; continue; }
        if (!change) {
          change = { start: oldAt, end: oldAt, newStart: newAt, newEnd: newAt };
          changes.push(change);
        }
        if (step < 0) change.end = ++oldAt;
        else change.newEnd = ++newAt;
      }
      return changes;
    }
  }
  return fallback();
}

/* Locate external changes in a buffer which also has unsaved local edits. */
export function mapChanges(changes, localChanges) {
  const position = (at, end) => {
    let offset = 0;
    for (const change of localChanges) {
      if (at < change.start || (end && at === change.start)) break;
      if (at < change.end) {
        const fraction = (at - change.start) / (change.end - change.start);
        return change.newStart + fraction * (change.newEnd - change.newStart);
      }
      offset = change.newEnd - change.end;
    }
    return at + offset;
  };
  return changes.map((change) => {
    const start = position(change.start, false);
    return { ...change, start, end: Math.max(start, position(change.end, true)) };
  });
}

/* Also serialised into HTML previews; keep this function self-contained.
   Off-screen changes take priority. If all changes are visible, highlight
   exactly one half; ties go down, including unlocatable/binary changes. */
export function changeDirections(regions, height, changed = true) {
  if (!changed) return { top: false, bottom: false };
  let top = false, bottom = false, upper = 0, lower = 0;
  const middle = height / 2;
  for (const region of regions) {
    const { start, end, weight = 1 } = region;
    if (![start, end, weight].every(Number.isFinite) || end < start) continue;
    top ||= start < -0.5;
    bottom ||= end > height + 0.5;
    if (end < 0 || start > height) continue;
    if (end === start) {
      if (start < middle) upper += weight; else lower += weight;
    } else {
      upper += weight * Math.max(0, Math.min(end, middle) - Math.max(start, 0)) / (end - start);
      lower += weight * Math.max(0, Math.min(end, height) - Math.max(start, middle)) / (end - start);
    }
  }
  return top || bottom ? { top, bottom } : { top: upper > lower, bottom: upper <= lower };
}

/* DOM tokens retain real node positions, so tables, collapsed data and
   minified HTML are compared in reading order rather than by scroll ratio. */
export function domSnapshot(root, attributes = false) {
  const tokens = [], anchors = [];
  const add = (value, anchor) => { tokens.push(value); anchors.push(anchor); };
  const walk = (node) => {
    if (tokens.length > 100000) return;
    if (node.nodeType === 3) {
      const text = node.data;
      for (const match of text.matchAll(/\s+|\S+/g)) {
        add("text:" + match[0], { node, from: match.index, to: match.index + match[0].length });
        if (tokens.length > 100000) break;
      }
    } else if (node.nodeType === 1) {
      const attrs = attributes ? Array.from(node.attributes).filter((attr) => attr.name !== "data-ficelo-change")
        .map((attr) => [attr.name, attr.value]).sort(([a], [b]) => a.localeCompare(b)) : [];
      add("open:" + node.tagName + JSON.stringify(attrs), { node, edge: "top" });
      for (const child of node.childNodes) walk(child);
      add("close:" + node.tagName, { node, edge: "bottom" });
    }
  };
  walk(root);
  return tokens.length > 100000 ? null : { tokens, anchors };
}

/* Also serialised into HTML previews. All positions are relative to the
   visible scrollport. Insertions use the boundary before the next token. */
export function domChangeRegions(anchors, changes, viewportTop = 0) {
  const regions = [];
  let measurements = 0;
  const measure = (anchor, insertion) => {
    if (!anchor?.node?.isConnected) return null;
    const node = anchor.node, element = node.nodeType === 1 ? node : node.parentElement;
    if (!element || element.closest("head,script,style,template")) return null;
    let box;
    if (node.nodeType === 3 && node.data.length >= anchor.to) {
      const range = node.ownerDocument.createRange();
      range.setStart(node, anchor.from); range.setEnd(node, anchor.to);
      if (range.getClientRects().length) box = range.getBoundingClientRect();
    }
    if (!box?.height) {
      let visible = element;
      while (visible && !visible.getClientRects().length) visible = visible.parentElement;
      box = visible?.getBoundingClientRect();
    }
    if (!box) return null;
    let start = box.top - viewportTop, end = box.bottom - viewportTop;
    if (insertion) start = end = anchor.edge === "bottom" ? end : start;
    return { start, end };
  };
  for (let changeIndex = 0; changeIndex < changes.length; changeIndex++) {
    const change = changes[changeIndex];
    const count = change.end - change.start;
    const weight = change.weight ?? Math.max(count, change.newEnd - change.newStart, 1);
    if (!count) {
      const index = Math.min(Math.floor(change.start), anchors.length - 1);
      const region = measure(anchors[index], true);
      if (region) regions.push({ ...region, weight });
    } else {
      for (let index = Math.floor(change.start); index < Math.ceil(change.end);) {
        if (measurements >= 512) {
          // Bound layout work for large rewrites. The common ancestor covers
          // the remaining source span, including any intervening unchanged
          // content, rather than measuring thousands of individual nodes.
          const last = changes[changes.length - 1];
          const end = anchors[Math.min(anchors.length - 1, Math.floor(Math.max(last.start, last.end - 1)))]?.node;
          let node = anchors[index]?.node;
          if (node?.nodeType === 3) node = node.parentElement;
          while (node && end && !node.contains(end)) node = node.parentElement;
          const region = measure({ node }, false);
          if (region && node.scrollHeight > node.clientHeight) {
            const contentTop = region.start - node.scrollTop;
            region.start = Math.min(region.start, contentTop);
            region.end = Math.max(region.end, contentTop + node.scrollHeight);
          }
          const remaining = weight * (change.end - index) / count + changes.slice(changeIndex + 1)
            .reduce((sum, next) => sum + (next.weight ?? Math.max(next.end - next.start, next.newEnd - next.newStart, 1)), 0);
          if (region) regions.push({ ...region, weight: remaining });
          return regions;
        }
        const anchor = anchors[index];
        let next = index + 1, grouped = anchor;
        // One Range for a changed run in a text node avoids repeated layout
        // queries for each word in a long log or preformatted HTML block.
        if (anchor?.node?.nodeType === 3) {
          while (next < Math.ceil(change.end) && anchors[next]?.node === anchor.node) next++;
          grouped = { ...anchor, to: anchors[next - 1].to };
        }
        const region = measure(grouped, false);
        measurements++;
        if (region) regions.push({ ...region, weight: weight * (next - index) / count });
        index = next;
      }
    }
  }
  return regions;
}
