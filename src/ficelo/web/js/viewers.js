/* Files that deserve better than being shown as bytes.

   Each viewer takes the tab and a container and fills it. Which one applies
   is decided by `viewerFor`, from the filename — the server has already said
   whether the bytes are text, and that is not the same question. */

import { el, formatSize } from "./util.js";
import { renderMarkdown, sanitise } from "./markdown.js";

const IMAGE = /\.(png|jpe?g|gif|webp|bmp|ico|avif|svg)$/i;
const PDF = /\.pdf$/i;
const CSV = /\.(csv|tsv)$/i;
const NOTEBOOK = /\.ipynb$/i;
const AUDIO = /\.(mp3|m4a|aac|ogg|oga|wav|flac)$/i;
const VIDEO = /\.(mp4|m4v|mov|webm|ogv|avi|mkv)$/i;

export function viewerFor(path) {
  if (IMAGE.test(path)) return "image";
  if (PDF.test(path)) return "pdf";
  if (CSV.test(path)) return "csv";
  if (NOTEBOOK.test(path)) return "notebook";
  if (AUDIO.test(path)) return "audio";
  if (VIDEO.test(path)) return "video";
  return null;
}

function rawUrl(path) {
  return "/api/raw?path=" + encodeURIComponent(path);
}

export function downloadLink(tab) {
  return el("a", { href: rawUrl(tab.path), download: tab.name || "", textContent: "Download file" });
}

/* -- images, pdf, media --------------------------------------------------- */

function viewImage(tab, into) {
  // SVG is an inert image here. Direct visits to its resource are sandboxed.
  const src = /\.svg$/i.test(tab.path)
    ? "/preview" + tab.path.split("/").map(encodeURIComponent).join("/") : rawUrl(tab.path);
  const image = el("img", { className: "view-image", src, alt: tab.name });
  const caption = el("div", { className: "view-caption", role: "status", textContent: "Loading image…" });
  image.addEventListener("load", () => {
    caption.textContent =
      image.naturalWidth + " × " + image.naturalHeight + " · " + formatSize(tab.size || 0);
  });
  image.addEventListener("error", () => {
    caption.textContent = "This image could not be displayed.";
  });
  into.replaceChildren(el("div", { className: "view-centre" }, [image, caption, downloadLink(tab)]));
}

function viewPdf(tab, into) {
  into.replaceChildren(
    el("embed", { className: "view-pdf", src: rawUrl(tab.path), type: "application/pdf" })
  );
}

function viewMedia(tab, into, tag) {
  const player = el(tag, { className: "view-media", src: rawUrl(tab.path), controls: true, preload: "metadata" });
  const status = el("div", { className: "view-caption", role: "status", textContent: "Loading media…" });
  player.addEventListener("loadedmetadata", () => { status.textContent = ""; });
  player.addEventListener("waiting", () => { status.textContent = "Buffering…"; });
  player.addEventListener("playing", () => { status.textContent = ""; });
  player.addEventListener("canplay", () => { status.textContent = ""; });
  player.addEventListener("error", () => {
    status.textContent = "This browser could not play the file. Download it to open in another player.";
  });
  into.replaceChildren(el("div", { className: "view-centre" }, [player, status, downloadLink(tab)]));
}

/* -- delimited text -------------------------------------------------------- */

/* RFC 4180 rather than `split(",")`: a quoted field may contain commas,
   newlines and doubled quotes, and every real export has at least one. */
export function parseDelimited(text, delimiter) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  let at = 0;
  while (at < text.length) {
    const ch = text[at];
    if (quoted) {
      if (ch === '"') {
        if (text[at + 1] === '"') {
          field += '"';
          at += 2;
          continue;
        }
        quoted = false;
        at += 1;
        continue;
      }
      field += ch;
      at += 1;
      continue;
    }
    if (ch === '"' && field === "") {
      quoted = true;
      at += 1;
      continue;
    }
    if (ch === delimiter) {
      row.push(field);
      field = "";
      at += 1;
      continue;
    }
    if (ch === "\n" || ch === "\r") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      // A CRLF is one line ending, not two.
      at += ch === "\r" && text[at + 1] === "\n" ? 2 : 1;
      continue;
    }
    field += ch;
    at += 1;
  }
  if (field !== "" || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

const MAX_ROWS = 2000;
const MAX_COLUMNS = 200;
const MAX_CELLS = 20000;

function viewCsv(tab, into) {
  const rows = parseDelimited(tab.text || "", tab.path.toLowerCase().endsWith(".tsv") ? "\t" : ",");
  if (!rows.length) {
    into.replaceChildren(el("p", { className: "notice", textContent: "This file is empty." }));
    return;
  }
  const [header, ...body] = rows;
  const width = body.slice(0, MAX_ROWS).reduce((width, row) => Math.max(width, row.length), header.length);
  const columns = Array.from({ length: Math.min(width, MAX_COLUMNS) });
  const rowLimit = Math.min(MAX_ROWS, Math.floor(MAX_CELLS / (columns.length + 1)) - 1);
  const shown = body.slice(0, rowLimit);
  const table = el("table", { className: "view-table" }, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", { className: "rownum", textContent: "" }),
        ...columns.map((_, index) => el("th", { textContent: header[index] || "" })),
      ]),
    ]),
    el(
      "tbody",
      {},
      shown.map((line, index) =>
        el("tr", {}, [
          el("td", { className: "rownum", textContent: String(index + 1) }),
          ...columns.map((_, column) => el("td", { textContent: line[column] || "" })),
        ])
      )
    ),
  ]);
  const parts = [table];
  if (body.length > shown.length) {
    parts.push(
      el("p", {
        className: "notice",
        textContent:
          "Showing the first " + shown.length + " of " + body.length + " rows. Download the file to see all cells.",
      })
    );
  }
  if (width > columns.length) parts.push(el("p", { className: "notice",
    textContent: "Showing the first " + columns.length + " of " + width + " columns." }));
  if (body.length > shown.length || width > columns.length) parts.push(downloadLink(tab));
  into.replaceChildren(el("div", { className: "view-scroll" }, parts));
}

