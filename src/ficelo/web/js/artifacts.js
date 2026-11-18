/* Lightweight previews built from text nodes. Documents with their own
   scripts run only in an opaque-origin sandbox. */

import { el, parentOf } from "./util.js";
import { shortcutFor } from "./shortcuts.js";
import { domSnapshot, domChangeRegions, changeDirections } from "./changes.js";

export function artifactFor(path) {
  if (/\.html?$/i.test(path)) return "html";
  if (/\.json$/i.test(path)) return "json";
  if (/\.(jsonl|ndjson)$/i.test(path)) return "jsonl";
  if (/\.log$/i.test(path)) return "log";
  if (/\.(diff|patch)$/i.test(path)) return "patch";
  return null;
}

function jsonNode(value, budget, depth = 0, name = "") {
  if (--budget.left < 0) return el("span", { textContent: "…" });
  if (value === null || typeof value !== "object") {
    return el("span", { className: "json-value is-" + typeof value,
      textContent: name + JSON.stringify(value) });
  }
  const entries = Object.entries(value);
  const label = entries.length + (Array.isArray(value) ? " items" : " keys");
  const branch = el("details", { className: "json-branch", open: depth < 2 }, [
    el("summary", { textContent: name + label }),
  ]);
  if (depth >= 50) { branch.append(el("span", { textContent: "Nesting limit reached" })); return branch; }
  for (const [key, child] of entries) {
    if (budget.left <= 0) break;
    branch.append(el("div", { className: "json-entry" }, [jsonNode(child, budget, depth + 1, key + ": ")]));
  }
  return branch;
}

export function renderJson(text, lines = false) {
  const body = el("div", { className: "view-scroll json-document" });
  const budget = { left: 20000 };
  if (!lines) {
    try { body.append(jsonNode(JSON.parse(text), budget)); }
    catch (error) {
      body.append(el("p", { className: "notice is-error", textContent: error.message }),
        el("pre", { textContent: text }));
    }
  } else {
    const rows = text.split(/\r?\n/);
    let count = 0;
    for (let index = 0; index < rows.length; index += 1) {
      if (!rows[index].trim()) continue;
      if (count++ >= 2000 || budget.left <= 0) {
        body.append(el("p", { className: "notice", textContent: "Preview limit reached. Source contains every record." }));
        break;
      }
      const record = el("section", { className: "json-record" }, [el("small", { textContent: "Line " + (index + 1) })]);
      try { record.append(jsonNode(JSON.parse(rows[index]), budget)); }
      catch (error) { record.append(el("pre", { className: "is-error", textContent: rows[index] })); }
      body.append(record);
    }
  }
  if (budget.left <= 0) body.append(el("p", { className: "notice", textContent: "Preview limited to 20,000 values. Source contains the complete file." }));
  return body;
}

const ANSI = ["#24292f", "#cf222e", "#1a7f37", "#9a6700", "#0969da", "#8250df", "#1b7c83", "#a0a0a0",
  "#6e7781", "#ff7b72", "#7ee787", "#e3b341", "#79c0ff", "#d2a8ff", "#76e3ea", "#ffffff"];

function ansiColour(number) {
  if (number < 16) return ANSI[number];
  if (number >= 232) return "rgb(" + Array(3).fill(8 + (number - 232) * 10).join(",") + ")";
  const n = number - 16;
  const level = (value) => value ? 55 + value * 40 : 0;
  return "rgb(" + [Math.floor(n / 36), Math.floor(n / 6) % 6, n % 6].map(level).join(",") + ")";
}

