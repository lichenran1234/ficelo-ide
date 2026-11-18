/* Reviewing local changes: what differs from the base, and each file's diff.

   The diff itself is Monaco's diff editor. That is not laziness — it brings
   side-by-side and inline layouts, character-level highlighting inside a
   changed line, and collapsible unchanged regions with expand controls,
   all of which would otherwise be a diff algorithm and a fold model to
   write and maintain. */

import { api } from "./api.js";
import { el, store } from "./util.js";
import { CODE_FONT, editorTheme, languageFor } from "./monaco.js";

const LAYOUT_KEY = "ficelo.difflayout";

const STATUS_LABEL = {
  M: "modified",
  A: "added",
  D: "deleted",
  R: "renamed",
  C: "copied",
  "?": "untracked",
};

export function readLayout() {
  return store.get(LAYOUT_KEY, "side") === "inline" ? "inline" : "side";
}

export function writeLayout(value) {
  store.set(LAYOUT_KEY, value);
}

const MAP_KEY = "ficelo.diffmap";

export function readMap() {
  return store.get(MAP_KEY, true) !== false;
}

export function writeMap(on) {
  store.set(MAP_KEY, on === true);
}

/* Changed files grouped by the folder they are in.

   A flat list is fine for three files and unreadable for eighty, where the
   same filename appears in four places and the eye has nothing to hold on
   to. Folders come out in path order with their own counts. */
export function groupByFolder(files) {
  const groups = new Map();
  for (const entry of files) {
    const cut = entry.path.lastIndexOf("/");
    const folder = cut <= 0 ? "" : entry.path.slice(0, cut);
    if (!groups.has(folder)) groups.set(folder, []);
    groups.get(folder).push(entry);
  }
  return Array.from(groups.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([folder, entries]) => ({
      folder,
      entries,
      added: entries.reduce((sum, e) => sum + (e.added || 0), 0),
      removed: entries.reduce((sum, e) => sum + (e.removed || 0), 0),
    }));
}

/* Directories first, recursively, then the files in each directory. Sharing
   this traversal with the document keeps sidebar and section order identical. */
export function fileTree(files) {
  const root = { name: "", path: "", folders: new Map(), files: [] };
  for (const entry of files) {
    const parts = entry.path.split("/");
    let node = root;
    for (const part of parts.slice(0, -1)) {
      if (!node.folders.has(part)) node.folders.set(part, {
        name: part, path: node.path ? node.path + "/" + part : part,
        folders: new Map(), files: [],
      });
      node = node.folders.get(part);
    }
    node.files.push(entry);
  }
  return root;
}

export function orderedFiles(files) {
  const result = [];
  function visit(node) {
    [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name)).forEach(visit);
    result.push(...node.files.slice().sort((a, b) => a.path.localeCompare(b.path)));
  }
  visit(fileTree(files));
  return result;
}

function activate(button, action) {
  button.addEventListener("click", action);
  return button;
}