/* -- notebooks -------------------------------------------------------------- */

function source(cell) {
  // nbformat allows source as a string or as a list of lines.
  return Array.isArray(cell.source) ? cell.source.join("") : cell.source || "";
}

function outputNode(output) {
  const data = output.data || {};
  if (data["image/png"]) {
    const raw = Array.isArray(data["image/png"]) ? data["image/png"].join("") : data["image/png"];
    return el("img", { className: "nb-image", src: "data:image/png;base64," + raw.trim() });
  }
  if (output.output_type === "error") {
    return el("pre", {
      className: "nb-output is-error",
      // The traceback carries ANSI colour codes that would otherwise print
      // as literal escape sequences.
      textContent: (output.traceback || []).join("\n").replace(/\[[0-9;]*m/g, ""),
    });
  }
  const text = data["text/plain"] || output.text || "";
  return el("pre", {
    className: "nb-output",
    textContent: Array.isArray(text) ? text.join("") : text,
  });
}

function viewNotebook(tab, into, marked) {
  let notebook;
  try {
    notebook = JSON.parse(tab.text || "{}");
  } catch (err) {
    into.replaceChildren(
      el("p", { className: "notice is-error", textContent: "This notebook is not valid JSON." })
    );
    return;
  }
  const cells = notebook.cells || [];
  const body = el("div", { className: "notebook" });
  cells.forEach((cell, index) => {
    if (cell.cell_type === "markdown") {
      const block = el("div", { className: "nb-cell nb-markdown markdown" });
      renderMarkdown(source(cell), marked, block);
      body.append(block);
      return;
    }
    const block = el("div", { className: "nb-cell nb-code" }, [
      el("div", { className: "nb-gutter", textContent: "[" + (cell.execution_count || " ") + "]" }),
      el("pre", { className: "nb-source" }, [el("code", { textContent: source(cell) })]),
    ]);
    (cell.outputs || []).forEach((output) => block.append(outputNode(output)));
    body.append(block);
  });
  if (!cells.length) {
    body.append(el("p", { className: "notice", textContent: "This notebook has no cells." }));
  }
  sanitise(body);
  into.replaceChildren(el("div", { className: "view-scroll" }, [body]));
}

/* -- dispatch --------------------------------------------------------------- */

export function renderViewer(kind, tab, into, marked) {
  const cached = ["image", "pdf", "audio", "video"].includes(kind);
  const key = JSON.stringify([kind, tab.path, tab.stamp]);
  if (cached && tab.nativeViewer?.key === key) {
    into.replaceChildren(tab.nativeViewer.node);
    return;
  }
  disposeViewer(tab);
  fillViewer(kind, tab, into, marked);
  if (cached) tab.nativeViewer = { key, node: into.firstElementChild };
}

export function disposeViewer(tab) {
  const node = tab.nativeViewer?.node;
  if (!node) return;
  node.querySelectorAll("audio,video").forEach((player) => {
    player.pause(); player.removeAttribute("src"); player.load();
  });
  node.querySelectorAll("img").forEach((image) => image.removeAttribute("src"));
  node.remove();
  delete tab.nativeViewer;
}

function fillViewer(kind, tab, into, marked) {
  switch (kind) {
    case "image":
      return viewImage(tab, into);
    case "pdf":
      return viewPdf(tab, into);
    case "csv":
      return viewCsv(tab, into);
    case "notebook":
      return viewNotebook(tab, into, marked);
    case "audio":
      return viewMedia(tab, into, "audio");
    case "video":
      return viewMedia(tab, into, "video");
    default:
      return null;
  }
}
