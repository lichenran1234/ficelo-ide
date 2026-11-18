/* Navigation controllers: the app supplies API calls and file actions. */
import { el, store, parentOf } from "./util.js";

const RECENT_KEY = "ficelo.recent";

/* Folder visits are separate from file tabs and browser URL history. */
export class FolderHistory {
  constructor(path) { this.reset(path); }

  reset(path) {
    this.paths = path ? [path] : [];
    this.index = this.paths.length - 1;
  }

  visit(path) {
    if (this.paths[this.index] === path) return;
    this.paths.splice(this.index + 1);
    this.paths.push(path);
    if (this.paths.length > 100) this.paths.shift();
    this.index = this.paths.length - 1;
  }

  destination(offset) { return this.paths[this.index + offset] ?? null; }

  move(offset) {
    if (this.destination(offset) !== null) this.index += offset;
  }

  retarget(from, to) {
    this.paths = this.paths.map((path) => retargetPath(path, from, to));
  }
}

/* URLs carry physical paths; API requests stay relative to the served root. */
function requestPath(path, root) {
  if (typeof path !== "string" || !path.startsWith("/") || path.split("/").includes("..")) {
    throw new Error("The link must contain an absolute path inside the served folder.");
  }
  root = (root || "/").replace(/\/+$/, "");
  if (path === root) return "/";
  if (root && !path.startsWith(root + "/")) throw new Error("This link points outside the served folder.");
  return path.slice(root.length).replace(/\/+$/, "") || "/";
}

export function legacyRoute(hash) {
  let raw;
  try { raw = decodeURIComponent(hash || ""); } catch (_) { return null; }
  if (raw.startsWith("#/")) return { kind: "file", path: raw.slice(1) };
  if (raw === "#review") return { kind: "review" };
  if (raw.startsWith("#diff/")) return { kind: "diff", path: "/" + raw.slice(6) };
  if (raw.startsWith("#commit/")) {
    const rest = raw.slice(8), cut = rest.indexOf("/");
    if (cut > 0) return { kind: "commit", rev: rest.slice(0, cut), path: rest.slice(cut) };
  }
  return null;
}

export function routeFromUrl(href, root = "/") {
  const url = new URL(href), query = url.searchParams;
  let anchor = url.hash.slice(1);
  try { anchor = decodeURIComponent(anchor); } catch (_) {}
  if (query.has("review")) {
    const repo = requestPath(query.get("review"), root);
    const path = query.has("path") ? requestPath(query.get("path"), root) : null;
    const rev = query.get("sha") || null;
    return { kind: rev ? "commit" : path ? "diff" : "review", repo, path,
      base: query.get("base") || null, rev, wholeCommit: !!rev && !path, anchor };
  }
  if (query.has("path")) return { kind: "path", path: requestPath(query.get("path"), root), anchor };
  return legacyRoute(url.hash);
}

export function urlForRoute(href, route, root = "/", session = null) {
  const url = new URL(href);
  const legacyHash = !url.searchParams.has("path") && !url.searchParams.has("review") && legacyRoute(url.hash);
  const absolute = (path) => ((root || "/").replace(/\/+$/, "") + (path || "/")).replace(/\/+$/, "") || "/";
  for (const key of ["path", "review", "base", "sha"]) url.searchParams.delete(key);
  if (session) url.searchParams.set("session", session);
  if (route?.path && !route.wholeCommit) url.searchParams.set("path", absolute(route.path));
  if (route && ["review", "diff", "commit"].includes(route.kind)) {
    url.searchParams.set("review", absolute(route.repo));
    if (route.base) url.searchParams.set("base", route.base);
    if (route.rev) url.searchParams.set("sha", route.rev);
  }
  if (legacyHash) url.hash = "";
  // Keep directory separators readable without changing reserved characters.
  url.search = [...url.searchParams].map(([key, value]) =>
    encodeURIComponent(key) + "=" + encodeURIComponent(value).replace(/%2F/gi, "/")).join("&");
  return url.href;
}

export function rememberPath(path, now = Date.now()) {
  if (typeof path !== "string" || !path.startsWith("/")) return;
  const recent = store.get(RECENT_KEY, {});
  recent[path] = now;
  const ordered = Object.entries(recent).sort((a, b) => b[1] - a[1]).slice(0, 200);
  store.set(RECENT_KEY, Object.fromEntries(ordered));
}

export function retargetPath(path, from, to) {
  if (path === from) return to;
  return typeof path === "string" && from !== "/" && path.startsWith(from + "/")
    ? to + path.slice(from.length) : path;
}

export function retargetMap(values, from, to) {
  return Object.fromEntries(Object.entries(values || {}).map(([path, value]) => [retargetPath(path, from, to), value]));
}

export function retargetTab(tab, from, to) {
  if (tab.rev) {
    const repo = retargetPath(tab.repo, from, to);
    // A commit's filenames belong to that revision. Only relocating its
    // repository changes the request path used to reach those filenames.
    const path = repo !== tab.repo ? retargetPath(tab.path, tab.repo, repo) : tab.path;
    return { ...tab, repo, path };
  }
  const path = retargetPath(tab.path, from, to);
  return { ...tab, path, ...(tab.repo ? { repo: retargetPath(tab.repo, from, to) } : {}),
    ...(!tab.kind ? { id: path, name: path.split("/").pop() } : {}) };
}

