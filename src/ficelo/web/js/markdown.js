/* Markdown rendering.

   Rendered a block at a time rather than in one call, so every top-level
   block carries the source line it came from. That is what makes an accurate
   two-pane scroll sync possible later; doing it here means there is one
   rendering path rather than two that drift. */

/* Tags that can execute or fetch, and the attributes that can do it without a
   tag. A markdown file is usually your own, but "usually" is not a security
   posture, and a rendered README from a repository you just cloned is not
   yours at all. */
import { configureMath, renderMath } from "./math.js";

const PARSE_OPTIONS = { gfm: true, breaks: false };
const FORBIDDEN_TAGS = ["script", "style", "iframe", "object", "embed", "link", "meta", "base", "form"];
const URL_ATTRS = ["href", "src", "xlink:href"];

export function sanitise(root) {
  for (const tag of FORBIDDEN_TAGS) {
    root.querySelectorAll(tag).forEach((node) => node.remove());
  }
  root.querySelectorAll("*").forEach((node) => {
    for (const attr of Array.from(node.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) {
        node.removeAttribute(attr.name);
        continue;
      }
      if (URL_ATTRS.includes(name)) {
        /* javascript: and data: URLs are the two that execute. Whitespace and
           control characters are stripped first because `java\nscript:` is
           the oldest way past a naive check. */
        const value = attr.value.replace(/[\s\u0000-\u001f]/g, "").toLowerCase();
        if (value.startsWith("javascript:") || value.startsWith("data:text/html")) {
          node.removeAttribute(attr.name);
        }
      }
    }
  });
  return root;
}

/* Split source into top-level blocks, each with the 1-based line it starts on.

   `token.raw` is exact, so counting newlines through the preceding tokens
   gives the line without re-parsing.

   Carry the reference-link definitions and registered extensions through
   both stages so rendering one block agrees with parsing the document. */
export function markdownBlocks(source, marked) {
  configureMath(marked);
  source = String(source == null ? "" : source);
  const metadata = frontMatter(source);
  if (metadata) source = source.slice(metadata.raw.length);
  const options = { ...marked.defaults, ...PARSE_OPTIONS };
  const tokens = marked.lexer(source, options);
  const blocks = metadata ? [{ line: 1, type: "metadata", rows: metadata.rows }] : [];
  let line = metadata ? 1 + (metadata.raw.match(/\n/g) || []).length : 1;
  for (const token of tokens) {
    const single = [token];
    single.links = tokens.links;
    /* A token whose raw is only whitespace contributes lines but no block:
       emitting it would put an empty anchor between two real ones and drag
       the scroll sync to the wrong place. */
    if (token.type !== "space") {
      blocks.push({ line, type: token.type, token, html: marked.parser(single, options) });
    }
    line += (token.raw.match(/\n/g) || []).length;
  }
  return blocks;
}

export function renderMarkdown(source, marked, into) {
  into.replaceChildren();
  for (const block of markdownBlocks(source, marked)) {
    if (block.type === "metadata") {
      const table = document.createElement("table");
      table.className = "front-matter";
      for (const [key, value] of block.rows) {
        const row = table.insertRow();
        const label = document.createElement("th");
        label.textContent = key;
        row.append(label);
        row.insertCell().textContent = value.trim();
      }
      into.append(table);
      continue;
    }
    const wrapper = document.createElement("div");
    wrapper.className = "md-block";
    wrapper.dataset.srcLine = String(block.line);
    wrapper.innerHTML = block.html;
    sanitise(wrapper);
    into.append(wrapper);
  }
  into.headings = renderedHeadings(into);
  into.mathReady = renderMath(into);
  return into;
}

/* Preserve nested or multi-line YAML as text; interpreting configuration is
   outside a reader's remit. Only a complete opening block is metadata. */
export function frontMatter(source) {
  source = String(source == null ? "" : source);
  const lines = source.split("\n");
  if (lines[0].trim() !== "---") return null;
  const end = lines.findIndex((line, index) => index > 0 && ["---", "..."].includes(line.trim()));
  if (end < 0 || !lines.slice(1, end).join("\n").trim()) return null;
  const rows = [];
  for (const raw of lines.slice(1, end)) {
    const line = raw.replace(/\r$/, "");
    if (!line.trim()) continue;
    const field = /^([A-Za-z0-9_.-]+)\s*:\s?(.*)$/.exec(line);
    if (field) rows.push([field[1], field[2]]);
    else if (rows.length) rows[rows.length - 1][1] += "\n" + line;
    else rows.push(["", line]);
  }
  const raw = lines.slice(0, end + 1).join("\n") + (end < lines.length - 1 ? "\n" : "");
  return { raw, rows };
}

/* Headings, for the contents pane. Ids are assigned here rather than trusted
   from the renderer so that the pane and the document cannot disagree. */
export function outline(source, marked) {
  // A template is inert: discovering headings must not fetch images or run
  // anything. Rendered headings include lists, quotations and embedded HTML.
  const template = document.createElement("template");
  template.innerHTML = markdownBlocks(source, marked).filter((block) => block.html)
    .map((block) => '<div data-src-line="' + block.line + '">' + block.html + "</div>").join("");
  sanitise(template.content);
  return renderedHeadings(template.content);
}

function renderedHeadings(root) {
  const headings = Array.from(root.querySelectorAll("h1,h2,h3,h4"));
  const counts = new Map();
  return headings.map((node) => {
    const text = node.textContent;
    if (headings.length >= 2) {
      const base = text.toLowerCase().replace(/[^\w\s-]/g, "").trim()
        .replace(/\s+/g, "-").slice(0, 60) || "section";
      const count = (counts.get(base) || 0) + 1;
      counts.set(base, count);
      node.id = base + (count > 1 ? "-" + count : "");
    }
    return { depth: Number(node.tagName[1]), text, slug: node.id,
      line: Number(node.closest("[data-src-line]")?.dataset.srcLine || 1) };
  });
}

export { colouriseCode } from "./highlighter.js";