export class Review {
  constructor(mount, { onOpenDiff, onOpenCommit, onContext, onLoaded, onChanged, onRefreshed, onSelectRepository } = {}) {
    Object.assign(this, { mount, onOpenDiff, onOpenCommit });
    this.onContext = onContext || (() => {});
    this.onLoaded = onLoaded || (() => {});
    this.onChanged = onChanged || (() => {});
    this.onRefreshed = onRefreshed || (() => {});
    this.onSelectRepository = onSelectRepository || (() => {});
    this.scope = "/";
    this.repo = null;
    this.base = null;
    this.tab = "files";
    this.collapsed = new Set();
    this.foldedCommits = new Set();
    this.generation = 0;
    this.searchGeneration = 0;
    this.commits = [];
    this.active = null;
    this.error = null;
    this.search = el("input", { className: "input review-search", placeholder: "Find repository or enter its path", ariaLabel: "Find repository" });
    this.results = el("div", { className: "review-repositories" });
    this.picker = el("div", { className: "review-picker", hidden: true }, [this.search, this.results]);
    this.repoButton = activate(el("button", { className: "review-repo-button", textContent: "Choose repository", ariaExpanded: "false" }), () => this.togglePicker());
    this.dismissPicker = (event) => {
      if (!this.picker.hidden && !this.picker.contains(event.target) && !this.repoButton.contains(event.target)) this.togglePicker(false);
    };
    document.addEventListener("click", this.dismissPicker, true);
    this.navigate = (event) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || !this.mount.getClientRects().length ||
          event.target.closest?.("input,textarea,select,[contenteditable=true],.monaco-editor,.terminal-view,dialog,.menu")) return;
      if (event.key === "Escape" && !this.picker.hidden) { this.togglePicker(false); this.repoButton.focus(); return; }
      if (!["j", "k"].includes(event.key) || !this.picker.hidden) return;
      const rows = [...this.body.querySelectorAll(this.tab === "files" ? ".review-file" : ".review-commit-row .commit")];
      if (!rows.length) return;
      const current = rows.findIndex((row) => row.classList.contains("is-active"));
      const next = Math.max(0, Math.min(rows.length - 1, current + (event.key === "j" ? 1 : -1)));
      event.preventDefault(); rows[next].click(); rows[next].scrollIntoView({ block: "nearest" });
    };
    document.addEventListener("keydown", this.navigate);
    this.body = el("div", { className: "review-body" });
    this.mount.replaceChildren(this.repoButton, this.picker, this.body);
    this.search.addEventListener("input", () => {
      clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => this.searchRepos(), 160);
    });
    this.search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") this.togglePicker(false);
      if (event.key === "Enter") this.searchRepos();
    });
    this.watch = setInterval(() => {
      if (!document.hidden && this.mount.isConnected && this.mount.getClientRects().length && this.status?.repo) this.refresh();
    }, 4000);
  }

  setScope(scope) { this.scope = scope || "/"; }

  snapshot() {
    return { repo: this.repo, base: this.base, tab: this.tab,
      collapsed: [...this.collapsed], foldedCommits: [...this.foldedCommits] };
  }

  restore(state) {
    if (!state || typeof state !== "object") return;
    this.repo = typeof state.repo === "string" ? state.repo : null;
    this.base = typeof state.base === "string" ? state.base : null;
    this.tab = state.tab === "commits" ? "commits" : "files";
    this.collapsed = new Set(Array.isArray(state.collapsed) ? state.collapsed : []);
    this.foldedCommits = new Set(Array.isArray(state.foldedCommits) ? state.foldedCommits : []);
  }

  changed() { this.onChanged(this.snapshot()); }

  togglePicker(open = this.picker.hidden) {
    this.picker.hidden = !open;
    this.repoButton.setAttribute("aria-expanded", String(open));
    this.searchGeneration++;
    clearTimeout(this.searchTimer);
    if (open) {
      this.search.focus();
      this.searchRepos();
    } else {
      this.results.replaceChildren();
    }
  }

  async searchRepos() {
    const generation = ++this.searchGeneration;
    this.results.replaceChildren(el("p", { className: "notice", textContent: "Looking nearby…" }));
    try {
      const result = await api.gitRepositories([this.scope, this.repo].filter(Boolean), this.search.value.trim());
      if (generation !== this.searchGeneration || this.picker.hidden) return;
      this.results.replaceChildren(...result.repositories.map((entry) => activate(el("button", {
        className: "review-repository", title: entry.path,
      }, [el("strong", { textContent: entry.name }), el("span", { textContent: entry.branch || entry.vcs }),
        el("small", { textContent: entry.path })]), () => this.selectRepo(entry.path))));
      if (!result.repositories.length) this.results.append(el("p", { className: "notice", textContent: "No repositories nearby. Enter a repository path to look elsewhere." }));
      if (result.partial) this.results.append(el("p", { className: "notice", textContent: "Search is limited to nearby folders. Enter a path to narrow it." }));
    } catch (error) {
      if (generation === this.searchGeneration && !this.picker.hidden) this.results.replaceChildren(el("p", { className: "notice is-error", textContent: error.message }));
    }
  }

  async selectRepo(repo) {
    this.repo = repo;
    this.base = null;
    this.active = null;
    this.collapsed.clear();
    this.foldedCommits.clear();
    this.togglePicker(false);
    this.changed();
    const loaded = this.load();
    const generation = this.generation;
    await loaded;
    if (generation !== this.generation || this.repo !== repo || !this.status?.repo || this.error) return;
    try { await this.onSelectRepository(repo); }
    catch (error) { this.error = error.message; this.draw(); }
  }

  async load({ quiet = false } = {}) {
    const generation = ++this.generation;
    this.loading = true;
    if (!quiet) { this.error = null; this.status = null; this.draw(); }
    try {
      const status = await api.gitStatus(this.base, this.repo || this.scope);
      if (generation !== this.generation) return;
      const previous = this.status;
      if (quiet && previous?.version === status.version && status.repo) return;
      const history = status.repo ? await api.gitCommits(200, status.root, this.base) : { commits: [] };
      if (generation !== this.generation) return;
      this.status = status;
      this.repo = status.repo ? status.root : this.repo;
      this.commits = history.commits;
      this.error = null;
      if (!quiet || !this.body.querySelector(".review-base-input:focus")) this.draw();
      this.onLoaded(status);
      if (quiet && previous) this.onRefreshed(status);
    } catch (error) {
      if (generation !== this.generation) return;
      this.error = error.message;
      this.draw();
    } finally {
      if (generation === this.generation) this.loading = false;
    }
  }

  refresh() { if (!this.loading) return this.load({ quiet: true }); }

  async follow(tab, section = null) {
    if (!tab || !["diff", "commit"].includes(tab.kind)) return;
    const repo = tab.repo || this.repo || this.scope;
    const base = tab.base || null;
    const sameBase = base === this.base || (!this.base && base === this.status?.base);
    if (repo !== this.repo || !sameBase) {
      this.repo = repo;
      this.base = base;
      await this.load();
      if (repo !== this.repo || base !== this.base) return;
    }
    const path = section || (tab.wholeCommit ? null : tab.path);
    this.tab = tab.rev ? "commits" : "files";
    this.active = { path, rev: tab.rev || null };
    if (tab.rev) this.foldedCommits.delete(tab.rev);
    const entries = tab.rev ? this.commits.find((commit) => commit.sha === tab.rev)?.files : this.status?.files;
    const relative = entries?.find((entry) => entry.request === path || entry.path === path)?.path;
    if (relative) {
      const parts = relative.split("/");
      for (let n = 1; n < parts.length; n++) this.collapsed.delete((tab.rev || "work") + ":" + parts.slice(0, n).join("/"));
    }
    this.draw();
    this.markActive(true);
    this.changed();
  }

  section(path, rev) {
    this.active = { path, rev };
    this.markActive(false);
  }

  markActive(scroll) {
    let selected;
    for (const row of this.body.querySelectorAll("[data-review-path], [data-review-commit]")) {
      const matches = this.active && (row.dataset.reviewCommit === this.active.rev ||
        (row.dataset.reviewPath === this.active.path && (row.dataset.rev || null) === this.active.rev));
      row.classList.toggle("is-active", !!matches);
      if (matches && row.dataset.reviewPath) selected = row;
      else if (matches && !selected) selected = row;
    }
    if (scroll && selected) selected.scrollIntoView({ block: "nearest" });
  }

  fileRow(entry, rev, depth = 0) {
    const label = STATUS_LABEL[entry.status] || entry.status;
    const row = el("button", { className: "change review-file", title: entry.from ? `${entry.from} → ${entry.path}` : entry.path }, [
      el("span", { className: "change-status is-" + label, textContent: entry.status, title: label }),
      el("span", { className: "change-name", textContent: entry.path.split("/").pop() }),
      entry.added == null ? null : el("span", { className: "change-stat" }, [
        el("span", { className: "stat-add", textContent: "+" + entry.added }),
        el("span", { className: "stat-del", textContent: "−" + (entry.removed || 0) }),
      ]),
    ]);
    row.style.paddingLeft = (12 + depth * 12) + "px";
    row.dataset.reviewPath = entry.request;
    row.dataset.rev = rev || "";
    row.addEventListener("click", () => {
      this.active = { path: entry.request, rev: rev || null };
      this.markActive(false);
      const context = { repo: this.repo, base: this.base || this.status?.base };
      if (rev && this.onOpenCommit) this.onOpenCommit(this.commits.find((commit) => commit.sha === rev), entry, context);
      else this.onOpenDiff?.(entry.request, rev, { ...entry, ...context });
    });
    row.addEventListener("contextmenu", (event) => { event.preventDefault(); this.onContext(event, { ...entry, repo: this.repo, base: this.base }, rev); });
    return row;
  }

  treeRows(entries, rev = null) {
    const rows = [];
    const visit = (node, depth) => {
      for (let folder of [...node.folders.values()].sort((a, b) => a.name.localeCompare(b.name))) {
        const key = (rev || "work") + ":" + folder.path;
        let label = folder.name;
        while (!folder.files.length && folder.folders.size === 1) {
          folder = folder.folders.values().next().value;
          label += "/" + folder.name;
        }
        const shut = this.collapsed.has(key);
        const header = activate(el("button", { className: "change-group", ariaExpanded: String(!shut) }, [
          el("span", { className: "twisty", textContent: shut ? "▸" : "▾" }),
          el("span", { className: "group-name", textContent: label, title: folder.path }),
        ]), () => { if (shut) this.collapsed.delete(key); else this.collapsed.add(key); this.draw(); this.changed(); });
        header.style.paddingLeft = (12 + depth * 12) + "px";
        header.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          const first = orderedFilesFromNode(folder)[0];
          const suffix = first.path.slice(folder.path.length);
          this.onContext(event, { path: folder.path, name: folder.name, request: first.request.slice(0, -suffix.length), is_dir: true, repo: this.repo }, rev);
        });
        rows.push(header);
        if (!shut) visit(folder, depth + 1);
      }
      for (const entry of node.files.slice().sort((a, b) => a.path.localeCompare(b.path))) rows.push(this.fileRow(entry, rev, depth));
    };
    visit(fileTree(entries), 0);
    return rows;
  }

  baseControl() {
    const base = el("input", { className: "input review-base-input", value: this.base || this.status?.base || "", ariaLabel: "Compare against revision", title: "Branch, tag or revision to compare against" });
    const apply = () => { const value = base.value.trim() || null; if (value !== this.base) { this.base = value; this.changed(); this.load(); } };
    base.addEventListener("blur", () => { if (base.value === (this.base || this.status?.base || "")) this.draw(); });
    base.addEventListener("keydown", (event) => { if (event.key === "Enter") apply(); });
    const choices = el("datalist", { id: "review-base-choices" }, (this.status?.branches || []).map((name) => el("option", { value: name })));
    base.setAttribute("list", choices.id);
    return el("div", { className: "review-base-control" }, [el("span", { className: "review-base", textContent: "vs " }), base,
      activate(el("button", { className: "button", textContent: "Apply" }), apply), choices]);
  }

  draw() {
    const scroll = this.mount.scrollTop;
    this.repoButton.textContent = this.status?.repo ? (this.status.remote || this.status.name || this.repo) + " ▾" : "Choose repository ▾";
    if (this.error) { this.body.replaceChildren(this.baseControl(), el("p", { className: "notice is-error", textContent: this.error })); return; }
    if (!this.status) { this.body.replaceChildren(el("p", { className: "notice", textContent: "Loading…" })); return; }
    if (!this.status.repo) { this.body.replaceChildren(el("p", { className: "notice", textContent: "Choose a repository to review its changes." })); return; }
    const header = el("div", { className: "review-head" }, [
      el("div", { className: "review-branch", textContent: this.status.branch || "detached" }),
      this.baseControl(),
      el("div", { className: "review-stat" }, [el("span", { textContent: this.status.files.length + " files" }),
        el("span", { className: "stat-add", textContent: "+" + (this.status.added || 0) }),
        el("span", { className: "stat-del", textContent: "−" + (this.status.removed || 0) })]),
    ]);
    if (this.status.worktrees?.length > 1) {
      const select = el("select", { className: "input review-worktrees", ariaLabel: "Worktree" }, this.status.worktrees.map((entry) => el("option", { value: entry.path, textContent: `${entry.folder} · ${entry.branch || "detached"}`, selected: entry.path === this.repo })));
      select.addEventListener("change", () => this.selectRepo(select.value));
      header.append(select);
    }
    const switcher = el("div", { className: "segmented review-switch" }, [["files", "Files"], ["commits", "Commits"]].map(([key, label]) => activate(el("button", { className: "seg", textContent: label, ariaPressed: String(this.tab === key) }), () => { this.tab = key; this.draw(); this.changed(); })));
    const rows = [];
    if (this.tab === "files") {
      rows.push(...this.treeRows(this.status.files));
      if (!rows.length) rows.push(el("p", { className: "notice", textContent: "No changes against the base." }));
      if (this.status.hidden) rows.push(el("p", { className: "notice", textContent: this.status.hidden + " changed file(s) are outside the folder being served." }));
    } else {
      for (const commit of this.commits) {
        const shut = this.foldedCommits.has(commit.sha);
        const fold = activate(el("button", { className: "commit-fold", textContent: shut ? "▸" : "▾", ariaLabel: "Toggle commit files", ariaExpanded: String(!shut) }), () => { if (shut) this.foldedCommits.delete(commit.sha); else this.foldedCommits.add(commit.sha); this.draw(); this.changed(); });
        const open = activate(el("button", { className: "commit", title: commit.subject }, [
          el("span", { className: "commit-sha", textContent: commit.short }),
          el("span", { className: "commit-subject", textContent: commit.subject }),
          el("span", { className: "commit-author", textContent: commit.merge ? "Merge · " + commit.author : commit.author }),
        ]), () => {
          this.active = { rev: commit.sha, path: null }; this.markActive(false);
          this.onOpenCommit?.(commit, null, { repo: this.repo, base: this.base || this.status.base });
        });
        open.dataset.reviewCommit = commit.sha;
        rows.push(el("div", { className: "review-commit-row" }, [fold, open]));
        if (!shut) rows.push(...this.treeRows(commit.files || [], commit.sha));
      }
      if (!rows.length) rows.push(el("p", { className: "notice", textContent: "No commits beyond the selected base." }));
    }
    this.body.replaceChildren(header, switcher, ...rows);
    this.markActive(false);
    this.mount.scrollTop = scroll;
  }

  dispose() {
    this.generation++;
    this.searchGeneration++;
    clearInterval(this.watch);
    document.removeEventListener("click", this.dismissPicker, true);
    document.removeEventListener("keydown", this.navigate);
    clearTimeout(this.searchTimer);
  }
}