export function retargetWorkspace(record, from, to) {
  const entries = record.tabs ? Object.values(record.tabs) : (record.paths || []).map((path) => ({id: path, path}));
  const changes = (path) => retargetPath(path, from, to) !== path;
  if (!entries.some((tab) => changes(tab.path) || changes(tab.repo)) && !changes(record.root) &&
      !(record.expanded || []).some(changes) && !changes(record.review?.repo) &&
      !changes(record.state?.["ficelo.treeroot"]) &&
      !["ficelo.scroll", "ficelo.mdview"].some((key) => Object.keys(record.state?.[key] || {}).some(changes))) return record;
  const tabs = entries.map((tab) => retargetTab(tab, from, to));
  const ids = new Map(entries.map((tab, index) => [tab.id, tabs[index].id]));
  const state = { ...record.state };
  for (const key of ["ficelo.scroll", "ficelo.mdview"]) {
    if (state[key]) state[key] = retargetMap(state[key], from, to);
  }
  if (state["ficelo.treeroot"]) state["ficelo.treeroot"] = retargetPath(state["ficelo.treeroot"], from, to);
  const updated = { ...record, state, tabs: Object.fromEntries(tabs.map((tab) => [tab.id, tab])),
    order: (record.order || entries.map((tab) => tab.id)).map((id) => ids.get(id) || id),
    active: ids.get(record.active) || record.active,
    root: retargetPath(record.root, from, to),
    expanded: (record.expanded || []).map((path) => retargetPath(path, from, to)) };
  if (record.review?.repo) updated.review = { ...record.review, repo: retargetPath(record.review.repo, from, to) };
  delete updated.paths;
  return updated;
}

export function moveDestination(entry, folder) {
  if (!entry || !folder || entry.path === folder || parentOf(entry.path) === folder) return null;
  if (entry.is_dir && folder.startsWith(entry.path + "/")) return null;
  return (folder === "/" ? "" : folder) + "/" + entry.path.split("/").pop();
}

let nextList = 0;

export class Suggestions {
  constructor(input, mount, { query, onPick, shortcuts = () => [], onError = () => {}, onVisibility = () => {}, submitInput = () => false, place = false }) {
    this.input = input; this.mount = mount; this.query = query; this.onPick = onPick;
    this.shortcuts = shortcuts; this.onError = onError; this.place = place;
    this.onVisibility = onVisibility;
    this.submitInput = submitInput;
    this.generation = 0; this.active = -1; this.rows = [];
    this.id = "suggestions-" + ++nextList;
    mount.id ||= this.id;
    mount.setAttribute("role", "listbox");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", mount.id);
    this.onInput = () => this.schedule();
    this.onFocus = () => this.schedule(0);
    this.onKey = (event) => this.key(event);
    this.onOutside = (event) => { if (event.target !== input && !mount.contains(event.target)) this.hide(); };
    input.addEventListener("input", this.onInput);
    input.addEventListener("focus", this.onFocus);
    input.addEventListener("keydown", this.onKey);
    document.addEventListener("pointerdown", this.onOutside);
  }

  schedule(delay = 120) {
    clearTimeout(this.timer);
    this.navigated = false;
    const generation = ++this.generation;
    this.timer = setTimeout(() => this.run(generation), delay);
  }

  async run(generation = ++this.generation) {
    const query = this.input.value;
    try {
      const result = await this.query(query);
      if (generation !== this.generation || query !== this.input.value) return;
      const supplied = !query.trim() ? this.shortcuts() : [];
      const seen = new Set();
      const previous = this.lastQuery === query ? this.rows[this.active]?.path : null;
      this.rows = [...supplied, ...(result.matches || [])].filter((row) => {
        if (seen.has(row.path)) return false;
        seen.add(row.path); return true;
      });
      this.active = this.rows.length ? Math.max(0, this.rows.findIndex((row) => row.path === previous)) : -1;
      this.lastQuery = query;
      const nodes = this.rows.map((row, index) => {
        const node = el("button", { type: "button", className: "result", title: row.path, id: this.id + "-" + index }, [
          el("span", { className: "result-name", textContent: (row.is_dir ? "📁 " : "") + (row.name || row.path.split("/").pop() || "/") }),
          el("span", { className: "result-path", textContent: row.path }),
        ]);
        node.setAttribute("role", "option");
        node.addEventListener("click", () => this.pick(row));
        return node;
      });
      if (!nodes.length) nodes.push(el("div", { className: "result is-empty", textContent: result.indexing ? "Searching…" : "No matching paths" }));
      else if (result.indexing || result.complete === false) nodes.push(el("div", { className: "result is-empty", textContent: result.indexing ? "Searching deeper folders…" : "This tree is large; some paths were not searched." }));
      this.mount.replaceChildren(...nodes);
      this.mount.hidden = false;
      this.onVisibility(true);
      this.input.setAttribute("aria-expanded", "true");
      this.highlight(this.active);
      if (this.place) {
        const box = this.input.getBoundingClientRect();
        this.mount.style.position = "fixed";
        this.mount.style.left = Math.max(6, Math.min(box.left, innerWidth - 466)) + "px";
        this.mount.style.top = box.bottom + 4 + "px";
        this.mount.style.width = Math.min(460, innerWidth - 12) + "px";
      }
      if (result.indexing) this.timer = setTimeout(() => this.run(generation), 350);
    } catch (error) {
      if (generation !== this.generation) return;
      this.onError(error.message);
      this.hide();
    }
  }