export function renderAnsi(text, into = el("pre", { className: "log-document" })) {
  const pattern = /\x1b\[([0-9;]*)([A-Za-z])|\x1b\][^\x07]*(?:\x07|\x1b\\)/g;
  let at = 0;
  let style = {};
  const append = (content) => { if (content) { const span = el("span", { textContent: content }); Object.assign(span.style, style); into.append(span); } };
  for (const match of text.matchAll(pattern)) {
    append(text.slice(at, match.index));
    at = match.index + match[0].length;
    if (match[2] !== "m") continue;
    const codes = (match[1] || "0").split(";").map(Number);
    for (let i = 0; i < codes.length; i += 1) {
      const code = codes[i];
      if (code === 0) style = {};
      else if (code === 1) style.fontWeight = "bold";
      else if (code === 22) delete style.fontWeight;
      else if (code === 39) delete style.color;
      else if (code === 49) delete style.backgroundColor;
      else if (code >= 30 && code <= 37) style.color = ANSI[code - 30];
      else if (code >= 90 && code <= 97) style.color = ANSI[code - 90 + 8];
      else if (code >= 40 && code <= 47) style.backgroundColor = ANSI[code - 40];
      else if (code >= 100 && code <= 107) style.backgroundColor = ANSI[code - 100 + 8];
      else if (code === 38 || code === 48) {
        const key = code === 38 ? "color" : "backgroundColor";
        if (codes[i + 1] === 5 && codes[i + 2] >= 0 && codes[i + 2] < 256) {
          style[key] = ansiColour(codes[i + 2]); i += 2;
        } else if (codes[i + 1] === 2 && codes.slice(i + 2, i + 5).length === 3) {
          style[key] = "rgb(" + codes.slice(i + 2, i + 5).map((v) => Math.max(0, Math.min(255, v))).join(",") + ")"; i += 4;
        }
      }
    }
  }
  append(text.slice(at));
  return into;
}

export function renderLines(text, patch) {
  const all = text.split("\n");
  const dropped = Math.max(0, all.length - 5000);
  const body = el("div", { className: "view-scroll" });
  if (dropped) body.append(el("p", { className: "notice", textContent: dropped + " earlier lines omitted from preview. Source contains the complete file." }));
  if (!patch) {
    // Parse before clipping so a colour selected earlier remains in force.
    const log = renderAnsi(text);
    if (dropped) {
      let remaining = dropped;
      for (const span of Array.from(log.children)) {
        const lines = span.textContent.split("\n");
        if (remaining >= lines.length) { remaining -= lines.length - 1; span.remove(); }
        else if (remaining) { span.textContent = lines.slice(remaining).join("\n"); remaining = 0; }
      }
    }
    body.append(log);
  } else {
    const pre = el("pre", { className: "patch-document" });
    all.slice(dropped).forEach((line) => {
      const kind = /^(diff |index |---|\+\+\+)/.test(line) ? "header" : line.startsWith("@@") ? "hunk"
        : line.startsWith("+") ? "add" : line.startsWith("-") ? "remove" : "context";
      pre.append(el("div", { className: "patch-line is-" + kind, textContent: line || " " }));
    });
    body.append(pre);
  }
  return body;
}