function orderedFilesFromNode(node) {
  return [...node.files, ...[...node.folders.values()].flatMap(orderedFilesFromNode)];
}

/* -- the diff editor ------------------------------------------------------ */

export function createDiffEditor(monaco, host, dark) {
  return monaco.editor.createDiffEditor(host, {
    /* Observed rather than told. The diff editor is created while its host
       is still hidden, so it measures zero; a later layout() call does not
       reliably recover, and the result is two 485x5 panes rendering exactly
       one line each — which looks like a working diff whenever the change
       happens to be on line 1. */
    automaticLayout: true,
    readOnly: true,
    originalEditable: false,
    /* renderSideBySide is deliberately not set here: drawDiff applies the
       remembered layout through updateOptions on every draw, so a value
       here would be dead the moment it was read. Verified — mutating it
       changed nothing. */
    /* The expand-context feature, and Monaco's own: unchanged stretches
       collapse to a band that can be clicked to reveal more. Writing this by
       hand means a fold model and a hunk-gap calculation. */
    hideUnchangedRegions: {
      enabled: true,
      contextLineCount: 3,
      minimumLineCount: 4,
      revealLineCount: 20,
    },
    renderOverviewRuler: true,
    minimap: { enabled: readMap() },
    ignoreTrimWhitespace: false,
    theme: editorTheme(dark),
    fontFamily: CODE_FONT,
    scrollBeyondLastLine: false,
  });
}

export function setDiffModel(monaco, editor, tab) {
  const language = languageFor(tab.name);
  const identity = encodeURIComponent(JSON.stringify([tab.repo || "", tab.base || "", tab.rev || "", tab.path, tab.modelScope || ""]));
  const oldUri = monaco.Uri.parse("ficelo-old:/" + identity);
  const newUri = monaco.Uri.parse("ficelo-new:/" + identity);
  const original = monaco.editor.getModel(oldUri) || monaco.editor.createModel(tab.old || "", language, oldUri);
  const modified = monaco.editor.getModel(newUri) || monaco.editor.createModel(tab.new || "", language, newUri);
  if (original.getValue() !== (tab.old || "")) original.setValue(tab.old || "");
  if (!tab.dirty && modified.getValue() !== (tab.new || "")) modified.setValue(tab.new || "");
  editor.setModel({ original, modified });
  return { original, modified };
}