  highlight(index) {
    this.active = index;
    this.mount.querySelectorAll("[role=option]").forEach((node, at) => node.setAttribute("aria-selected", String(at === index)));
    const node = this.mount.querySelectorAll("[role=option]")[index];
    if (node) { this.input.setAttribute("aria-activedescendant", node.id); node.scrollIntoView({ block: "nearest" }); }
    else this.input.removeAttribute("aria-activedescendant");
  }

  key(event) {
    if (event.key === "Escape") { this.hide(); return; }
    if (this.mount.hidden || !this.rows.length || this.lastQuery !== this.input.value) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); event.stopImmediatePropagation();
      this.navigated = true;
      const delta = event.key === "ArrowDown" ? 1 : -1;
      this.highlight((this.active + delta + this.rows.length) % this.rows.length);
    } else if (event.key === "Enter" && this.active >= 0) {
      // A typed path can be submitted directly until an arrow key explicitly
      // chooses a suggestion. Tab still completes the highlighted folder.
      if (!this.navigated && this.submitInput(this.input.value)) return;
      event.preventDefault(); event.stopImmediatePropagation(); this.pick(this.rows[this.active]);
    } else if (event.key === "Tab" && !event.shiftKey && this.active >= 0 && this.rows[this.active].is_dir) {
      event.preventDefault(); event.stopImmediatePropagation();
      this.input.value = this.rows[this.active].path.replace(/\/$/, "") + "/";
      this.schedule(0);
    }
  }

  async pick(row) {
    this.hide();
    try { await this.onPick(row); }
    catch (error) { this.onError(error.message); }
  }

  hide() {
    clearTimeout(this.timer); this.generation += 1;
    this.mount.hidden = true;
    this.onVisibility(false);
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
  }

  dispose() {
    this.hide();
    this.input.removeEventListener("input", this.onInput);
    this.input.removeEventListener("focus", this.onFocus);
    this.input.removeEventListener("keydown", this.onKey);
    document.removeEventListener("pointerdown", this.onOutside);
  }
}

export class ChangeWatcher {
  constructor({ paths, snapshot, onChange, onVisible = () => {}, paused = () => false, interval = 1000 }) {
    this.paths = paths; this.snapshot = snapshot; this.onChange = onChange;
    this.onVisible = onVisible; this.paused = paused; this.interval = interval;
    this.known = new Map(); this.started = false; this.running = false;
    this.visible = () => { if (!document.hidden) this.check(true); };
  }

  prime(path, stamp) {
    this.known.set(path, stamp);
    this.primedDuringCheck?.add(path);
  }

  start() {
    if (this.started) return;
    this.started = true;
    document.addEventListener("visibilitychange", this.visible);
    window.addEventListener("focus", this.visible);
    this.check();
  }

  async check(force = false) {
    if (!this.started) return;
    if (document.hidden || this.paused()) {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.check(force), this.interval);
      return;
    }
    if (this.running) { this.forcePending ||= force; return; }
    this.running = true; clearTimeout(this.timer);
    this.primedDuringCheck = new Set();
    try {
      if (force) await this.onVisible();
      const paths = [...new Set(this.paths())].slice(0, 512);
      const stamps = await this.snapshot(paths);
      const changed = [], gone = [];
      for (const path of paths) {
        if (!Object.prototype.hasOwnProperty.call(stamps, path)) continue;
        if (this.known.has(path) && this.known.get(path) !== stamps[path]) {
          (stamps[path] === null ? gone : changed).push(path);
        }
      }
      if (changed.length || gone.length) await this.onChange({ changed, gone, stamps });
      // A failed refresh must be retried even if the disk stamp stops
      // changing. A successful listing/read may prime a newer baseline.
      for (const path of paths) {
        if (Object.prototype.hasOwnProperty.call(stamps, path) && !this.primedDuringCheck.has(path)) this.known.set(path, stamps[path]);
      }
      if (this.known.size > 1024) this.known = new Map([...this.known].slice(-512));
    } catch (_) {
      // A transient disconnect leaves the baseline intact for the next tick.
    } finally {
      this.running = false;
      this.primedDuringCheck = null;
      if (this.started) {
        const next = this.forcePending; this.forcePending = false;
        this.timer = setTimeout(() => this.check(next), next ? 0 : this.interval);
      }
    }
  }

  stop() {
    this.started = false; clearTimeout(this.timer);
    document.removeEventListener("visibilitychange", this.visible);
    window.removeEventListener("focus", this.visible);
  }
}