/* This function is serialised into the frame; it closes over nothing. */
function previewBridge(saved, shortcutFor, changeRefs, domChangeRegions, changeDirections) {
  // Establish a private port before any document scripts run. Untrusted
  // content must not be able to forge app commands with window.postMessage.
  const channel = new MessageChannel();
  const sendShortcut = channel.port1.postMessage.bind(channel.port1);
  // Capture native getters and their invocation before document scripts can
  // replace them. The parent rechecks the command in its own trusted realm.
  const keyReaders = ["key", "code", "altKey", "ctrlKey", "metaKey", "shiftKey", "isComposing", "keyCode"].map((name) => ({
    name, read: Function.prototype.call.bind(Object.getOwnPropertyDescriptor(KeyboardEvent.prototype, name).get),
  }));
  parent.postMessage({ ficelo: "shortcuts" }, "*", [channel.port2]);
  addEventListener("keydown", (event) => {
    if (!event.isTrusted) return;
    const command = shortcutFor(event);
    if (!command) return;
    const keys = { __proto__: null };
    for (let i = 0; i < keyReaders.length; i++) keys[keyReaders[i].name] = keyReaders[i].read(event);
    event.preventDefault();
    event.stopImmediatePropagation();
    sendShortcut({ command, keys });
  }, true);
  let restored = false;
  let pendingEdge = null;
  let changes = null, changeRevision = 0, changeAnchors = [];
  let hash = location.hash || saved.anchor || "";
  const report = () => {
    if (!restored) return;
    const box = document.scrollingElement;
    parent.postMessage({ ficelo: "position", anchor: hash || location.hash,
      frac: box && box.scrollHeight > box.clientHeight ? box.scrollTop / (box.scrollHeight - box.clientHeight) : 0 }, "*");
    if (changes) parent.postMessage({ ficelo: "change-directions", revision: changeRevision,
      directions: changeDirections(domChangeRegions(changeAnchors, changes), innerHeight) }, "*");
  };
  const scrollToEdge = (edge) => {
    const box = document.scrollingElement;
    if (box) box.scrollTop = edge === "bottom" ? box.scrollHeight : 0;
    hash = "";
    report();
  };
  addEventListener("message", (event) => {
    if (event.source !== parent) return;
    if (event.data?.ficelo === "active") {
      if (event.data.active === false) document.querySelectorAll("audio,video").forEach((player) => player.pause());
      return;
    }
    if (event.data?.ficelo === "changes" && Array.isArray(event.data.changes)) {
      changes = event.data.changes;
      changeRevision = event.data.revision;
      report();
      return;
    }
    if (event.data?.ficelo !== "scroll-edge" || !["top", "bottom"].includes(event.data.edge)) return;
    if (restored) scrollToEdge(event.data.edge);
    else pendingEdge = event.data.edge;
  });
  for (const name of ["pushState", "replaceState"]) {
    const original = history[name].bind(history);
    history[name] = function(state, title, url) {
      try { original(state, title, url); }
      catch (error) { if (typeof url !== "string" || !url.startsWith("#")) throw error; }
      if (typeof url === "string" && url.includes("#")) hash = url.slice(url.indexOf("#"));
      report();
    };
  }
  if (!location.hash && hash) {
    try { history.replaceState(null, "", "about:srcdoc" + (hash.startsWith("#") ? hash : "#" + hash)); }
    catch (error) { location.hash = hash; }
  }
  addEventListener("load", () => {
    const changeElements = new Map(Array.from(document.querySelectorAll("[data-ficelo-change]"),
      (node) => [node.getAttribute("data-ficelo-change"), node]));
    changeAnchors = changeRefs.map(([id, child, from, to, edge]) => {
      const element = changeElements.get(String(id));
      return { node: child === null ? element : element?.childNodes[child], from, to, edge };
    });
    const box = document.scrollingElement;
    if (box && Number.isFinite(saved.frac)) box.scrollTo({ top: saved.frac * (box.scrollHeight - box.clientHeight), behavior: "instant" });
    requestAnimationFrame(() => {
      restored = true;
      if (pendingEdge) scrollToEdge(pendingEdge);
      else report();
    });
  });
  let timer;
  addEventListener("scroll", () => { clearTimeout(timer); timer = setTimeout(report, 150); }, true);
  addEventListener("resize", report);
  new ResizeObserver(report).observe(document.documentElement);
  addEventListener("hashchange", () => { hash = location.hash; report(); });
  document.addEventListener("click", (event) => {
    const link = event.target.closest && event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const href = link.getAttribute("href");
    if (href.startsWith("#")) {
      event.preventDefault();
      location.hash = href;
      hash = location.hash;
      report();
      return;
    }
    const target = new URL(href, document.baseURI);
    if (target.origin === new URL(document.baseURI).origin && target.pathname.startsWith("/preview/")) {
      event.preventDefault();
      parent.postMessage({ ficelo: "navigate", href: target.pathname.slice(8) + target.hash }, "*");
    } else { link.target = "_blank"; link.rel = "noopener noreferrer"; }
  });
}

export function htmlSnapshot(text) {
  const document = new DOMParser().parseFromString(text, "text/html");
  document.querySelectorAll("base").forEach((node) => node.remove());
  return { document, snapshot: domSnapshot(document.documentElement, true) };
}

