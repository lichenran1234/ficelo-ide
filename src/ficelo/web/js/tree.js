/* The folder tree in the sidebar. */

import { api } from "./api.js";
import { el, ancestors, parentOf } from "./util.js";
import { moveDestination, retargetPath } from "./navigation.js";
import { createFileIcon } from "./file-icons.js";

export class Tree {
  constructor(mount, { onOpenFile, onOpenFolder, onContext, onRootChanged, onExpand, onListing, onMove, onError }) {
    this.mount = mount;
    this.onExpand = onExpand || (() => {});
    this.onMove = onMove || (() => {});
    this.onError = onError || (() => {});
    this.onListing = onListing || (() => {});
    this.writable = false;
    this.dragged = null;
    this.selectedIsDir = false;
    mount.setAttribute("role", "tree");
    mount.addEventListener("contextmenu", (event) => {
      if (event.target.closest(".row")) return;
      event.preventDefault();
      this.onContext(event, { path: this.root, name: this.root.split("/").pop() || "/", is_dir: true });
    });
    mount.addEventListener("keydown", (event) => this.key(event));
    mount.addEventListener("dragover", (event) => {
      if (!event.target.closest(".row") && moveDestination(this.dragged, this.root)) event.preventDefault();
    });
    mount.addEventListener("drop", (event) => {
      if (!event.target.closest(".row")) this.drop(event, this.root);
    });
    this.onOpenFile = onOpenFile;
    this.onOpenFolder = onOpenFolder;
    this.onContext = onContext || (() => {});
    this.onRootChanged = onRootChanged || (() => {});
    this.showHidden = false;
    /* Where the tree starts. Not the served folder necessarily: serving a
       whole filesystem is a real configuration, and starting at / is
       useless there. */
    this.root = "/";
    /* request path -> git status letter, supplied by the review panel so a
       change is visible in the tree without switching modes. */
    this.status = new Map();
    /* Listings are kept once fetched, so collapsing and re-expanding is
       instant and does not re-hit the disk. `refresh` is the way to forget. */
    this.listings = new Map();
    this.stamps = new Map();
    this.expanded = new Set(["/"]);
    this.selected = null;
  }

  async listing(path) {
    if (!this.listings.has(path)) {
      const loading = api.tree(path, this.showHidden).then((body) => {
        // The server takes this stamp before scanning. Keeping that baseline
        // makes a later change visible even if the first watch was delayed.
        if (this.listings.get(path) === loading && body.stamp !== undefined) {
          this.stamps.set(path, body.stamp);
          this.onListing(path, body.stamp);
        }
        return body.entries;
      });
      this.listings.set(path, loading);
    }
    const pending = this.listings.get(path);
    try {
      return await pending;
    } catch (err) {
      /* Do not cache a failure: a folder that was briefly unreadable should
         work on the next click rather than stay broken until reload. */
      if (this.listings.get(path) === pending) this.listings.delete(path);
      throw err;
    }
  }

  refresh(path) {
    if (path === undefined) this.listings.clear();
    else this.listings.delete(path);
  }

  async toggle(path) {
    if (this.expanded.has(path)) this.expanded.delete(path);
    else this.expanded.add(path);
    await this.draw();
    this.onExpand();
  }

  async reveal(path, { widen = true } = {}) {
    /* Revealing something outside the current start point moves the start
       point, rather than silently doing nothing. */
    if (widen && this.root !== "/" && path !== this.root && !path.startsWith(this.root + "/")) {
      this.root = "/";
      this.onRootChanged("/");
    }
    ancestors(path).forEach((step) => this.expanded.add(step));
    this.selected = path;
    await this.draw();
    const row = this.mount.querySelector('[data-path="' + CSS.escape(path) + '"]');
    if (row && row.scrollIntoViewIfNeeded) row.scrollIntoViewIfNeeded();
    else if (row) row.scrollIntoView({ block: "nearest" });
  }

  select(path, isDir = false) {
    this.selected = path;
    this.selectedIsDir = isDir;
    this.mount.querySelectorAll(".row[aria-selected]").forEach((row) =>
      row.removeAttribute("aria-selected")
    );
    const row = this.mount.querySelector('[data-path="' + CSS.escape(path) + '"]');
    if (row) row.setAttribute("aria-selected", "true");
    this.mount.querySelectorAll(".row").forEach((node) => { node.tabIndex = node === row ? 0 : -1; });
  }