export function renderHtml(text, path, saved, onPosition, onNavigate, onShortcut = () => {}, onChanges = () => {}) {
  const frame = el("iframe", { className: "html-preview", title: path.split("/").pop() });
  frame.setAttribute("sandbox", "allow-scripts allow-popups allow-popups-to-escape-sandbox");
  const { document: documentCopy, snapshot } = htmlSnapshot(text);
  frame.changeSnapshot = snapshot;
  const base = documentCopy.createElement("base");
  base.href = location.origin + "/preview" + (parentOf(path) === "/" ? "" : parentOf(path)) + "/";
  const script = documentCopy.createElement("script");
  documentCopy.head.prepend(base, script);
  const elements = new Map();
  const references = (snapshot?.anchors || []).map(({ node, from, to, edge }) => {
    const element = node.nodeType === 1 ? node : node.parentElement;
    if (!elements.has(element)) {
      elements.set(element, elements.size);
      element.setAttribute("data-ficelo-change", String(elements.get(element)));
    }
    return [elements.get(element), node === element ? null : Array.from(element.childNodes).indexOf(node), from, to, edge];
  });
  script.textContent = "(" + previewBridge.toString() + ")(" + JSON.stringify(saved || {}).replace(/</g, "\\u003c") +
    "," + shortcutFor.toString() + "," + JSON.stringify(references) + "," + domChangeRegions.toString() + "," + changeDirections.toString() + ");";
  let shortcutPort;
  let changeRevision = 0, pendingChanges = null;
  const listen = (event) => {
    if (event.source !== frame.contentWindow || !event.data) return;
    if (event.data.ficelo === "shortcuts" && event.isTrusted && !shortcutPort && event.ports.length === 1) {
      shortcutPort = event.ports[0];
      shortcutPort.onmessage = ({ data }) => {
        if (frame.isConnected && document.activeElement === frame && !document.querySelector("dialog[open]") &&
            data?.keys && data.command === shortcutFor(data.keys)) onShortcut(data.command);
      };
    } else if (event.data.ficelo === "change-directions" && event.data.revision === changeRevision) {
      const directions = event.data.directions;
      if (directions && typeof directions.top === "boolean" && typeof directions.bottom === "boolean") {
        frame.changeDirections = directions;
        onChanges();
      }
    } else if (event.data.ficelo === "position") {
      const { anchor, frac } = event.data;
      if (typeof anchor === "string" && anchor.length < 4096 && Number.isFinite(frac) && frac >= 0 && frac <= 1) onPosition({ anchor, frac });
    } else if (event.data.ficelo === "navigate" && typeof event.data.href === "string") onNavigate(event.data.href);
  };
  window.addEventListener("message", listen);
  let ready = false, pendingEdge = null, active = true;
  frame.setActive = (value) => {
    if (active === value) return;
    active = value;
    if (ready) frame.contentWindow.postMessage({ ficelo: "active", active }, "*");
  };
  frame.setChanges = (changes) => {
    if (changes === pendingChanges) return;
    pendingChanges = changes;
    frame.changeDirections = null;
    changeRevision++;
    if (ready) frame.contentWindow.postMessage({ ficelo: "changes", changes, revision: changeRevision }, "*");
  };
  frame.scrollToEdge = (edge) => {
    if (!["top", "bottom"].includes(edge)) return;
    pendingEdge = edge;
    if (ready) frame.contentWindow.postMessage({ ficelo: "scroll-edge", edge }, "*");
  };
  frame.addEventListener("load", () => {
    ready = true;
    if (!active) frame.contentWindow.postMessage({ ficelo: "active", active }, "*");
    if (pendingEdge) frame.scrollToEdge(pendingEdge);
    if (pendingChanges) frame.contentWindow.postMessage({ ficelo: "changes", changes: pendingChanges, revision: changeRevision }, "*");
  });
  frame.dispose = () => {
    window.removeEventListener("message", listen);
    shortcutPort?.close();
  };
  frame.srcdoc = "<!doctype html>" + documentCopy.documentElement.outerHTML;
  return frame;
}