  row(entry) {
    const open = entry.is_dir && this.expanded.has(entry.path);
    const node = el("div", {
      className:
        "row " + (entry.is_dir ? "is-dir" : "is-file") + (entry.error ? " is-broken" : ""),
      title: entry.error || entry.path,
    });
    node.dataset.path = entry.path;
    node.setAttribute("role", "treeitem");
    node.tabIndex = entry.path === this.selected ? 0 : -1;
    if (entry.is_dir) node.setAttribute("aria-expanded", String(open));
    if (entry.path === this.selected) node.setAttribute("aria-selected", "true");
    const mark = this.status.get(entry.path);
    const parts = [
      el("span", { className: "twisty", textContent: entry.is_dir ? (open ? "▾" : "▸") : "" }),
      el("span", { className: "glyph file-glyph" }, [createFileIcon(entry.name, entry.is_dir, open)]),
      el("span", { className: "name", textContent: entry.name }),
    ];
    if (mark) {
      parts.push(
        el("span", {
          className: "tree-status is-" + mark.toLowerCase().replace("?", "untracked"),
          textContent: mark,
          title: "git: " + mark,
        })
      );
    }
    /* Appended one at a time rather than as a list with a conditional hole:
       Element.append(null) does not skip, it inserts the text "null", which
       is exactly what appeared on every row. */
    parts.forEach((part) => node.append(part));
    node.addEventListener("click", (event) => {
      if (event.detail > 1) return;
      this.select(entry.path, entry.is_dir);
      // Expanding redraws the row, so the second native click can land on
      // a new element and arrive with detail=1. Keep the folder identity.
      if (entry.is_dir && event.detail && this.lastFolderClick?.path === entry.path &&
          event.timeStamp - this.lastFolderClick.at < 400) {
        this.rootHere(entry.path);
        return;
      }
      this.lastFolderClick = entry.is_dir && event.detail ? { path: entry.path, at: event.timeStamp } : null;
      if (entry.is_dir) this.toggle(entry.path);
      else this.onOpenFile(entry.path);
    });
    if (entry.is_dir) node.addEventListener("dblclick", () => this.rootHere(entry.path));
    node.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      this.onContext(event, entry);
    });
    node.draggable = this.writable;
    node.addEventListener("dragstart", (event) => {
      if (!this.writable) { event.preventDefault(); return; }
      this.dragged = entry;
      event.dataTransfer.setData("application/x-ficelo-path", entry.path);
      event.dataTransfer.setData("text/plain", entry.path);
      event.dataTransfer.effectAllowed = "move";
      node.classList.add("is-dragging");
    });
    node.addEventListener("dragend", () => {
      this.dragged = null;
      this.mount.querySelectorAll(".is-dragging,.is-drop-target").forEach((row) => row.classList.remove("is-dragging", "is-drop-target"));
    });
    if (entry.is_dir) {
      node.addEventListener("dragover", (event) => {
        if (!moveDestination(this.dragged, entry.path)) return;
        event.preventDefault(); event.stopPropagation();
        event.dataTransfer.dropEffect = "move";
        node.classList.add("is-drop-target");
      });
      node.addEventListener("dragleave", () => node.classList.remove("is-drop-target"));
      node.addEventListener("drop", (event) => this.drop(event, entry.path));
    }
    return node;
  }

  creationFolder() {
    return this.selectedIsDir && this.expanded.has(this.selected) &&
      (this.root === "/" || this.selected === this.root || this.selected.startsWith(this.root + "/"))
      ? this.selected : this.root;
  }

  async rootHere(path) {
    this.lastFolderClick = null;
    if (this.onOpenFolder) {
      this.select(path, true);
      await this.onOpenFolder(path);
      return;
    }
    this.root = path; this.expanded.add(path);
    this.select(path, true); this.onRootChanged(path);
    await this.draw();
  }

  async drop(event, folder) {
    const entry = this.dragged;
    if (!this.writable || !moveDestination(entry, folder)) return;
    event.preventDefault(); event.stopPropagation();
    this.dragged = null;
    this.mount.querySelectorAll(".is-dragging,.is-drop-target").forEach((row) => row.classList.remove("is-dragging", "is-drop-target"));
    try { await this.onMove(entry, folder); }
    catch (error) { this.onError(error.message); }
  }

  retarget(from, to) {
    this.root = retargetPath(this.root, from, to);
    this.selected = retargetPath(this.selected, from, to);
    this.expanded = new Set([...this.expanded].map((path) => retargetPath(path, from, to)));
    this.refresh();
  }

  watchedPaths() {
    return [...new Set([this.root, ...this.expanded])].filter((path) =>
      this.root === "/" || path === this.root || path.startsWith(this.root + "/"));
  }

  async refreshVisible(gone = []) {
    for (const path of this.watchedPaths()) this.refresh(path);
    if (gone.length) {
      this.expanded = new Set([...this.expanded].filter((path) =>
        !gone.some((missing) => path === missing || path.startsWith(missing + "/"))));
    }
    await this.draw({ reportErrors: true });
  }

  key(event) {
    const row = event.target.closest(".row");
    if (!row || event.ctrlKey || event.metaKey || event.altKey) return;
    const path = row.dataset.path, isDir = row.classList.contains("is-dir");
    const rows = [...this.mount.querySelectorAll(".row[data-path]")];
    const index = rows.indexOf(row);
    const focus = (target) => {
      if (!target) return;
      this.select(target.dataset.path, target.classList.contains("is-dir")); target.focus();
    };
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); focus(rows[index + (event.key === "ArrowDown" ? 1 : -1)]);
    } else if (event.key === "ArrowRight" && isDir) {
      event.preventDefault();
      if (this.expanded.has(path)) focus(row.parentElement.querySelector("ul .row[data-path]"));
      else this.toggle(path).then(() => focus(this.mount.querySelector('[data-path="' + CSS.escape(path) + '"]')));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (isDir && this.expanded.has(path)) this.toggle(path).then(() => focus(this.mount.querySelector('[data-path="' + CSS.escape(path) + '"]')));
      else focus(this.mount.querySelector('[data-path="' + CSS.escape(parentOf(path)) + '"]'));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (isDir) this.toggle(path); else this.onOpenFile(path);
    } else if (event.key === "F10" && event.shiftKey) {
      event.preventDefault();
      const box = row.getBoundingClientRect();
      this.onContext({ clientX: box.left, clientY: box.bottom }, { path, is_dir: isDir, name: path.split("/").pop() });
    }
  }

  async branch(path, depth, failures = []) {
    const list = el("ul");
    let entries;
    try {
      entries = await this.listing(path);
    } catch (err) {
      failures.push(err);
      list.append(el("li", {}, [el("div", { className: "row is-broken", textContent: err.message })]));
      return list;
    }
    for (const entry of entries) {
      const item = el("li", {}, [this.row(entry)]);
      if (entry.is_dir && this.expanded.has(entry.path)) {
        /* Depth-limited so a symlink loop, or a genuinely enormous tree that
           was restored from a previous session, cannot hang the render. */
        item.append(depth > 0 ? await this.branch(entry.path, depth - 1, failures) : el("ul"));
      }
      list.append(item);
    }
    return list;
  }

  async draw({ reportErrors = false } = {}) {
    /* Draws are asynchronous — each waits on listings — and several can be
       in flight at once: a git-status refresh, a re-root, a toggle. Only the
       most recent may paint. Without this, a slow draw started for the old
       start folder could land after a newer one and put the tree back where
       it was. */
    this.drawing = (this.drawing || 0) + 1;
    const mine = this.drawing;
    const scrollTop = this.mount.scrollTop;
    const failures = [];
    const fresh = await this.branch(this.root, 40, failures);
    if (mine !== this.drawing) {
      if (reportErrors && failures.length) throw failures[0];
      return;
    }
    const focused = this.mount.contains(document.activeElement) ? document.activeElement.closest(".row")?.dataset.path : null;
    this.mount.replaceChildren(...fresh.childNodes);
    this.mount.scrollTop = scrollTop;
    if (focused) this.mount.querySelector('[data-path="' + CSS.escape(focused) + '"]')?.focus({ preventScroll: true });
    if (!this.mount.querySelector('.row[tabindex="0"]')) {
      const first = this.mount.querySelector(".row[data-path]");
      if (first) first.tabIndex = 0;
    }
    if (reportErrors && failures.length) throw failures[0];
  }
}
