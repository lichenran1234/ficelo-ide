/* Wiring. Everything with a name of its own lives in a sibling module; this
   file is the part that knows they exist. */

import { api } from "./api.js";
import { CommitView } from "./commit-view.js";
import { TerminalView } from "./terminal.js";
import { ask, dismissOnBackdrop, copyText as copyToClipboard } from "./dialogs.js";
import { validateHome } from "./home.js";
import { primaryShortcut, shortcutFor } from "./shortcuts.js";
import { PanelLayout } from "./panel-layout.js";
import {
  el,
  store,
  debounce,
  basename,
  ancestors,
  parentOf,
  formatSize,
  interpretPath,
  loadStore,
  refreshStore,
  flushStore,
} from "./util.js";
import {
  loadMonaco,
  languageFor,
  CODE_FONT,
  editorTheme,
  applyTheme as applyEditorTheme,
} from "./monaco.js";
import { renderMarkdown, colouriseCode } from "./markdown.js";
import { readFileBackground, applyReadingBackground, grayColor, backgroundDark } from "./reading-background.js";
import { Tree } from "./tree.js";
import { FolderHistory, Suggestions, ChangeWatcher, rememberPath, retargetPath, retargetMap, retargetTab, retargetWorkspace, moveDestination, legacyRoute, routeFromUrl, urlForRoute } from "./navigation.js";
import { Tabs } from "./tabs.js";
import { pixelAnchors, interpolatePixels, topWithin, makeGuard, anchorsFrom, topForLine } from "./sync.js";
import { changedRanges, mapChanges, changeDirections, domSnapshot, domChangeRegions } from "./changes.js";
import { showMenu, showInfo, closeMenu, wireDismissal } from "./menu.js";
import * as sessions from "./sessions.js";
import { viewerFor, renderViewer, disposeViewer, downloadLink } from "./viewers.js";
import { artifactFor, renderHtml, renderJson, renderLines, htmlSnapshot } from "./artifacts.js";
import { renderDiagrams, setMermaidTheme } from "./mermaid.js";
import {
  Review,
  createDiffEditor,
  setDiffModel,
  readLayout,
  writeLayout,
  readMap,
  writeMap,
} from "./review.js";
import {
  readAppearance,
  writeAppearance,
  applyAppearance,
  resolveTheme,
  readScroll,
  writeScroll,
  buildPanel,
} from "./settings.js";

const HOME_KEY = "ficelo.home";
const TREE_ROOT_KEY = "ficelo.treeroot";
const VIEW_KEY = "ficelo.mdview";
const MAX_REMEMBERED_VIEWS = 200;
let panelLayout = null;

const app = {
  restoring: true,
  config: null,
  monaco: null,
  editor: null,
  models: new Map(), // path -> monaco model
  appearance: null,
  /* A monotonic counter used where a timestamp would do. Ordering is all
     that is needed, and a counter is testable where a clock is not. */
  clock: 1,
  diffEditor: null,
  openRequest: 0,
  workspaceGeneration: 0,
  mode: "files",
};

const dom = {};

/* -- markdown view, remembered per file ---------------------------------

   Per file rather than globally: switching one document to Source should not
   put every other markdown tab into Source on the next reload. */

function readView(path) {
  const views = store.get(VIEW_KEY, {});
  return views[path] || "preview";
}

function writeView(path, view) {
  const views = store.get(VIEW_KEY, {});
  views[path] = view;
  const keys = Object.keys(views);
  if (keys.length > MAX_REMEMBERED_VIEWS) {
    /* Unbounded growth would eventually hit the server's size limit on a
       setting, which is a hard failure for a soft feature. */
    keys.slice(0, keys.length - MAX_REMEMBERED_VIEWS).forEach((key) => delete views[key]);
  }
  store.set(VIEW_KEY, views);
}

/* Canonical links use absolute path/repository query parameters. Legacy
   hash routes remain readable, while ordinary fragments belong to documents. */
function writeLocation() {
  if (!app.config || app.restoring) return;
  const tab = tabs.current;
  const route = tab && tab.kind !== "terminal" ? tab : app.mode === "review" && !tab
    ? { kind: "review", repo: review.repo || tree.root, base: review.base || review.status?.base }
    : { path: tab?.path || tree.root };
  const want = urlForRoute(location.href, route, app.config.root, sessions.currentId());
  // Replacing avoids filling browser history with every tab selection.
  if (location.href !== want) history.replaceState(null, "", want);
}

async function openLocation(route, { widen = false } = {}) {
  if (!route) return;
  if (route.kind === "file" || route.kind === "path") {
    const request = ++app.openRequest;
    let info;
    if (route.kind === "path") {
      try { info = await api.info(route.path); } catch (_) { /* File opening displays the error. */ }
      if (request !== app.openRequest) return;
    }
    if (info?.is_dir) {
      tabs.active = null;
      tabs.draw();
      await rootTreeAt(info.path);
      drawContent();
      return;
    }
    setMode("files");
    await openFile(route.path);
    await tree.reveal(route.path, { widen });
    const tab = tabs.current;
    if (route.anchor && tab?.path === route.path && isMarkdown(tab.path)) {
      const generation = app.previewGeneration;
      await app.previewReady;
      if (!await waitForDiagrams(tab, generation)) return;
      tab.pendingAnchor = route.anchor;
      writeScroll(tab.path, { anchor: route.anchor });
      restoreScroll(tab, "preview");
    }
  } else {
    // A repository link selects its own context, independent of a restored diff.
    if (route.kind === "review") {
      app.openRequest++;
      tabs.active = null;
      tabs.draw();
    }
    if (route.repo) review.restore({ repo: route.repo, base: route.base, tab: route.rev ? "commits" : "files" });
    setMode("review");
    if (route.kind === "review") {
      await review.load();
      drawContent();
    } else if (route.wholeCommit) {
      await openCommit({ sha: route.rev }, null, route);
    } else await openDiff(route.path, route.rev, route);
  }
}

function isMarkdown(path) {
  return /\.(md|markdown|mdown|mkd)$/i.test(path);
}

function previewable(path) {
  return isMarkdown(path) || !!artifactFor(path);
}

function decodePath(value) {
  try { return decodeURIComponent(value); }
  catch (_) { return value; }
}

async function openDocumentLink(href, from) {
  const request = ++app.openRequest;
  let activation = request;
  const workspace = app.workspaceGeneration;
  const origin = tabs.current;
  const current = () => request === app.openRequest && workspace === app.workspaceGeneration && tabs.current === origin;
  try {
    const url = new URL(href, "https://files.invalid" + from);
    if (url.origin !== "https://files.invalid") return;
    const target = decodePath(url.pathname);
    const anchor = url.hash ? decodePath(url.hash.slice(1)) : "";
    if (target !== tabs.active) {
      await api.info(target);
      if (!current()) return;
      const opening = openFile(target);
      const openedRequest = app.openRequest;
      activation = openedRequest;
      await opening;
      if (openedRequest !== app.openRequest || workspace !== app.workspaceGeneration || tabs.current?.path !== target) return;
    }
    const tab = tabs.current;
    if (tab && anchor) {
      if (isMarkdown(tab.path) && !dom.previewHost.hidden) {
        const position = app.previewPosition;
        position.explicit = true;
        position.moved = false;
        position.top = dom.preview.scrollTop;
        await app.previewReady;
        if (!await waitForDiagrams(tab, position.generation) || position.moved ||
            activation !== app.openRequest || workspace !== app.workspaceGeneration) return;
      }
      tab.pendingAnchor = anchor;
      tab.pendingAnchorBehavior = "smooth";
      writeScroll(tab.path, { anchor });
      if (artifactFor(tab.path) === "html") drawContent();
      else restoreScroll(tab, "preview");
    }
  } catch (error) { if (current()) flash("This link could not be opened: " + error.message, true); }
}

/* -- files or review ------------------------------------------------------ */

function setMode(mode) {
  app.mode = mode;
  if (mode !== "files") finder?.hide();
  dom.fileSearch.hidden = mode !== "files";
  dom.tree.hidden = mode !== "files" || !dom.results.hidden;
  dom.review.hidden = mode !== "review";
  document.querySelectorAll("#modeSwitch .seg").forEach((button) => {
    if (button.dataset.mode === mode) button.setAttribute("aria-pressed", "true");
    else button.removeAttribute("aria-pressed");
  });
  if (mode === "review") {
    if (!review.status && !review.error) review.load();
    else if (tabs.current?.kind) review.follow(tabs.current, tabs.current.commitViewState?.active);
  }
  writeLocation();
}

/* -- sessions --------------------------------------------------------------

   Tabs.remember() writes into whichever session is current, so switching is
   a matter of saving the present one and loading another. */

function currentSession() {
  const all = sessions.readAll();
  const id = sessions.currentId();
  return all.find((s) => s.id === id) || null;
}

function saveSession() {
  if (app.restoring) return;
  const id = sessions.currentId() || sessions.newId();
  sessions.setCurrentId(id);
  if (tabs.closedKey !== "ficelo.closed." + id) tabs.setWorkspace(id);
  const existing = currentSession() || { id };
  const savedTabs = tabs.items.filter((tab) => tab.kind !== "terminal");
  const record = { ...existing, name: dom.sessionName.value.trim(),
    tabs: Object.fromEntries(savedTabs.map((tab) => [tab.id, tabRecord(tab)])),
    order: savedTabs.map((tab) => tab.id), active: tabs.current?.kind === "terminal" ? null : tabs.active, mode: app.mode,
    root: tree.root, expanded: [...tree.expanded],
    review: review.snapshot(),
  };
  delete record.paths;
  if (JSON.stringify(record) !== JSON.stringify(existing)) {
    sessions.save(record);
  }
  drawSessionChrome();
}

function markSessionViewed() {
  if (!app.restoring && !document.hidden) sessions.markActive();
}

const markSessionEdited = debounce((id, at) => sessions.markActive(id, at), 250);

function tabRecord(tab) {
  const record = { id: tab.id, path: tab.path, name: tab.name };
  for (const field of ["kind", "wholeCommit", "rev", "repo", "base", "view", "viewState", "commitViewState"]) {
    if (tab[field] !== undefined) record[field] = tab[field];
  }
  return record;
}

async function restoreTabs(record) {
  for (const item of sessions.tabRecords(record)) {
    if (item.kind) await openDiff(item.path, item.rev, item, { focus: false });
    else await openFile(item.path, { focus: false });
    const tab = tabs.items.find((tab) => tab.id === item.id) || tabs.items.find((tab) => tab.path === item.path && tab.kind === item.kind);
    if (tab) {
      if (item.view) tab.view = item.view;
      if (item.viewState) tab.viewState = item.viewState;
    }
  }
  const order = record.order || sessions.tabRecords(record).map((tab) => tab.id);
  tabs.items.sort((a, b) => {
    const first = order.indexOf(a.id), second = order.indexOf(b.id);
    return (first < 0 ? Infinity : first) - (second < 0 ? Infinity : second);
  });
  tabs.active = tabs.find(record.active)?.id || tabs.items[0]?.id || null;
}

function clearTabs() {
  app.openRequest++;
  app.workspaceGeneration++;
  app.editor?.setModel(null);
  app.diffEditor?.setModel(null);
  for (const tab of tabs.items) disposeTabResources(tab);
  tabs.items = []; tabs.active = null; tabs.recentlyClosed = [];
}

function drawSessionChrome() {
  const session = currentSession();
  const starred = !!(session && session.starred);
  dom.starSession.textContent = starred ? "★" : "☆";
  dom.starSession.classList.toggle("is-on", starred);
  sizeSessionName();
  drawPageTitle();
}

function sizeSessionName() {
  const input = dom.sessionName;
  const text = Array.from(input.value).slice(0, 20).join("");
  input.classList.toggle("has-name", !!text);
  if (!text) { input.style.removeProperty("--session-name-width"); return; }
  const style = getComputedStyle(input);
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = style.font;
  const padding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
  const border = parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth);
  input.style.setProperty("--session-name-width", Math.ceil(measure.measureText(text).width + padding + border + 2) + "px");
}

function drawPageTitle() {
  const name = dom.sessionName.value.trim();
  const tab = tabs.current;
  const isFile = tab && tab.kind !== "terminal" && !tab.wholeCommit;
  const folder = tab && !isFile ? tab.path : tree.root;
  const atHome = !tabs.items.length && folder === readHome();
  document.title = name || (atHome ? "Home" : isFile ? basename(tab.path) : absolutePath(folder || "/"));
}

async function loadSession(id, { force = false, remember = true } = {}) {
  id = sessions.canonical(id);
  if (id === sessions.currentId() && !force) return;
  const target = sessions.readAll().find((s) => s.id === id);
  if (!target) return;
  if (tabs.items.some((tab) => tab.dirty) && !await ask("Switch session", "Discard unsaved edits and switch to this session?", { accept: "Switch", danger: true })) return;
  if (remember) { rememberScroll(); saveSession(); await flushStore(); }
  treeNavigation++;
  app.restoring = true;
  updateFolderButtons();
  try {
    clearTabs(); sessions.setCurrentId(id); tabs.setWorkspace(id);
    dom.sessionName.value = target.name || "";
    tree.root = await usableTreeRoot(target.root || readHome());
    tree.expanded = new Set(target.expanded || [tree.root]);
    await tree.draw();
    review.setScope(tree.root); review.restore(target.review);
    await review.load();
    await restoreTabs(target);
    setMode(target.mode || "files");
    tabs.draw(); drawContent(); drawSessionChrome();
  } finally { app.restoring = false; resetFolderHistory(); }
  sessions.markActive(id);
  writeLocation();
  flash("Opened " + sessions.describe(target));
}

function toggleStar(id) {
  if (!id) {
    saveSession();
    id = sessions.currentId();
  }
  sessions.writeAll(
    sessions.readAll().map((s) => (s.id === id ? Object.assign({}, s, { starred: !s.starred }) : s))
  );
  drawSessionChrome();
}

/* Rebuilt in place rather than closed and reopened, so starring or
   forgetting one does not dismiss the list mid-tidy. */
function redrawSessions() {
  sessions.buildList(dom.sessionList, sessions.readAll(), sessions.currentId(), {
    onPick: (id) => { dom.sessionList.hidden = true; loadSession(id); },
    onStar: (id) => { toggleStar(id); redrawSessions(); },
    onDelete: (id) => { sessions.remove(id); redrawSessions(); },
    onRefresh: redrawSessions,
    onMerge: mergeSessions,
    onAdd: addSessionTabs,
    onMenu: (event, session) => showMenu(event.clientX, event.clientY, [
      { label: "Rename…", run: async () => {
        const name = await ask("Session name", "Leave empty for a name based on its tabs.", { value: session.name || "", accept: "Save" });
        if (name === null) return;
        sessions.save({ ...session, name: name.trim() });
        if (session.id === sessions.currentId()) dom.sessionName.value = name.trim();
        redrawSessions(); drawSessionChrome();
      } },
      { label: "Set group…", run: async () => {
        const group = await ask("Session group", "Use the same group to keep related sessions together.", { value: session.group || "", accept: "Save" });
        if (group !== null) { sessions.save({ ...session, group: group.trim() }); redrawSessions(); }
      } },
      { label: "Arrange tabs…", run: () => arrangeSession(session) },
      { label: "Copy session link", run: () => copyText(sessions.link(session.id)) },
      { label: "Copy link to a separate copy", run: () => copyText(sessions.link(session.id, true)) },
    ]),
  });
}

async function addSessionTabs(id) {
  saveSession();
  const source = sessions.readAll().find((record) => record.id === id);
  if (!source) return;
  const additions = sessions.missingTabs(currentSession() || {}, source);
  const generation = app.workspaceGeneration;
  const previous = tabs.active;
  for (const item of additions) {
    if (generation !== app.workspaceGeneration) return;
    if (item.kind) await openDiff(item.path, item.rev, item, { focus: false });
    else await openFile(item.path, { focus: false });
    if (generation !== app.workspaceGeneration) return;
    const tab = tabs.find(item.id) || tabs.items.find((tab) => tab.path === item.path && tab.kind === item.kind);
    if (tab) {
      if (item.view) tab.view = item.view;
      if (item.viewState) tab.viewState = item.viewState;
      if (item.commitViewState) tab.commitViewState = item.commitViewState;
    }
  }
  // Existing reading positions and view choices always win conflicts.
  for (const key of ["ficelo.scroll", "ficelo.mdview"]) {
    store.set(key, { ...(source.state?.[key] || {}), ...store.get(key, {}) });
  }
  rememberScroll();
  tabs.active = tabs.find(previous)?.id || tabs.items[0]?.id || null;
  saveSession(); tabs.draw(); drawContent(); redrawSessions();
  flash("Added " + additions.length + " tabs");
}

async function arrangeSession(session) {
  const entries = sessions.tabRecords(session);
  const dialog = el("dialog", { className: "app-dialog" });
  const list = el("div", { className: "arrange-list" });
  const draw = () => list.replaceChildren(...entries.map((tab, index) => {
    const row = el("div", { className: "arrange-row" }, [el("span", { textContent: tab.name || tab.path, title: tab.path })]);
    for (const [delta, label] of [[-1, "↑"], [1, "↓"]]) {
      const button = el("button", { textContent: label, disabled: index + delta < 0 || index + delta >= entries.length });
      button.addEventListener("click", () => { [entries[index], entries[index + delta]] = [entries[index + delta], entries[index]]; draw(); });
      row.append(button);
    }
    return row;
  }));
  const cancel = el("button", { textContent: "Cancel" });
  cancel.onclick = () => dialog.remove();
  const done = el("button", { textContent: "Save order" });
  done.onclick = () => {
    sessions.save({ ...session, order: entries.map((tab) => tab.id) });
    if (session.id === sessions.currentId()) {
      tabs.items.sort((a, b) => entries.findIndex((t) => t.id === a.id) - entries.findIndex((t) => t.id === b.id));
      tabs.draw();
    }
    dialog.remove(); redrawSessions();
  };
  dialog.append(el("h2", { textContent: "Arrange tabs" }), list, el("div", { className: "dialog-actions" }, [cancel, done]));
  dialog.addEventListener("cancel", () => dialog.remove());
  dismissOnBackdrop(dialog, () => dialog.remove());
  document.body.append(dialog); draw(); dialog.showModal();
}

async function mergeSessions(ids) {
  const previous = sessions.currentId();
  if (ids.includes(previous) && tabs.items.some((tab) => tab.dirty)) {
    flash("Save your edits before merging the current session.", true); return;
  }
  saveSession();
  ids.forEach((id) => sessions.migrate(id));
  await flushStore();
  let snapshot;
  try { snapshot = await refreshStore(); }
  catch (error) { flash(error.message, true); return; }
  const chosen = ids.map((id) => snapshot["ficelo.workspace." + id]).filter(Boolean);
  const count = new Set(chosen.flatMap((s) => sessions.tabRecords(s).map((t) => t.id))).size;
  if (chosen.length < 2 || !await ask("Merge sessions", "Combine " + chosen.map(sessions.describe).join(", ") + " into " + count + " tabs? The first session keeps its name and conflicting positions. A recovery backup is saved.", { accept: "Merge" })) return;
  try {
    const response = await fetch("/api/workspaces/merge", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected: Object.fromEntries(chosen.map((s) => [s.id, s])) }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    await loadStore();
    if (ids.includes(previous)) await loadSession(result.id, { force: true, remember: false });
    redrawSessions(); flash("Merged sessions; backup: " + result.backup);
  } catch (error) { flash(error.message, true); }
}

async function startNewSession() {
  if (tabs.items.length && !await ask("New session", tabs.items.some((tab) => tab.dirty)
    ? "Save this set of tabs and start a new session? Unsaved edits will be discarded."
    : "Save this set of tabs and start a new session?", { accept: "Start new", danger: tabs.items.some((tab) => tab.dirty) })) return;
  rememberScroll(); saveSession(); await flushStore();
  clearTabs(); sessions.setCurrentId(sessions.newId()); tabs.setWorkspace(sessions.currentId());
  resetFolderHistory();
  dom.sessionName.value = "";
  await tree.draw();
  saveSession(); tabs.draw(); drawContent();
  writeLocation();
  flash("Started a new session");
}

/* -- the breadcrumb -------------------------------------------------------

   Clickable, because the commonest thing to want after opening a deep file
   is the folder two levels up. Shows the folder, not the file: the file's
   own name is already on its tab. */
/* With a file open the breadcrumb shows that file's folder; with none, the
   folder the tree starts at — "~" at startup. It used to fall back to "/"
   regardless, so the page opened showing ~/ in the tree and / above it. */
function drawCurrentCrumbs() {
  writeLocation();
  drawPageTitle();
  const tab = tabs.current;
  if (tab) {
    drawCrumbs(tab.kind === "terminal" || tab.wholeCommit ? tab.path.replace(/\/$/, "") + "/" : tab.path);
    return;
  }
  /* A trailing slash marks it as a folder, so drawCrumbs keeps the last
     part instead of treating it as a file name to drop. */
  drawCrumbs(tree.root === "/" ? "/" : tree.root + "/");
}

function drawCrumbs(path) {
  /* The full path, always, from "/": the same shape as the address of the
     folder on disk, with nothing abbreviated. The folder the tree starts at
     is bold; folders below it (the open file's, when it is deeper) are
     lighter. Clicking a segment starts the tree there. */
  const parts = (path || "/").split("/").filter(Boolean);
  const folder = path && !path.endsWith("/") ? parts.slice(0, -1) : parts;
  const rootParts = tree.root.split("/").filter(Boolean);
  const rootLabel = app.config && app.config.root_name !== "/" ? app.config.root_name : "/";
  const nodes = [];
  const add = (label, target, depth) => {
    const kind =
      depth === rootParts.length && target === (tree.root.replace(/\/+$/, "") || "/")
        ? " is-root"
        : depth > rootParts.length
        ? " is-below"
        : "";
    const button = el("button", {
      className: "crumb" + kind + (depth === folder.length ? " is-last" : ""),
      textContent: label,
      title: target,
    });
    button.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey) {
        // An unused session link starts empty instead of restoring this tab's files.
        const url = urlForRoute(sessions.link(sessions.newId()), { kind: "path", path: target }, app.config.root);
        window.open(url, "_blank", "noopener,noreferrer");
      } else rootTreeAt(target);
    });
    button.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      const items = pathCopyItems(target, { isDir: true });
      if (target !== tree.root) items.push("-", explorerRootItem(target));
      showMenu(event.clientX, event.clientY, items);
    });
    /* No separator straight after a leading "/", which is a separator
       itself: "/ data / users", not "/ / data / users". A narrower served
       root starts with a folder name, which does need one. */
    if (nodes.length && !(nodes.length === 1 && rootLabel === "/")) {
      nodes.push(el("span", { className: "crumb-sep", textContent: "/" }));
    }
    nodes.push(button);
  };
  add(rootLabel, "/", 0);
  let at = "";
  folder.forEach((part, index) => {
    at += "/" + part;
    add(part, at, index + 1);
  });
  dom.crumbs.replaceChildren(...nodes);
}

/* Home chooses the explorer's starting folder independently of the server's
   fixed filesystem root. It defaults to the account's home when that folder
   is inside the served tree. */
function readHome() {
  const saved = store.get(HOME_KEY, null);
  if (typeof saved === "string" && saved.startsWith("/")) return saved;
  /* The server's idea of home, which is the user's home folder when it is
     inside the served tree and "/" when it is not. */
  return (app.config && app.config.home) || "/";
}

let treeNavigation = 0;
const folderHistory = new FolderHistory();
let folderHistoryPending = null;

function updateFolderButtons() {
  const busy = app.restoring || folderHistoryPending !== null;
  document.getElementById("folderBack").disabled = busy || folderHistory.destination(-1) === null;
  document.getElementById("folderForward").disabled = busy || folderHistory.destination(1) === null;
}

function resetFolderHistory() {
  treeNavigation++;
  folderHistory.reset(tree.root);
  folderHistoryPending = null;
  updateFolderButtons();
}

function rememberFolder(path) {
  if (!app.restoring) folderHistory.visit(path);
  updateFolderButtons();
}

async function goFolderHistory(offset) {
  const path = folderHistory.destination(offset);
  if (path === null || folderHistoryPending || app.restoring) return;
  const pending = {};
  folderHistoryPending = pending;
  updateFolderButtons();
  try {
    if (await rootTreeAt(path, true, { addToHistory: false })) folderHistory.move(offset);
  } catch (error) { flash(error.message, true); }
  finally {
    if (folderHistoryPending === pending) folderHistoryPending = null;
    updateFolderButtons();
  }
}

async function rootTreeAt(path, remember = true, { reportErrors = false, addToHistory = true } = {}) {
  const navigation = ++treeNavigation;
  // Validate again: a previously visited folder may have been removed.
  tree.refresh();
  try { await tree.listing(path); }
  catch (error) { if (navigation !== treeNavigation) return false; throw error; }
  if (navigation !== treeNavigation) return false;
  const previousRoot = tree.root;
  const previousExpanded = new Set(tree.expanded);
  tree.root = path;
  tree.expanded.add(path);
  try { await tree.draw({ reportErrors }); }
  catch (error) {
    if (navigation !== treeNavigation) return false;
    if (tree.root === path) {
      tree.root = previousRoot;
      tree.expanded = previousExpanded;
      tree.refresh();
      await tree.draw();
      if (navigation !== treeNavigation) return false;
      drawCurrentCrumbs();
    }
    throw error;
  }
  if (navigation !== treeNavigation || tree.root !== path) return false;
  if (addToHistory) rememberFolder(path);
  setMode("files");
  /* Remembered, so the tree comes back where it was left rather than
     resetting to Home on every reload. Home stays the place the button
     goes, which is a different question from where you are now. */
  if (remember) { store.set(TREE_ROOT_KEY, path); rememberPath(path); }
  review.setScope(path);
  drawCurrentCrumbs();
  saveSession();
  return true;
}

function goHome() {
  rootTreeAt(readHome()).catch((error) => flash(error.message, true));
}

/* -- theme ---------------------------------------------------------------- */

function applyTheme(choice) {
  const effective = resolveTheme(choice);
  const changed = document.documentElement.dataset.theme !== effective;
  app.appearance.theme = effective;
  document.documentElement.dataset.theme = effective;
  writeAppearance(app.appearance);
  if (changed && app.diffEditor) {
    app.diffEditor.updateOptions({ theme: editorTheme(effective === "dark") });
  }
  const tab = tabs.current;
  if (changed) tab?.commitView?.setOptions({ dark: effective === "dark" });
  updateReadingBackground();
  const dark = previewIsDark();
  setMermaidTheme(dark);
  if (tab && !dom.previewHost.hidden && app.diagramDark !== dark) {
    app.diagramDark = dark;
    const cache = tab.previewCache;
    if (cache) cache.ready = false;
    const generation = app.previewGeneration;
    app.diagramRender = renderDiagrams(dom.preview, dark);
    app.diagramRender.then(() => {
      if (tabs.current !== tab || generation !== app.previewGeneration) return;
      if (cache) { cache.dark = dark; cache.ready = true; }
      if (inSplit()) mdSync.force(syncPreviewToEditor);
      updateActiveHeading();
    });
  }
}

function updateReadingBackground() {
  const tab = tabs.current;
  const theme = document.documentElement.dataset.theme;
  const ordinary = tab && !tab.kind && app.config && theme === "light";
  const override = ordinary ? readFileBackground(absolutePath(tab.path)) : null;
  const previewColor = ordinary && isMarkdown(tab.path)
    ? override || grayColor(app.appearance.mdBackground) : null;
  const sourceColor = ordinary ? override || grayColor(app.appearance.codeBackground) : null;
  applyReadingBackground(dom.preview, previewColor);
  applyEditorTheme(theme, sourceColor);
}

function previewIsDark() {
  return document.documentElement.dataset.theme === "dark" || backgroundDark(dom.preview.dataset.fileBackground);
}

/* -- models -------------------------------------------------------------- */

function modelFor(tab) {
  if (!app.models.has(tab.path)) {
    // A move can keep this model and its undo history under a new path.
    // Unique identities also let the old filename be opened again later.
    const uri = app.monaco.Uri.from({scheme: "ficelo", path: tab.path, query: "model=" + app.clock++});
    const model = app.monaco.editor.createModel(tab.text || "", languageFor(tab.name), uri);
    model.onDidChangeContent(() => {
      tab.dirty = model.getValue() !== tab.saved;
      if (tab.dirty && !app.restoring) markSessionEdited(sessions.currentId(), Date.now());
      tabs.draw();
      scheduleReloadControl();
      if (!tab.dirty) autoReloadTab(tab);
      if (isMarkdown(tab.path) && tab.view !== "edit") schedulePreview(tab);
    });
    app.models.set(tab.path, model);
  }
  return app.models.get(tab.path);
}

function disposeModel(path) {
  const model = app.models.get(path);
  if (model) {
    model.dispose();
    app.models.delete(path);
  }
}

/* -- rendering the active tab -------------------------------------------- */

const schedulePreview = debounce((tab) => {
  if (tabs.active !== tab.id) return;
  drawPreview(tab);
}, 150);

function drawPreview(tab) {
  const changingFile = app.previewPosition?.tab !== tab;
  const generation = app.previewGeneration = (app.previewGeneration || 0) + 1;
  const position = app.previewPosition = { tab, generation, moved: false, top: null };
  const model = app.models.get(tab.path);
  const source = model ? model.getValue() : tab.text || "";
  const cached = tab.previewCache;
  if (cached?.ready && cached.source === source && cached.path === tab.path && cached.dark === previewIsDark()) {
    if (app.previewCache !== cached) dom.markdownContent.replaceChildren(...cached.nodes);
    app.previewCache = cached;
    app.diagramDark = cached.dark;
    app.diagramRender = app.previewReady = Promise.resolve();
    dom.preview.headings = cached.headings;
    drawOutline();
    dom.preview.scrollTo({ top: cached.top, left: cached.left, behavior: "instant" });
    for (const [node, top, left] of cached.scrolls || []) node.scrollTo({ top, left, behavior: "instant" });
    position.top = dom.preview.scrollTop;
    tab.previewScrollTop = position.top;
    tab.positionReady = true;
    if (tab.pendingAnchor) restoreScroll(tab, "preview", position);
    updateActiveHeading();
    return cached.view === tab.view;
  }
  tab.positionReady = false;
  renderMarkdown(source, window.marked, dom.markdownContent);
  const cache = app.previewCache = tab.previewCache = {
    source, path: tab.path, dark: previewIsDark(), nodes: [...dom.markdownContent.childNodes],
    headings: dom.markdownContent.headings, ready: false, top: 0, left: 0, view: tab.view,
  };
  // The preview is reused across files. Start a new document at the top;
  // restoreScroll will apply its saved position or requested heading later.
  if (changingFile) dom.preview.scrollTop = 0;
  dom.preview.headings = dom.markdownContent.headings;
  dom.preview.querySelectorAll("img[src]").forEach((image) => {
    const raw = image.getAttribute("src");
    if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(raw)) return;
    const url = new URL(raw, "https://files.invalid" + tab.path);
    image.src = "/preview" + decodePath(url.pathname).split("/").map(encodeURIComponent).join("/") + url.search + url.hash;
  });
  dom.preview.querySelectorAll("a[href]").forEach((link) => {
    const href = link.getAttribute("href");
    if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(href)) {
      link.target = "_blank"; link.rel = "noopener noreferrer";
    } else link.addEventListener("click", (event) => {
      if (event.button) return;
      event.preventDefault(); openDocumentLink(href, tab.path);
    });
  });
  /* Diagrams first: rendering replaces the whole <pre>, so colouring it
     beforehand would be wasted and would briefly show the source. */
  app.diagramDark = previewIsDark();
  app.diagramRender = renderDiagrams(dom.preview, app.diagramDark);
  /* Put the reader at its saved position promptly. Later diagrams may change
     layout, but must not pull a reader back after they have started scrolling. */
  if (!inSplit()) restoreScroll(tab, "preview", position);
  app.previewReady = Promise.resolve(dom.markdownContent.mathReady).then(async () => {
    if (await waitForDiagrams(tab, generation)) return colouriseCode(dom.preview);
  });
  app.previewReady.then(() => {
    if (tabs.current !== tab || generation !== app.previewGeneration) return;
    cache.ready = true;
    if (inSplit()) mdSync.force(syncPreviewToEditor);
    else if (!position.moved && !position.explicit) restoreScroll(tab, "preview", position);
    scheduleReloadControl();
  });
  drawOutline();
  return false;
}

function disposeTabPreview(tab) {
  if (tab.previewCache && app.previewCache === tab.previewCache) {
    dom.markdownContent.replaceChildren();
    app.previewCache = null;
    app.previewPosition = null;
    app.previewGeneration++;
  }
  delete tab.previewCache;
}

async function waitForDiagrams(tab, generation) {
  let diagrams;
  do {
    diagrams = app.diagramRender;
    await diagrams;
    if (generation !== app.previewGeneration || tabs.current !== tab) return false;
  } while (diagrams !== app.diagramRender);
  return true;
}

function drawOutline() {
  const headings = outlineHeadings();
  const shallowest = Math.min(...headings.map((heading) => heading.depth));
  dom.toc.replaceChildren(
    ...headings.map((heading, index) => {
      const link = el("button", {
        className: "toc-item depth-" + Math.min(heading.depth - shallowest + 1, 4),
        textContent: heading.text,
        title: heading.text,
      });
      link.dataset.heading = heading.slug;
      link.dataset.headingIndex = String(index);
      link.addEventListener("click", () => {
        if (app.previewPosition) app.previewPosition.moved = true;
        const target = dom.preview.querySelectorAll("h1,h2,h3,h4")[index];
        if (target) dom.preview.scrollTo({ top: Math.max(0, topWithin(dom.preview, target) - 8), behavior: "smooth" });
      });
      return link;
    })
  );
  /* Also hidden in split, where the preview is already the second of two
     columns and there is no room for a third. drawOutline runs after
     setView, so this has to be decided here or it is immediately undone. */
  updateOutlineVisibility(); updateActiveHeading();
}

function outlineHeadings() {
  return (dom.preview.headings || []).filter((heading) => heading.depth <= 4);
}

function updateOutlineVisibility() {
  const available = outlineHeadings().length >= 2 && !inSplit();
  const hidden = store.get("ficelo.toc", {}).hidden === true;
  dom.tocPane.hidden = !available || hidden;
  updateRightPanelToggle();
}

function updateRightPanelToggle() {
  const tab = tabs.current;
  const minimap = !!tab && !tab.kind && !dom.editorHost.hidden && !inSplit();
  const contents = !!tab && !tab.kind && isMarkdown(tab.path) && !dom.previewHost.hidden &&
    !inSplit() && outlineHeadings().length >= 2;
  const expanded = minimap ? app.appearance?.minimap !== false : !dom.tocPane.hidden;
  const button = document.getElementById("tocToggle");
  button.hidden = !minimap && !contents;
  button.dataset.panel = minimap ? "minimap" : "contents";
  button.querySelector("span").textContent = minimap ? "Minimap" : "Contents";
  button.setAttribute("aria-controls", minimap ? "editorHost" : "tocPane");
  button.setAttribute("aria-pressed", String(expanded));
  button.setAttribute("aria-expanded", String(expanded));
  button.title = (expanded ? "Hide " : "Show ") + (minimap ? "minimap" : "contents");
  panelLayout?.refresh();
}

function updateActiveHeading() {
  if (dom.previewHost.hidden) return;
  const top = dom.preview.getBoundingClientRect().top + 12;
  const headings = [...dom.preview.querySelectorAll("h1[id],h2[id],h3[id],h4[id]")];
  const bottom = dom.preview.scrollTop + dom.preview.clientHeight >= dom.preview.scrollHeight - 2;
  const current = bottom ? headings[headings.length - 1]
    : headings.filter((node) => node.getBoundingClientRect().top <= top).pop() || headings[0];
  const currentIndex = String(headings.indexOf(current));
  let active = null;
  dom.toc.querySelectorAll(".toc-item").forEach((button) => {
    if (button.dataset.headingIndex === currentIndex) { button.setAttribute("aria-current", "location"); active = button; }
    else button.removeAttribute("aria-current");
  });
  if (active && !dom.tocPane.hidden) {
    const pane = dom.tocPane.getBoundingClientRect(), row = active.getBoundingClientRect();
    if (row.top < pane.top) dom.tocPane.scrollTop += row.top - pane.top;
    else if (row.bottom > pane.bottom) dom.tocPane.scrollTop += row.bottom - pane.bottom;
  }
}

const mdSync = makeGuard();

/* Editor on the left, rendered document on the right, kept in step. */
function showSplit() {
  document.querySelector(".stage").classList.add("md-split");
  dom.editorHost.hidden = false;
  dom.previewHost.hidden = false;
  showViewer(false);
  dom.diffHost.hidden = true;
  updateRightPanelToggle();
  dom.notice.hidden = true;
  if (app.editor) app.editor.layout();
}

function syncPreviewToEditor() {
  const model = app.editor && app.editor.getModel();
  if (!model) return;
  const anchors = pixelAnchors(dom.preview, app.editor);
  dom.preview.scrollTop = interpolatePixels(anchors, app.editor.getScrollTop());
}

function syncEditorToPreview() {
  const model = app.editor && app.editor.getModel();
  if (!model) return;
  const anchors = pixelAnchors(dom.preview, app.editor);
  app.editor.setScrollTop(interpolatePixels(anchors, dom.preview.scrollTop, "preview", "editor"));
}

function wireSplitSync() {
  if (app.splitWired) return;
  app.splitWired = true;
  app.editor.onDidScrollChange(() => {
    if (!inSplit()) return;
    mdSync.run(syncPreviewToEditor);
  });
  dom.preview.addEventListener("scroll", () => {
    if (!inSplit()) return;
    mdSync.run(syncEditorToPreview);
  });
}

function inSplit() {
  const tab = tabs.current;
  return !!tab && !tab.kind && isMarkdown(tab.path) && tab.view === "split";
}

function showOnly(which) {
  document.querySelector(".stage").classList.remove("md-split");
  dom.editorHost.hidden = which !== "editor";
  dom.previewHost.hidden = which !== "preview";
  showViewer(which === "viewer");
  dom.diffHost.hidden = which !== "diff";
  dom.notice.hidden = which !== "notice";
  updateRightPanelToggle();
  if (which === "editor" && app.editor) app.editor.layout();
}

/* Saved before the view changes, restored after it is on screen. Kept on the
   tab as well as in storage so switching tabs within a session does not have
   to go through the settings store. */
function rememberScroll() {
  const tab = tabs.current;
  if (!tab) return;
  if (!dom.previewHost.hidden && tab.previewCache === app.previewCache && tab.previewCache) {
    Object.assign(tab.previewCache, {
      top: dom.preview.scrollTop, left: dom.preview.scrollLeft, view: tab.view,
      scrolls: [...dom.markdownContent.querySelectorAll("*")]
        .filter((node) => node.scrollTop || node.scrollLeft)
        .map((node) => [node, node.scrollTop, node.scrollLeft]),
    });
  }
  if (tab.wholeCommit && tab.commitView) { tab.commitViewState = tab.commitView.snapshot(); return; }
  if (!dom.diffHost.hidden && app.diffEditor?.getModel()) { tab.viewState = app.diffEditor.saveViewState(); return; }
  if (!dom.editorHost.hidden && app.editor && app.editor.getModel()) {
    tab.viewState = app.editor.saveViewState();
    writeScroll(tab.path, { top: app.editor.getScrollTop() });
  } else if (!dom.previewHost.hidden && tab.positionReady) {
    if (tab.previewScrollTop === dom.preview.scrollTop) return;
    tab.previewScrollTop = dom.preview.scrollTop;
    const offset = dom.preview.getBoundingClientRect().top;
    const headings = Array.from(dom.preview.querySelectorAll("h1[id],h2[id],h3[id],h4[id],h5[id],h6[id]"));
    const heading = headings.filter((node) => node.getBoundingClientRect().top <= offset + 12).pop();
    const height = dom.preview.scrollHeight - dom.preview.clientHeight;
    writeScroll(tab.path, { top: dom.preview.scrollTop, anchor: heading ? heading.id : null,
      frac: height > 0 ? dom.preview.scrollTop / height : 0 });
  } else if (!dom.viewerHost.hidden) {
    const scroller = dom.viewerHost.querySelector(".view-scroll");
    if (scroller) writeScroll(tab.path, { top: scroller.scrollTop });
  }
}

function restoreScroll(tab, where, position = null) {
  // These hosts have already been shown and laid out. Restore before paint,
  // so another tab's position never appears for a frame in the shared editor.
  if (where !== "preview") {
    const saved = readScroll(tab.path);
    if (where === "editor" && app.editor) {
      if (tab.viewState) app.editor.restoreViewState(tab.viewState);
      else app.editor.setScrollTop(saved?.top || 0, app.monaco.editor.ScrollType.Immediate);
    } else if (where === "viewer") {
      const scroller = dom.viewerHost.querySelector(".view-scroll");
      if (scroller && saved) scroller.scrollTop = saved.top || 0;
    }
    return;
  }
  const generation = app.previewGeneration;
  /* After layout, or the target is beyond a content height that is still
     zero and the scroll is silently clamped back to the top. */
  requestAnimationFrame(() => {
    if (tabs.current !== tab || (where === "preview" && generation !== app.previewGeneration) || position?.moved || position?.explicit) return;
    const saved = readScroll(tab.path);
    if (where === "preview") {
      const anchor = tab.pendingAnchor || (saved && saved.anchor);
      const heading = anchor && dom.preview.querySelector("[id='" + CSS.escape(anchor) + "']");
      if (heading) dom.preview.scrollTo({ top: Math.max(0, topWithin(dom.preview, heading) - 8), behavior: tab.pendingAnchorBehavior || "auto" });
      else if (saved) dom.preview.scrollTop = Number.isFinite(saved.frac)
        ? saved.frac * (dom.preview.scrollHeight - dom.preview.clientHeight) : saved.top || 0;
      delete tab.pendingAnchor;
      delete tab.pendingAnchorBehavior;
      tab.previewScrollTop = dom.preview.scrollTop;
      if (position) position.top = dom.preview.scrollTop;
      tab.positionReady = true;
      updateActiveHeading();
    }
  });
}

const FILE_ACTIONS = [
  { id: "scrollFileTop", setting: "showScrollTopButton", edge: "top" },
  { id: "reloadFile", setting: "showReloadButton" },
  { id: "scrollFileBottom", setting: "showScrollBottomButton", edge: "bottom" },
];

let reloadControlFrame = null;
function scheduleReloadControl() {
  if (reloadControlFrame !== null) return;
  reloadControlFrame = requestAnimationFrame(() => { reloadControlFrame = null; updateReloadControl(); });
}

/* A separate read locates changes, but never updates a model, its stamp, or
   the content on screen. Save/reload/move and newer observations supersede it. */
async function readChangedContent(tab) {
  if (!tab.diskChanged || app.appearance?.manualRefresh === false) return;
  const observation = tab.diskObservation, path = tab.path, baseline = tab.stamp;
  const previous = tab.changedContent;
  if (previous?.path === path && previous.baseline === baseline &&
      (previous.observation === observation || previous.body?.stamp === observation) &&
      (!previous.error || Date.now() < previous.retryAt)) return;
  const request = tab.changedContent = { path, baseline, observation };
  tab.changeRead = request;
  const workspace = app.workspaceGeneration;
  try {
    const body = tab.kind ? await api.gitDiff(path, tab.rev, tab.base, tab.repo) : await api.read(path);
    if (tab.changeRead !== request || tab.changedContent !== request || tab.path !== path || tab.stamp !== baseline ||
        workspace !== app.workspaceGeneration || !tabs.items.includes(tab) || !tab.diskChanged) return;
    request.body = body;
  } catch (_) {
    request.error = true;
    request.retryAt = Date.now() + 1000;
  }
  if (tabs.current === tab) scheduleReloadControl();
}

function contentSnapshot(tab, text, type) {
  if (type === "lines") return { tokens: text.split("\n") };
  if (type === "html") return htmlSnapshot(text).snapshot;
  let node;
  if (type === "json" || type === "jsonl") node = renderJson(text, type === "jsonl");
  else if (type === "log" || type === "patch") node = renderLines(text, type === "patch");
  else {
    const host = document.createElement("div");
    renderViewer(type, { path: tab.path, name: tab.name, text }, host, window.marked);
    node = host.firstElementChild;
  }
  return domSnapshot(node);
}

function analyseChanges(tab, body, type, node, model, original = false) {
  const baseline = tab.kind ? (original ? tab.old : tab.new) : tab.saved ?? tab.text;
  const latest = tab.kind ? (original ? body.old : body.new) : body.text;
  if (typeof baseline !== "string" || typeof latest !== "string") return null;
  const version = model?.getVersionId();
  const slot = original ? "originalChanges" : "viewChanges";
  const cached = tab[slot];
  if (cached?.body === body && cached.type === type && cached.node === node && cached.version === version) return cached;
  const displayed = model?.getValue() ?? baseline;
  const snapshot = type === "lines" ? contentSnapshot(tab, displayed, type)
    : type === "html" ? node.changeSnapshot : domSnapshot(node);
  const before = displayed === baseline && type === "lines" ? snapshot : contentSnapshot(tab, baseline, type);
  const after = contentSnapshot(tab, latest, type);
  if (!snapshot || !before || !after) return null;
  let ranges = changedRanges(before.tokens, after.tokens).map((range) => ({ ...range,
    weight: Math.max(range.end - range.start, range.newEnd - range.newStart, 1) }));
  if (snapshot !== before) ranges = mapChanges(ranges, changedRanges(before.tokens, snapshot.tokens));
  return tab[slot] = { body, type, node, version, snapshot, ranges, changed: baseline !== latest };
}

function editorChangeRegions(editor, ranges) {
  const count = editor.getModel().getLineCount(), scroll = editor.getScrollTop();
  return ranges.map(({ start, end, weight }) => {
    const top = start >= count ? editor.getBottomForLineNumber(count)
      : editor.getTopForLineNumber(Math.floor(start) + 1);
    return { start: top - scroll, end: end === start ? top - scroll
      : editor.getBottomForLineNumber(Math.min(count, Math.max(1, Math.ceil(end)))) - scroll, weight };
  });
}

function reloadDirections(tab) {
  readChangedContent(tab);
  const content = tab.changedContent;
  if (!content?.body) return content?.error ? { top: false, bottom: true } : {};
  const body = content.body;
  if (body.binary || body.truncated || tab.binary || tab.truncated) return { top: false, bottom: true };
  if (!dom.diffHost.hidden && app.diffEditor?.getModel()) {
    const regions = [];
    let changed = false, height = 0;
    for (const original of [true, false]) {
      const editor = original ? app.diffEditor.getOriginalEditor() : app.diffEditor.getModifiedEditor();
      const data = analyseChanges(tab, body, "lines", editor, editor.getModel(), original);
      if (!data) continue;
      changed ||= data.changed;
      height = editor.getLayoutInfo().height;
      regions.push(...editorChangeRegions(editor, data.ranges));
    }
    return changeDirections(regions, height, changed);
  }
  const model = app.models.get(tab.path);
  if (!dom.editorHost.hidden) {
    const data = analyseChanges(tab, body, "lines", app.editor, model);
    return data ? changeDirections(editorChangeRegions(app.editor, data.ranges),
      app.editor.getLayoutInfo().height, data.changed) : { top: false, bottom: true };
  }
  if (!dom.previewHost.hidden) {
    const data = analyseChanges(tab, body, "lines", dom.preview, model);
    if (!data) return { top: false, bottom: true };
    const anchors = anchorsFrom(dom.preview, data.snapshot.tokens.length + 1);
    const regions = data.ranges.map(({ start, end, weight }) => ({
      start: topForLine(anchors, start + 1) - dom.preview.scrollTop,
      end: topForLine(anchors, end + 1) - dom.preview.scrollTop, weight,
    }));
    return changeDirections(regions, dom.preview.clientHeight, data.changed);
  }
  const type = artifactFor(tab.path) || viewerFor(tab.path), node = dom.viewerHost.firstElementChild;
  if (!node || !["html", "json", "jsonl", "log", "patch", "csv", "notebook"].includes(type)) return { top: false, bottom: true };
  const data = analyseChanges(tab, body, type, node, model);
  if (!data) return { top: false, bottom: true };
  if (type === "html") {
    if (!data.ranges.length) return { top: false, bottom: data.changed };
    node.setChanges(data.ranges);
    return node.changeDirections || {};
  }
  return changeDirections(domChangeRegions(data.snapshot.anchors, data.ranges, node.getBoundingClientRect().top),
    node.clientHeight, data.changed);
}

function updateReloadControl() {
  const tab = tabs.current;
  const group = document.getElementById("fileActions");
  const stale = !!tab?.diskChanged && app.appearance?.manualRefresh !== false;
  const directions = stale ? reloadDirections(tab) : {};
  for (const { id, setting, edge } of FILE_ACTIONS) {
    const button = document.getElementById(id);
    button.hidden = !tab || tab.kind === "terminal" || app.appearance?.[setting] === false;
    button.classList.toggle("is-stale", stale && (!edge || !!directions[edge]));
    const label = edge ? (stale ? "Reload and go to " : "Go to ") + edge : "Reload file";
    button.setAttribute("aria-label", label);
    button.title = edge ? label : stale
      ? "This file changed on disk. Reload to see the latest content  Alt+R"
      : "Reload this file from disk  Alt+R";
  }
  group.hidden = !group.querySelector("button:not([hidden])");
  const outlineWidth = dom.previewHost.hidden || dom.tocPane.hidden ? 0 : dom.tocPane.offsetWidth;
  const available = Math.max(4, group.parentElement.clientWidth - 48);
  group.style.right = Math.min(16 + outlineWidth, available) + "px";
}

let scrollRequest = 0;
async function scrollFileTo(edge) {
  const tab = tabs.current;
  if (!tab || tab.kind === "terminal") return;
  const request = ++scrollRequest, activation = app.openRequest;
  if (tab.diskChanged && app.appearance.manualRefresh !== false && !await reloadTab(tab)) return;
  if (tabs.current !== tab || activation !== app.openRequest) return;
  const view = tab.view;
  const position = app.previewPosition;
  const preview = !dom.previewHost.hidden;
  if (preview) {
    if (position?.tab === tab) position.explicit = true;
    await app.previewReady;
  }
  if (!dom.diffHost.hidden) await app.diffEditor?.waitForDiff();
  // Let queued view restoration finish before this explicit navigation.
  await new Promise(requestAnimationFrame);
  if (tabs.current !== tab || tab.view !== view || request !== scrollRequest || activation !== app.openRequest ||
      (preview && position !== app.previewPosition)) return;
  const bottom = edge === "bottom";
  const scrollEditor = (editor) => editor?.setScrollTop(bottom ? editor.getScrollHeight() : 0, app.monaco.editor.ScrollType.Immediate);
  mdSync.force(() => {
    if (!dom.editorHost.hidden) scrollEditor(app.editor);
    if (!dom.diffHost.hidden) {
      scrollEditor(app.diffEditor?.getOriginalEditor());
      scrollEditor(app.diffEditor?.getModifiedEditor());
    }
    if (preview) {
      dom.preview.scrollTop = bottom ? dom.preview.scrollHeight : 0;
      tab.positionReady = true;
      position.top = dom.preview.scrollTop;
      updateActiveHeading();
    }
    if (!dom.viewerHost.hidden) {
      const scroller = dom.viewerHost.querySelector(".view-scroll");
      if (tab.wholeCommit) tab.commitView?.scrollToEdge(edge);
      else if (scroller) scroller.scrollTop = bottom ? scroller.scrollHeight : 0;
      dom.viewerHost.querySelector("iframe.html-preview")?.scrollToEdge?.(edge);
    }
  });
  rememberScroll();
}

/* Keep each viewer mounted: removing an iframe/embed, or collapsing its
   layout, resets its document and native scroll state. Only the active host
   has the public ID; inactive hosts are hidden, inert, and keep their size. */
function activateViewer(tab) {
  app.emptyViewerHost ||= dom.viewerHost;
  if (!tab.viewerHost) {
    tab.viewerHost = el("div", { className: "viewer-host", hidden: true, inert: true });
    app.emptyViewerHost.after(tab.viewerHost);
    app.reloadLayout?.observe(tab.viewerHost);
  }
  if (dom.viewerHost !== tab.viewerHost) {
    showViewer(false);
    dom.viewerHost.removeAttribute("id");
    dom.viewerHost = tab.viewerHost;
    dom.viewerHost.id = "viewerHost";
  }
}

function showViewer(visible) {
  const host = dom.viewerHost;
  if (!visible && !host.hidden) host.querySelectorAll("audio,video").forEach((player) => player.pause());
  host.hidden = !visible;
  host.inert = !visible;
  host.querySelector("iframe.html-preview")?.setActive?.(visible);
}

function disposeTabViewer(tab) {
  const host = tab.viewerHost;
  host?.querySelector("iframe.html-preview")?.dispose?.();
  disposeViewer(tab);
  if (!host) return;
  app.reloadLayout?.unobserve(host);
  if (dom.viewerHost === host) {
    host.removeAttribute("id");
    dom.viewerHost = app.emptyViewerHost;
    dom.viewerHost.id = "viewerHost";
    showViewer(false);
  }
  host.remove();
  delete tab.viewerHost;
  delete tab.viewerKey;
  delete tab.viewerText;
}

function drawContent() {
  const tab = tabs.current;
  updateReadingBackground();
  document.getElementById("tocToggle").hidden = true;
  scheduleReloadControl();
  if (dom.viewerHost !== tab?.viewerHost) showViewer(false);
  if (!tab?.kind || tab.kind === "terminal") app.followedReviewTab = null;
  drawCurrentCrumbs();
  dom.diffLayout.hidden = !(tab && tab.kind);

  if (!tab) {
    drawWelcome();
    showOnly("notice");
    return;
  }
  if (tab.error) {
    dom.notice.textContent = tab.error;
    dom.notice.className = "notice is-error";
    showOnly("notice");
    return;
  }
  if (tab.kind === "terminal") {
    dom.viewToggle.hidden = true; dom.diffLayout.hidden = true;
    activateViewer(tab);
    if (dom.viewerHost.firstElementChild !== tab.terminal.element) dom.viewerHost.replaceChildren(tab.terminal.element);
    showOnly("viewer"); tab.terminal.focus();
    dom.sbKind.textContent = "Terminal"; dom.sbSize.textContent = "";
    return;
  }
  if (tab.kind) {
    dom.viewToggle.hidden = true;
    if (tab.wholeCommit) drawCommit(tab); else drawDiff(tab);
    if (app.mode === "review" && app.followedReviewTab !== tab.id) {
      app.followedReviewTab = tab.id;
      review.follow(tab, tab.commitViewState?.active);
    }
    drawStatus(tab);
    return;
  }

  /* A viewer is chosen from the name, not from whether the bytes decoded:
     a .png is binary and perfectly displayable, a .csv is text and still
     better as a table than as lines. */
  const kind = viewerFor(tab.path);
  if (kind) {
    dom.viewToggle.hidden = true;
    const key = JSON.stringify([kind, tab.path, tab.stamp]);
    const retained = tab.viewerKey === key;
    if (!retained) disposeTabViewer(tab);
    activateViewer(tab);
    if (!retained) {
      renderViewer(kind, tab, dom.viewerHost, window.marked);
      tab.viewerKey = key;
    }
    showOnly("viewer");
    if (!retained) restoreScroll(tab, "viewer");
    drawStatus(tab);
    return;
  }

  const artifact = artifactFor(tab.path);
  if (artifact && tab.view !== "edit") {
    const model = app.models.get(tab.path);
    const text = model ? model.getValue() : tab.text || "";
    const key = JSON.stringify([artifact, tab.path, tab.stamp]);
    if (tab.viewerKey === key && tab.viewerText === text && !tab.pendingAnchor) {
      activateViewer(tab);
      dom.viewToggle.hidden = false;
      showOnly("viewer"); drawViewToggle(tab); drawStatus(tab);
      return;
    }
    disposeTabViewer(tab);
    activateViewer(tab);
    const host = dom.viewerHost;
    const active = () => tabs.current === tab && dom.viewerHost === host && !host.hidden;
    let node;
    if (artifact === "html") {
      const position = Object.assign({}, readScroll(tab.path) || {});
      if (tab.pendingAnchor) { position.anchor = tab.pendingAnchor; delete position.frac; delete tab.pendingAnchor; }
      node = renderHtml(text, tab.path, position,
        (saved) => { if (active()) writeScroll(tab.path, saved); },
        (href) => { if (active()) openDocumentLink(href, tab.path); },
        (command) => { if (active()) handleShortcut(command); },
        () => { if (active()) scheduleReloadControl(); });
    } else if (artifact === "json" || artifact === "jsonl") node = renderJson(text, artifact === "jsonl");
    else node = renderLines(text, artifact === "patch");
    dom.viewerHost.replaceChildren(node);
    tab.viewerKey = key;
    tab.viewerText = text;
    dom.viewToggle.hidden = false;
    showOnly("viewer");
    restoreScroll(tab, "viewer");
    drawViewToggle(tab);
    drawStatus(tab);
    return;
  }

  if (tab.binary) {
    dom.notice.replaceChildren(el("p", { textContent: "This looks like a binary file." }), downloadLink(tab));
    dom.notice.className = "notice";
    showOnly("notice");
    return;
  }

  const markdown = isMarkdown(tab.path);
  dom.viewToggle.hidden = !previewable(tab.path);
  if (markdown && tab.view === "split") {
    mdSync.force(() => {
      app.editor.setModel(modelFor(tab));
      app.editor.updateOptions({
        readOnly: !app.config.writable || Boolean(tab.truncated),
        /* Off in split regardless of the setting: the pane is half width and
           the minimap eats a fifth of what is left. */
        minimap: { enabled: false },
      });
      showSplit();
      restoreScroll(tab, "editor");
      const retained = drawPreview(tab);
      wireSplitSync();
      if (!retained) syncPreviewToEditor();
    });
  } else if (markdown && tab.view !== "edit") {
    showOnly("preview");
    drawPreview(tab);
  } else {
    app.editor.setModel(modelFor(tab));
    app.editor.updateOptions({
      readOnly: !app.config.writable || Boolean(tab.truncated),
      minimap: { enabled: app.appearance.minimap !== false },
      fontSize: app.appearance.codeFontSize,
    });
    showOnly("editor");
    restoreScroll(tab, "editor");
  }
  drawViewToggle(tab);
  drawStatus(tab);
}

const SHORTCUTS = [
  ["Find a file, folder or path", primaryShortcut("P") + " · " + primaryShortcut("K")],
  ["Show or hide the tree", primaryShortcut("B")],
  ["Save", primaryShortcut("S") + "  ·  Alt+S"],
  ["Reload from disk", "Alt+R"],
  ["Close tab", "Alt+W"],
  ["Reopen closed tab", "Alt+Shift+W"],
  ["Previous / next tab", "Alt+←  Alt+→  ·  Alt+[  Alt+]"],
  ["Move tab left / right", "Alt+Shift+←  Alt+Shift+→"],
  ["Go to tab N", "Alt+1…9"],
];

function drawWelcome() {
  dom.notice.className = "notice";
  dom.notice.replaceChildren(
    el("div", { className: "welcome" }, [
      el("h2", { textContent: "ficelo" }),
      el("p", {
        textContent:
          "Open a file from the tree, or find a file, folder or path in Explorer.",
      }),
      el(
        "dl",
        { className: "keys" },
        SHORTCUTS.flatMap(([what, keys]) => [
          el("dt", { textContent: what }),
          el("dd", { textContent: keys }),
        ])
      ),
    ])
  );
}

function drawViewToggle(tab) {
  const current = previewable(tab.path) ? tab.view || "preview" : "edit";
  dom.viewToggle.replaceChildren(
    ...[
      ["preview", "Preview"],
      ["split", "Split"],
      ["edit", "Source"],
    ].filter(([value]) => value !== "split" || isMarkdown(tab.path)).map(([value, label]) => {
      const button = el("button", { className: "seg", textContent: label });
      button.dataset.view = value;
      if (value === current) button.setAttribute("aria-pressed", "true");
      button.addEventListener("click", () => setView(tab, value));
      return button;
    })
  );
}

/* The bottom bar: what is open, what kind of thing it is, how big.

   Down there rather than in the top bar because the top bar is for acting
   and this is for looking, and because the full path is long. */
const KINDS = [
  [/\.(md|markdown|mdown|mkd)$/i, "Markdown"],
  [/\.(py)$/i, "Python"],
  [/\.(js|mjs|cjs)$/i, "JavaScript"],
  [/\.(ts|tsx)$/i, "TypeScript"],
  [/\.(json)$/i, "JSON"],
  [/\.(ya?ml)$/i, "YAML"],
  [/\.(sh|bash|zsh)$/i, "Shell"],
  [/\.(css)$/i, "CSS"],
  [/\.(html?)$/i, "HTML"],
  [/\.(csv|tsv)$/i, "Table"],
  [/\.(ipynb)$/i, "Notebook"],
  [/\.(png|jpe?g|gif|webp|bmp|ico|avif|svg)$/i, "Image"],
  [/\.(pdf)$/i, "PDF"],
];

function kindOf(path) {
  for (const [pattern, name] of KINDS) {
    if (pattern.test(path)) return name;
  }
  return "Text";
}

function drawStatus(tab) {
  const root = (app.config.root || "").replace(/\/+$/, "");
  if (!tab) {
    dom.sbDir.textContent = root;
    dom.sbName.textContent = "";
    dom.sbPath.title = root;
    dom.sbKind.textContent = "";
    dom.sbSize.textContent = "";
    return;
  }
  const full = root + tab.path;
  const cut = full.lastIndexOf("/");
  dom.sbDir.textContent = full.slice(0, cut + 1);
  dom.sbName.textContent = full.slice(cut + 1);
  dom.sbPath.title = full;

  if (tab.wholeCommit) {
    dom.sbKind.textContent = "Commit " + tab.rev.slice(0, 7);
    dom.sbSize.textContent = (tab.commitData?.files?.length || 0) + " files";
    return;
  }
  if (tab.kind) {
    dom.sbKind.textContent = tab.rev
      ? "Diff · commit " + tab.rev.slice(0, 7)
      : "Diff · working tree vs base";
    const bits = [];
    if (tab.old === null) bits.push("added");
    else if (tab.new === null) bits.push("deleted");
    dom.sbSize.textContent = bits.join(" · ");
    return;
  }

  const view = isMarkdown(tab.path) ? " (" + (tab.view || "preview") + ")" : "";
  dom.sbKind.textContent = kindOf(tab.path) + view + (tab.truncated ? " · truncated" : "");
  dom.sbSize.textContent = tab.size === undefined ? "" : formatSize(tab.size);
}

async function setView(tab, view) {
  rememberScroll();
  if (view === "preview" && tab.dirty && app.config.writable) await save();
  tab.view = view;
  writeView(tab.path, view);
  drawContent();
}

function drawDiff(tab) {
  const header = reviewPathHeader(tab);
  if (tab.binary || tab.truncated) {
    dom.notice.replaceChildren(header, el("p", { textContent: tab.notice || (tab.binary ? "Binary file changed." : "This file is too large to display completely.") }));
    dom.notice.className = "notice"; showOnly("notice"); return;
  }
  if (!app.diffEditor) {
    app.diffPathHeader = el("div");
    const canvas = el("div", { className: "review-diff-canvas" });
    dom.diffHost.replaceChildren(el("div", { className: "review-diff-shell" }, [app.diffPathHeader, canvas]));
    app.diffEditor = createDiffEditor(app.monaco, canvas, document.documentElement.dataset.theme === "dark");
    for (const editor of [app.diffEditor.getOriginalEditor(), app.diffEditor.getModifiedEditor()]) {
      editor.onDidScrollChange(scheduleReloadControl);
      editor.onDidLayoutChange(scheduleReloadControl);
    }
  }
  app.diffPathHeader.replaceChildren(header);

  const models = setDiffModel(app.monaco, app.diffEditor, tab);
  tab.diffModels = models;
  app.diffEditor.updateOptions({
    renderSideBySide: readLayout() === "side",
    minimap: { enabled: readMap() },
    renderOverviewRuler: readMap(),
    fontSize: app.appearance?.diffFontSize || 13,
    /* Only the right-hand side, and only for a working-tree diff: there is
       no file on disk corresponding to "this commit's version". */
    readOnly: !app.config.writable || !tab.editable,
    originalEditable: false,
  });
  if (tab.watchedModel !== models.modified) {
    tab.diffSubscription?.dispose();
    tab.watchedModel = models.modified;
    tab.diffSubscription = models.modified.onDidChangeContent(() => {
      tab.dirty = models.modified.getValue() !== tab.new;
      if (tab.dirty && !app.restoring) markSessionEdited(sessions.currentId(), Date.now());
      tabs.draw();
      scheduleReloadControl();
      if (!tab.dirty) autoReloadTab(tab);
    });
  }
  tab.model = models.modified;
  showOnly("diff");
  app.diffEditor.layout();
  if (tab.viewState) {
    const wanted = tab.viewState;
    app.diffEditor.restoreViewState(wanted);
    const settled = app.diffEditor.onDidUpdateDiff(() => {
      settled.dispose();
      if (tabs.current === tab && tab.viewState === wanted) app.diffEditor.restoreViewState(wanted);
    });
  }
}

function drawCommit(tab) {
  activateViewer(tab);
  if (!tab.commitView) {
    tab.commitHost = el("div", { className: "view-scroll" });
    tab.commitView = new CommitView(app.monaco, tab.commitHost, {
      dark: document.documentElement.dataset.theme === "dark",
      onSection: (path, rev) => { if (tabs.current === tab) review.section(path, rev); },
      onChanged: (state) => { tab.commitViewState = state; saveSessionSoon(); },
      onCopyPath: (entry) => copyText(entry.path, "the path in the repository"),
    });
    tab.commitView.show(tab);
  }
  tab.commitView.setOptions({ layout: readLayout(), map: readMap(), fontSize: app.appearance?.diffFontSize || 13,
    dark: document.documentElement.dataset.theme === "dark" });
  if (dom.viewerHost.firstElementChild !== tab.commitHost) dom.viewerHost.replaceChildren(tab.commitHost);
  showOnly("viewer");
  tab.commitView.resume();
}

const saveSessionSoon = debounce(saveSession, 250);

async function openCommit(commit, entry, context = {}, { focus = true } = {}) {
  if (!commit?.sha) return;
  const request = focus ? ++app.openRequest : null;
  const generation = app.workspaceGeneration;
  if (focus) rememberScroll();
  const repo = context.repo || review.repo || tree.root;
  const key = context.id || "commit:" + JSON.stringify([repo, commit.sha]);
  let tab = tabs.find(key);
  if (!tab) {
    app.commitLoads ||= new Map();
    const loadKey = generation + ":" + key;
    let pending = app.commitLoads.get(loadKey);
    if (!pending) {
      pending = api.gitCommit(commit.sha, repo, true).then((commitData) => ({ commitData }))
        .catch((error) => ({ error: error.message }));
      app.commitLoads.set(loadKey, pending);
    }
    const result = await pending;
    if (app.commitLoads.get(loadKey) === pending) app.commitLoads.delete(loadKey);
    if (generation !== app.workspaceGeneration) return;
    tab = tabs.find(key) || tabs.add(key, { kind: "commit", wholeCommit: true, path: repo, repo, base: context.base || null,
      rev: commit.sha, name: result.commitData?.commit.subject || commit.subject || commit.sha.slice(0, 7),
      commitViewState: context.commitViewState, ...result, loaded: true });
  }
  const activate = focus && request === app.openRequest;
  if (activate) tabs.active = tab.id;
  saveSession(); tabs.draw();
  if (activate) {
    drawContent();
    if (entry) tab.commitView?.jump(entry.request || entry.path);
    else if (tab.commitHost) tab.commitHost.scrollTop = 0;
  }
}

async function openDiff(path, rev, entry, { focus = true } = {}) {
  if (entry?.wholeCommit) return openCommit({ sha: rev, subject: entry.name }, null, entry, { focus });
  const request = focus ? ++app.openRequest : null;
  const generation = app.workspaceGeneration;
  if (focus) rememberScroll();
  let tab = entry?.id ? tabs.find(entry.id) : null;
  let body;
  if (!tab) {
    try { body = await api.gitDiff(path, rev, entry?.base, entry?.repo || path); }
    catch (err) { body = { repo: entry?.repo || path, base: entry?.base || null, error: err.message }; }
    if (generation !== app.workspaceGeneration) return;
    const key = entry?.id || "diff:" + JSON.stringify([body.repo, body.base, rev || "work", path]);
    tab = tabs.find(key) || tabs.items.find((candidate) => !entry?.id && candidate.kind && !candidate.wholeCommit &&
      candidate.repo === body.repo && candidate.base === body.base && candidate.rev === (rev || null) && candidate.path === path);
    if (!tab) {
      tab = tabs.add(key, { kind: rev ? "commit" : "diff", path, repo: body.repo, base: body.base,
        rev: rev || null, name: path.split("/").pop(), status: entry?.status || null, ...body, loaded: true });
      if (!rev && body.stamp !== undefined) watcher?.prime(path, body.stamp);
    }
  }
  const activate = focus && request === app.openRequest;
  if (activate) tabs.active = tab.id;
  saveSession(); tabs.draw();
  if (activate) drawContent();
}

async function openTerminal(path = currentFolder()) {
  if (!app.config.terminal) { flash("Start ficelo with --terminal in read-write mode to open a shell."); return; }
  const existing = tabs.items.find((tab) => tab.kind === "terminal");
  if (existing) {
    const next = tabs.current === existing
      ? [...tabs.focused].reverse().find((id) => id !== existing.id && tabs.find(id)) || null : existing.id;
    selectTab(next);
    return;
  }
  app.openRequest++;
  rememberScroll();
  const id = "terminal:" + sessions.newId();
  const terminal = new TerminalView(path);
  const tab = tabs.add(id, { path, kind: "terminal", name: "Terminal · " + basename(path), terminal });
  tabs.active = id; tabs.draw(); drawContent();
  try { await terminal.start(); }
  catch (error) { tab.error = error.message; if (tabs.current === tab) drawContent(); }
}

/* -- opening and closing -------------------------------------------------- */

async function openFile(path, { focus = true } = {}) {
  const request = focus ? ++app.openRequest : null;
  const alreadyActive = tabs.current?.id === path && tabs.current.loaded;
  if (focus) rememberScroll();
  const tab = tabs.add(path);
  if (!tab.loaded) {
    try {
      const body = await api.read(path);
      Object.assign(tab, body, { loaded: true, saved: body.text, view: readView(path) });
      watcher?.prime(path, body.stamp);
    } catch (err) {
      Object.assign(tab, { loaded: true, error: err.message });
    }
  }
  const activate = focus && request === app.openRequest;
  if (activate) {
    tabs.active = tab.id;
    revealTab(tab);
    if (!app.restoring) rememberPath(path);
  }
  saveSession();
  tabs.draw();
  if (activate && !alreadyActive) drawContent();
}

async function closeTab(id) {
  return closeTabs([id]);
}

function disposeTabResources(tab) {
  disposeTabViewer(tab);
  disposeTabPreview(tab);
  tab.terminal?.dispose();
  tab.commitView?.dispose();
  tab.diffSubscription?.dispose();
  if (!tab.kind) {
    if (app.editor?.getModel() === app.models.get(tab.path)) app.editor.setModel(null);
    disposeModel(tab.path);
  }
  if (tab.diffModels) {
    if (app.diffEditor?.getModel()?.modified === tab.diffModels.modified) app.diffEditor.setModel(null);
    tab.diffModels.original.dispose(); tab.diffModels.modified.dispose();
  }
}

async function closeTabs(ids, { confirmed = false } = {}) {
  if (app.closingTabs) return false;
  const selected = new Set(ids);
  const targets = tabs.items.filter((tab) => selected.has(tab.id));
  if (!targets.length) return true;
  const generation = app.workspaceGeneration;
  app.closingTabs = true;
  try {
    const dirty = targets.filter((tab) => tab.dirty);
    if (!confirmed && dirty.length) {
      const title = targets.length === 1 ? "Close tab" : "Close " + targets.length + " tabs";
      const message = dirty.length === 1 ? dirty[0].name + " has unsaved changes. Discard them?"
        : dirty.length + " tabs have unsaved changes. Discard those changes and close the selected tabs?";
      if (!await ask(title, message, { accept: targets.length === 1 ? "Close" : "Close tabs", danger: true })) return false;
    }
    if (generation !== app.workspaceGeneration) return false;
    rememberScroll();
    app.openRequest++;
    for (const tab of targets) {
      if (tabs.find(tab.id) !== tab) continue;
      tabs.close(tab.id);
      disposeTabResources(tab);
    }
    saveSession(); tabs.draw(); drawContent();
    return true;
  } finally { app.closingTabs = false; }
}

async function save() {
  const tab = tabs.current;
  if (!tab || !app.config.writable) return;
  if (tab.truncated) { flash("This file is only partially loaded and cannot be saved.", true); return; }
  /* A diff tab saves the right-hand pane, which is the working tree. */
  const model = tab.kind ? tab.model : app.models.get(tab.path);
  if (!model) return;
  if (tab.kind === "terminal") return;
  if (tab.kind && !tab.editable) {
    flash("This diff cannot be saved back.", true);
    return;
  }
  const text = model.getValue();
  tab.saving = true;
  tab.reloadRequest = null;
  try {
    let result;
    try { result = await api.write(tab.path, text, tab.stamp); }
    catch (error) {
      if (error.status !== 409) throw error;
      if (!await ask("File changed on disk", "Overwrite the newer version of " + tab.name + " with your edits?", {accept: "Overwrite", danger: true})) return;
      result = await api.write(tab.path, text, null);
    }
    tab.saved = text;
    tab.stamp = result.stamp;
    watcher?.prime(tab.path, result.stamp);
    tab.diskChanged = false;
    updateReloadControl();
    /* The right-hand side is now what is on disk, so a later edit is
       measured against this rather than against the text first loaded. */
    if (tab.kind) tab.new = text;
    tab.dirty = model.getValue() !== text;
    tabs.draw();
    flash("Saved " + tab.name);
    review.refresh();
  } catch (err) {
    flash(err.message, true);
  } finally { tab.saving = false; }
}

let flashTimer = null;
function flash(message, bad) {
  dom.flash.textContent = message;
  dom.flash.className = "flash" + (bad ? " is-error" : "");
  dom.flash.hidden = false;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => {
    dom.flash.hidden = true;
  }, bad ? 6000 : 2000);
}

/* -- search --------------------------------------------------------------- */

let watcher = null;
let finder = null;

async function pickPath(match) {
  setMode("files");
  if (match.is_dir) await rootTreeAt(match.path);
  else { await openFile(match.path); await tree.reveal(match.path); }
}

function wireNavigation() {
  const query = (value) => api.search(value, 60, tree.showHidden, tree.root);
  const shortcuts = () => [{path: readHome(), name: "Home", is_dir: true}, {path: "/", name: "Root", is_dir: true}];
  const onError = (message) => flash(message, true);
  finder = new Suggestions(dom.search, dom.results, { query, shortcuts, onError,
    onVisibility: (visible) => { dom.tree.hidden = app.mode !== "files" || visible; },
    submitInput: (value) => value.includes("/") || value.trim() === "~",
    onPick: async (match) => { dom.search.value = ""; await pickPath(match); } });
  watcher = new ChangeWatcher({
    paths: () => [...tree.watchedPaths(), ...tabs.items.filter((tab) => !tab.kind || tab.kind === "diff").map((tab) => tab.path)],
    snapshot: async (paths) => {
      // Different tabs can hold different versions of the same file. Compare
      // each buffer's loaded stamp, not the watcher's shared path baseline.
      const versions = tabs.items.filter((tab) => (!tab.kind || tab.kind === "diff") && tab.loaded)
        .map((tab) => ({ tab, path: tab.path, stamp: tab.stamp }));
      const stamps = await api.watch(paths);
      if (!app.moving && !app.restoring) for (const { tab, path, stamp } of versions) {
        // A save/reload or move during this request supersedes its observation.
        if (!tabs.items.includes(tab) || tab.path !== path || tab.stamp !== stamp) continue;
        // Missing files leave the last readable version on screen.
        if (stamp != null && stamps[path] != null && stamps[path] !== stamp) {
          tab.diskChanged = true;
          tab.diskObservation = stamps[path];
          if (tabs.current === tab) updateReloadControl();
        }
        // Retry an unsuccessful automatic read even if only a diff's base
        // changed and the working file still has the same disk stamp.
        if (stamps[path] != null) autoReloadTab(tab);
      }
      return stamps;
    },
    paused: () => app.restoring || app.moving || Boolean(tree.dragged),
    onVisible: async () => { await refreshStore(); if (app.mode === "review") await review.load({quiet: true}); },
    onChange: async ({changed, gone}) => {
      if (app.moving) return;
      if (gone.includes(tree.root)) {
        const navigation = ++treeNavigation;
        const path = await usableTreeRoot(readHome());
        if (navigation !== treeNavigation) return;
        tree.root = path; tree.expanded.add(path);
        rememberFolder(path);
        review.setScope(tree.root); store.set(TREE_ROOT_KEY, tree.root);
        saveSession(); drawCurrentCrumbs();
      }
      if (changed.some((path) => tree.watchedPaths().includes(path)) || gone.length) await tree.refreshVisible(gone);
    },
  });
}

async function usableTreeRoot(preferred) {
  for (const path of [...new Set([preferred, readHome(), app.config.home, "/"])]) {
    if (typeof path !== "string" || !path.startsWith("/")) continue;
    tree.refresh(path);
    try { await tree.listing(path); return path; } catch (_) { /* Try the next existing folder. */ }
  }
  return "/";
}

/* -- start ---------------------------------------------------------------- */

const tabs = new Tabs(document.getElementById("tabs"), {
  onSelect: selectTab,
  onClose: closeTab,
  onReorder: saveSession,
  onContext: (event, tab) => tabMenu(event, tab),
});

const tree = new Tree(document.getElementById("tree"), {
  onOpenFile: (path) => openFile(path),
  onOpenFolder: (path) => rootTreeAt(path).catch((error) => flash(error.message, true)),
  onExpand: saveSession,
  onContext: (event, entry) => treeMenu(event, entry),
  onRootChanged: (path) => {
    treeNavigation++;
    rememberFolder(path);
    review.setScope(path);
    if (!app.restoring) { store.set(TREE_ROOT_KEY, path); rememberPath(path); saveSession(); }
    drawCurrentCrumbs();
  },
  onListing: (path, stamp) => watcher?.prime(path, stamp),
  onMove: (entry, folder) => moveEntry(entry, folder),
  onError: (message) => flash(message, true),
});

/* Git status marks changed files directly in the Explorer. */
function applyStatusToTree(status) {
  tree.status = new Map();
  if (status && status.repo !== false) {
    for (const entry of status.files) tree.status.set(entry.request, entry.status);
  }
  if (app.mode === "files") tree.draw();
  writeLocation();
}

function reviewPathValues(entry) {
  const requested = entry.request || (entry.wholeCommit ? entry.repo : entry.path) || "/";
  const repo = (entry.repo || review.repo || "/").replace(/\/+$/, "");
  const full = absolutePath(requested).replace(/\/+$/, "") || "/";
  const relative = entry.relative || (entry.request && entry.path) ||
    (requested === (repo || "/") ? "." : requested.startsWith(repo + "/") ? requested.slice(repo.length + 1) : requested.replace(/^\//, ""));
  return { requested, full, relative, name: basename(full), folder: !!(entry.is_dir || entry.wholeCommit) };
}

function reviewCopyItems(entry) {
  const value = reviewPathValues(entry);
  return pathCopyItems(value.requested, { relative: value.relative, isDir: value.folder, name: value.name });
}

function wholeFileItems(path) {
  return ["-", { label: "Open Whole File in Files", run: () => openFileFromReview(path) }];
}

function reviewContextMenu(event, entry) {
  event.preventDefault();
  const items = reviewCopyItems(entry);
  if (!entry.is_dir) items.push(...wholeFileItems(entry.request));
  showMenu(event.clientX, event.clientY, items);
}

function reviewPathHeader(tab) {
  const value = reviewPathValues(tab);
  const copy = el("button", { className: "mini review-copy-path", textContent: "Copy", title: "Copy path in repository", ariaLabel: "Copy path in repository" });
  copy.addEventListener("click", () => copyText(value.relative, "the path in the repository"));
  const header = el("div", { className: "review-diff-header" }, [el("span", { textContent: value.relative }), copy]);
  return header;
}

async function selectReviewRepository(path) {
  if (tree.root === path) return;
  const navigation = ++treeNavigation;
  await tree.listing(path);
  if (navigation !== treeNavigation || review.repo !== path) return;
  tree.root = path;
  store.set(TREE_ROOT_KEY, path); rememberPath(path);
  review.setScope(path); tree.expanded.add(path); tree.refresh();
  await tree.draw();
  if (navigation !== treeNavigation || tree.root !== path) return;
  rememberFolder(path); drawCurrentCrumbs(); saveSession();
}

const review = new Review(document.getElementById("review"), {
  onLoaded: (status) => applyStatusToTree(status),
  onChanged: () => { saveSessionSoon(); writeLocation(); },
  onRefreshed: (status) => checkWorkingDiffs(status),
  onOpenDiff: (path, rev, entry) => openDiff(path, rev, entry),
  onOpenCommit: (commit, entry, context) => openCommit(commit, entry, context),
  onSelectRepository: selectReviewRepository,
  onContext: reviewContextMenu,
});

async function checkWorkingDiffs(status) {
  for (const tab of tabs.items.filter((tab) => tab.kind === "diff" && !tab.rev && tab.repo === status.root)) {
    const { old, new: text, stamp, path } = tab;
    const request = tab.changeRead = {};
    try {
      const body = await api.gitDiff(path, null, tab.base, tab.repo);
      if (tab.changeRead !== request || !tabs.items.includes(tab) || tab.path !== path ||
          tab.old !== old || tab.new !== text || tab.stamp !== stamp) continue;
      tab.changedContent = { path, baseline: stamp, observation: tab.diskObservation, body };
      if (tab.old !== body.old || tab.new !== body.new || tab.stamp !== body.stamp) {
        tab.diskChanged = true;
        autoReloadTab(tab);
      }
      if (tabs.current === tab) scheduleReloadControl();
    } catch (_) {
      // A failed status read may have superseded an unfinished direct read.
      // Let the next watch observation retry that comparison too.
      if (tab.changeRead === request && tab.changedContent && !tab.changedContent.body) {
        tab.changedContent.error = true;
        tab.changedContent.retryAt = Date.now() + 1000;
      }
    }
  }
}

/* Opening a file from the review side switches back to Files, because
   otherwise the tree the file lives in is not on screen to show where it
   landed. */
function openFileFromReview(path) {
  setMode("files");
  openFile(path).then(() => tree.reveal(path));
}

function collect() {
  [
    "crumbs", "status", "tabs", "tree", "sidebar", "editorHost", "previewHost",
    "preview", "markdownContent", "toc", "tocPane", "notice", "viewToggle", "fileSearch", "search",
    "viewerHost", "diffHost", "review", "diffLayout",
    "sessionName", "sessionList", "starSession", "sbPath", "sbDir", "sbName",
    "sbKind", "sbSize", "showHiddenFiles",
    "results", "flash", "mode",
  ].forEach((id) => {
    dom[id] = document.getElementById(id);
  });
  /* The old id, kept because a great deal refers to it. */
  dom.status = { set textContent(_ignored) {} };
}

function placeSessionList() {
  if (dom.sessionList.hidden) return;
  const button = document.getElementById("sessionsButton").getBoundingClientRect();
  Object.assign(dom.sessionList.style, {
    position: "fixed", right: "auto", top: (button.bottom + 5) + "px",
    maxHeight: Math.max(80, innerHeight - button.bottom - 16) + "px",
  });
  // Measure after switching to viewport positioning, including on first open.
  dom.sessionList.style.left = Math.max(8, Math.min(button.left,
    document.documentElement.clientWidth - dom.sessionList.offsetWidth - 8)) + "px";
}

function wireChrome() {
  wireNavigation();
  document.addEventListener("visibilitychange", markSessionViewed);
  window.addEventListener("focus", markSessionViewed);
  const updateSessionActivity = () => {
    if (!document.hidden && !dom.sessionList.hidden) sessions.updateActivity(dom.sessionList);
  };
  let activityTimer = setInterval(updateSessionActivity, 1000);
  updateSidebarToggle();
  document.getElementById("terminalButton").title = "Open terminal  " + primaryShortcut("`");
  dom.search.title = "Search this folder or enter a path, including ~.  " + primaryShortcut("P") + " or " + primaryShortcut("K");
  document.getElementById("sidebarToggle").addEventListener("click", toggleSidebar);

  const go = async () => {
    const path = interpretPath(
      dom.search.value,
      app.config && app.config.root,
      app.config && app.config.home
    );
    if (!path) return;
    finder.hide();
    try { await pickPath(await api.info(path)); dom.search.value = ""; }
    catch (error) { flash(error.message, true); }
  };
  dom.search.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.defaultPrevented) { event.preventDefault(); go(); }
  });
  document.getElementById("clearSearch").addEventListener("click", () => {
    dom.search.value = "";
    dom.search.focus();
    finder.hide();
  });
  document.getElementById("homeButton").addEventListener("click", goHome);
  document.getElementById("folderBack").addEventListener("click", () => goFolderHistory(-1));
  document.getElementById("folderForward").addEventListener("click", () => goFolderHistory(1));
  document.getElementById("reloadFile").addEventListener("click", () => {
    const tab = tabs.current;
    if (tab) reloadTab(tab);
  });
  document.getElementById("scrollFileTop").addEventListener("click", () => scrollFileTo("top"));
  document.getElementById("scrollFileBottom").addEventListener("click", () => scrollFileTo("bottom"));
  document.getElementById("tocHide").addEventListener("click", () => {
    store.set("ficelo.toc", { hidden: true }); updateOutlineVisibility();
  });
  document.getElementById("tocToggle").addEventListener("click", () => {
    if (document.getElementById("tocToggle").dataset.panel === "minimap") {
      app.appearance.minimap = app.appearance.minimap === false;
      writeAppearance(app.appearance);
      app.editor.updateOptions({ minimap: { enabled: app.appearance.minimap } });
      updateRightPanelToggle();
    } else {
      store.set("ficelo.toc", { hidden: !store.get("ficelo.toc", {}).hidden });
      updateOutlineVisibility();
    }
  });
  let outlineFrame = null;
  const rememberPreview = debounce((tab) => {
    if (tabs.current === tab && tab?.positionReady && !dom.previewHost.hidden) rememberScroll();
  }, 250);
  dom.preview.addEventListener("scroll", () => {
    scheduleReloadControl();
    const position = app.previewPosition;
    if (position?.tab === tabs.current && position.top !== null && dom.preview.scrollTop !== position.top) {
      position.moved = true;
      if (position.explicit) tabs.current.positionReady = true;
    }
    if (tabs.current?.positionReady) rememberPreview(tabs.current);
    if (outlineFrame) return;
    outlineFrame = requestAnimationFrame(() => { outlineFrame = null; updateActiveHeading(); });
  }, { passive: true });
  document.getElementById("copyPath").addEventListener("click", async () => {
    const tab = tabs.current;
    const relative = tab ? (tab.kind === "terminal" || tab.wholeCommit ? tab.path : parentOf(tab.path)) : tree.root;
    const absolute = absolutePath(relative).replace(/\/+$/, "") || "/";
    await copyText(absolute, absolute);
  });

  document.getElementById("sessionsButton").addEventListener("click", async (event) => {
    event.stopPropagation();
    const opening = dom.sessionList.hidden;
    if (opening) {
      saveSession();
      await flushStore();
      try { await refreshStore(); } catch (error) { flash(error.message, true); }
      redrawSessions();
    }
    dom.sessionList.hidden = !opening;
    placeSessionList();
  });
  document.addEventListener("click", (event) => {
    // Row actions can replace the clicked node before this event bubbles here.
    if (!dom.sessionList.hidden && !event.composedPath().includes(dom.sessionList)) {
      dom.sessionList.hidden = true;
    }
  });
  document.getElementById("reopenTab").addEventListener("click", reopenClosed);
  document.getElementById("newSession").addEventListener("click", startNewSession);
  dom.starSession.addEventListener("click", () => toggleStar(sessions.currentId()));
  dom.sessionName.addEventListener("input", sizeSessionName);
  dom.sessionName.addEventListener("change", saveSession);
  dom.sessionName.addEventListener("blur", saveSession);

  document.getElementById("newFile").addEventListener("click", () =>
    promptCreate(tree.creationFolder(), "file")
  );
  document.getElementById("newFolder").addEventListener("click", () =>
    promptCreate(tree.creationFolder(), "folder")
  );

  dom.search.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      dom.search.value = "";
      finder.hide();
    }
  });

  // Capture app shortcuts before editors and native media controls consume them.
  document.addEventListener("keydown", (event) => {
    const command = shortcutFor(event);
    if (!command) return;
    event.preventDefault();
    event.stopPropagation();
    handleShortcut(command);
  }, true);

  document.querySelectorAll("#modeSwitch .seg").forEach((button) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  });
  document.getElementById("diffMap").addEventListener("click", () => {
    writeMap(!readMap());
    drawDiffLayoutButtons();
    if (app.diffEditor) app.diffEditor.updateOptions({ minimap: { enabled: readMap() }, renderOverviewRuler: readMap() });
    tabs.current?.commitView?.setOptions({ map: readMap() });
  });
  document.querySelectorAll("#diffLayout .seg[data-layout]").forEach((button) => {
    button.addEventListener("click", () => {
      writeLayout(button.dataset.layout);
      tabs.current?.commitView?.setOptions({ layout: button.dataset.layout });
      drawDiffLayoutButtons();
      if (app.diffEditor) {
        app.diffEditor.updateOptions({ renderSideBySide: button.dataset.layout === "side" });
      }
    });
  });
  drawDiffLayoutButtons();

  wireDismissal();

  /* Capture unselected diff surfaces before Monaco opens its own menu.
     Selected text and ordinary source editors keep native editing actions. */
  document.querySelector(".stage").addEventListener("contextmenu", documentMenu, true);

  const panel = document.getElementById("settings");
  let homeEdit = 0;
  const closeSettings = async () => {
    if (panel.hidden) return;
    const input = panel.querySelector("#homePath");
    const value = input.value;
    panel.hidden = true;
    const edit = ++homeEdit;
    const current = absolutePath(readHome());
    if (value === current) return;
    try {
      const folder = await validateHome(value, { root: app.config.root, home: app.config.home });
      if (edit !== homeEdit) return;
      store.set(HOME_KEY, folder);
      input.value = absolutePath(folder);
      drawPageTitle();
    } catch (_) {
      if (edit !== homeEdit) return;
      input.value = absolutePath(readHome());
      flash("Home folder change failed - " + (value || '""') + " is not a valid folder", true);
    }
  };
  const changeAppearance = (values) => {
    applyAppearance(values, { editor: app.editor, diffEditor: app.diffEditor });
    app.appearance = values;
    if (inSplit()) app.editor?.updateOptions({ minimap: { enabled: false } });
    updateOutlineVisibility();
    dom.showHiddenFiles.checked = values.showHidden === true;
    tabs.current?.commitView?.setOptions({ fontSize: values.diffFontSize });
    writeAppearance(values);
    applyTheme(values.theme);
    updateReloadControl();
    tabs.items.forEach(autoReloadTab);
    if (tree.showHidden !== dom.showHiddenFiles.checked) {
      tree.showHidden = dom.showHiddenFiles.checked;
      /* Every cached listing was fetched under the old setting. */
      finder.hide();
      tree.refresh();
      tree.draw();
    }
  };
  for (const { id, setting } of FILE_ACTIONS) {
    document.getElementById(id).addEventListener("contextmenu", (event) => {
      event.preventDefault(); event.stopPropagation();
      showMenu(event.clientX, event.clientY, [{ label: "Hide button", run: () => {
        app.appearance[setting] = false;
        changeAppearance(app.appearance);
        if (!panel.hidden) buildPanel(panel, app.appearance, changeAppearance);
        document.getElementById("settingsToggle").focus();
      } }]);
    });
  }
  dom.showHiddenFiles.addEventListener("change", () => {
    app.appearance.showHidden = dom.showHiddenFiles.checked;
    changeAppearance(app.appearance);
    if (!panel.hidden) buildPanel(panel, app.appearance, changeAppearance);
  });
  document.getElementById("settingsToggle").addEventListener("click", () => {
    if (!panel.hidden) { closeSettings(); return; }
    // Reopening starts a new edit; an older pending validation cannot win.
    homeEdit++;
    buildPanel(panel, app.appearance, changeAppearance, { home: absolutePath(readHome()) });
    panel.hidden = false;
  });
  document.addEventListener("click", (event) => {
    // Theme/reset may replace the clicked node before this event bubbles here.
    if (!event.composedPath().includes(panel) && !event.target.closest?.("#settingsToggle")) closeSettings();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || panel.hidden || event.target.closest("dialog")) return;
    event.preventDefault();
    closeSettings();
    document.getElementById("settingsToggle").focus();
  });

  window.addEventListener("resize", () => {
    placeSessionList();
    if (app.editor) app.editor.layout();
    if (app.diffEditor) app.diffEditor.layout();
  });
  document.querySelector(".stage").addEventListener("scroll", scheduleReloadControl, true);
  document.querySelector(".stage").addEventListener("toggle", scheduleReloadControl, true);
  document.querySelector(".stage").addEventListener("load", scheduleReloadControl, true);
  const reloadLayout = app.reloadLayout = new ResizeObserver(scheduleReloadControl);
  reloadLayout.observe(dom.tocPane);
  reloadLayout.observe(document.querySelector(".stage"));
  reloadLayout.observe(dom.markdownContent);
  reloadLayout.observe(dom.viewerHost);
  /* pagehide fires on mobile and on bfcache navigations, where
     beforeunload may not. */
  window.addEventListener("pagehide", () => {
    panelLayout?.cancel();
    rememberScroll(); saveSession(); flushStore(true);
    tabs.items.forEach((tab) => tab.terminal?.dispose(true));
    watcher?.stop();
    clearInterval(activityTimer);
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    markSessionViewed(); watcher?.start();
    panelLayout?.refresh();
    clearInterval(activityTimer);
    activityTimer = setInterval(updateSessionActivity, 1000);
  });
  window.addEventListener("ficelo:redirect", async (event) => {
    if (event.detail.from !== "ficelo.workspace." + sessions.currentId() || app.redirecting) return;
    if (tabs.items.some((tab) => tab.dirty)) {
      flash("This session was merged. Save your edits before reloading it."); return;
    }
    app.redirecting = true;
    try {
      await flushStore(); await loadStore();
      await loadSession(event.detail.key.slice("ficelo.workspace.".length), { force: true, remember: false });
    } finally { app.redirecting = false; }
  });
  window.addEventListener("ficelo:save-error", (event) => flash(event.detail + ". Changes are kept for retry.", true));
  window.addEventListener("beforeunload", (event) => {
    rememberScroll();
    saveSession();
    flushStore(true);
    if (tabs.items.some((tab) => tab.dirty)) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

/* Ctrl/Cmd+P and Ctrl/Cmd+K reach Explorer search. Alt shortcuts control
   files and tabs without replacing the browser's own tab shortcuts. */
function handleShortcut(command) {
  const tab = tabs.current;
  switch (command) {
    case "terminal": openTerminal(); break;
    case "save": save(); break;
    case "find":
      if (dom.sidebar.hidden) toggleSidebar();
      setMode("files");
      dom.search.focus();
      dom.search.select();
      break;
    case "sidebar": toggleSidebar(); break;
    case "reload": if (tab) reloadTab(tab); break;
    case "reopen": reopenClosed(); break;
    case "close": if (tab) closeTab(tab.id); break;
    case "move-left": case "move-right":
      if (tab && tabs.move(tab.id, command === "move-left" ? -1 : 1)) {
        saveSession();
        tabs.draw();
      }
      break;
    case "previous": case "next": {
      const next = tabs.neighbour(command === "previous" ? -1 : 1);
      if (next) selectTab(next);
      break;
    }
    default:
      if (typeof command === "string" && /^tab-[1-9]$/.test(command)) {
        const next = tabs.nth(Number(command.slice(4)));
        if (next) selectTab(next);
      }
  }
}

function updateSidebarToggle() {
  const button = document.getElementById("sidebarToggle");
  button.setAttribute("aria-pressed", String(!dom.sidebar.hidden));
  button.setAttribute("aria-expanded", String(!dom.sidebar.hidden));
  button.title = (dom.sidebar.hidden ? "Show file tree  " : "Hide file tree  ") + primaryShortcut("B");
  panelLayout?.refresh();
}

function toggleSidebar() {
  dom.sidebar.hidden = !dom.sidebar.hidden;
  updateSidebarToggle();
  if (app.editor) app.editor.layout();
  if (app.diffEditor) app.diffEditor.layout();
}

function selectTab(id) {
  app.openRequest++;
  const alreadyActive = tabs.active === id;
  rememberScroll();
  tabs.active = id;
  const chosen = tabs.find(id);
  if (chosen && !chosen.kind) revealTab(chosen);
  saveSession();
  tabs.draw();
  if (!alreadyActive || chosen?.kind === "terminal") drawContent();
  watcher?.check();
}

function revealTab(tab) {
  tree.select(tab.path);
  if (app.restoring || (tree.root !== "/" && !tab.path.startsWith(tree.root + "/"))) return;
  if (ancestors(tab.path).some((folder) => !tree.expanded.has(folder))) {
    tree.reveal(tab.path, { widen: false }).catch((error) => flash(error.message, true));
  } else tree.mount.querySelector('[data-path="' + CSS.escape(tab.path) + '"]')?.scrollIntoView({ block: "nearest" });
}

const automaticReloads = new WeakSet();
async function autoReloadTab(tab) {
  if (app.appearance?.manualRefresh !== false || !tab.diskChanged || tab.dirty || tab.saving ||
      tab.rev || tab.wholeCommit || automaticReloads.has(tab)) return;
  automaticReloads.add(tab);
  try { await reloadTab(tab, { automatic: true }); }
  finally { automaticReloads.delete(tab); }
}

/* Explicit reload (also Alt+R) discards local edits only after confirmation.
   Background reads must never replace edits or a newer save/reload. */
async function reloadTab(tab, { automatic = false } = {}) {
  if (tab.kind === "terminal" || tab.saving || (automatic && tab.dirty)) return false;
  if (tab.dirty && !await ask("Reload file", tab.name + " has unsaved changes. Discard them?", { accept: "Reload", danger: true })) return false;
  const path = tab.path, stamp = tab.stamp, workspace = app.workspaceGeneration;
  const model = tab.kind ? tab.model : app.models.get(path);
  const version = model?.getVersionId();
  const request = tab.reloadRequest = {};
  if (tabs.current === tab) rememberScroll();
  try {
    const body = tab.wholeCommit ? await api.gitCommit(tab.rev, tab.repo, true)
      : tab.kind ? await api.gitDiff(path, tab.rev, tab.base, tab.repo) : await api.read(path);
    if (app.moving || workspace !== app.workspaceGeneration || !tabs.items.includes(tab) ||
        path !== tab.path || stamp !== tab.stamp || request !== tab.reloadRequest ||
        (automatic && app.appearance.manualRefresh !== false)) return false;
    const currentModel = tab.kind ? tab.model : app.models.get(path);
    // A background tab may have acquired a model while the read was pending.
    if ((automatic && tab.dirty) || (model && (currentModel !== model || model.getVersionId() !== version)) ||
        (!model && currentModel && tab.dirty)) {
      if (!automatic) flash("Edits made during reload were kept. Reload again to discard them.");
      return false;
    }
    if (tabs.current === tab) rememberScroll();
    if (tab.wholeCommit) {
      tab.commitData = body;
      tab.commitView?.dispose(); tab.commitView = null;
      tab.error = undefined;
    } else if (tab.kind) {
      Object.assign(tab, body, { dirty: false, error: undefined });
      if (!tab.rev) watcher?.prime(tab.path, tab.stamp);
    } else {
      if (!currentModel || currentModel.getValue() !== body.text) disposeModel(tab.path);
      disposeTabViewer(tab);
      disposeTabPreview(tab);
      Object.assign(tab, body, { loaded: true, saved: body.text, error: undefined, dirty: false });
      watcher?.prime(tab.path, body.stamp);
    }
    tab.diskChanged = false;
    tabs.draw();
    if (tabs.current === tab) drawContent();
    if (!automatic) flash("Reloaded " + tab.name);
    return true;
  } catch (err) {
    if (!automatic) flash(err.message, true);
    return false;
  }
}

async function reopenClosed() {
  const last = tabs.takeLastClosed();
  if (!last) {
    flash("No recently closed tab");
    return;
  }
  if (last.kind) await openDiff(last.path, last.rev, last);
  else await openFile(last.path);
  /* Back where it was, not on the end: a tab reopened by accident of
     ordering is disorienting. */
  const tab = tabs.current;
  if (tab && typeof last.at === "number") {
    const now = tabs.items.indexOf(tab);
    if (now >= 0 && last.at < tabs.items.length) tabs.move(tab.id, last.at - now);
  }
  saveSession();
  tabs.draw();
}

function drawDiffLayoutButtons() {
  const current = readLayout();
  document.querySelectorAll("#diffLayout .seg[data-layout]").forEach((button) => {
    if (button.dataset.layout === current) button.setAttribute("aria-pressed", "true");
    else button.removeAttribute("aria-pressed");
  });
  const map = document.getElementById("diffMap");
  if (readMap()) map.setAttribute("aria-pressed", "true");
  else map.removeAttribute("aria-pressed");
}

/* -- creating, renaming, deleting ----------------------------------------- */

/* Toolbar actions use the selected expanded folder, then the explorer root. */
function currentFolder() {
  const tab = tabs.current;
  return tab && !tab.kind ? parentOf(tab.path) : tree.root || readHome();
}

async function refreshAround(path) {
  tree.refresh(parentOf(path));
  tree.refresh(path);
  await tree.draw();
  if (app.mode === "review") review.load();
}

/* Clipboard access needs a secure context, which plain http to a remote box
   is not. Falling back to showing the text is more use than an error. */
async function copyText(text, what = "link") {
  if (await copyToClipboard(text)) flash("Copied " + what);
}

function absolutePath(path) {
  return (app.config.root || "").replace(/\/+$/, "") + path;
}

function nativeDocumentContext(event) {
  if (window.getSelection()?.toString() || event.target.closest("#editorHost,.terminal-view,.xterm,input,textarea,[contenteditable=true]")) return true;
  if (!event.target.closest(".monaco-editor")) return false;
  const editors = app.monaco.editor.getDiffEditors().flatMap((diff) => [diff.getOriginalEditor(), diff.getModifiedEditor()]);
  const editor = editors.find((item) => item.getDomNode()?.contains(event.target));
  return !!editor?.getSelections()?.some((selection) => !selection.isEmpty());
}

function pathCopyItems(path, { isDir = false, name, relative = "" } = {}) {
  const full = absolutePath(path).replace(/\/+$/, "") || "/";
  const items = [];
  if (relative && relative !== full) items.push({ label: "Copy Path in Repo", run: () => copyText(relative, "the path in the repository") });
  items.push(
    { label: "Copy Full Path", run: () => copyText(full, "the full path") },
    { label: isDir ? "Copy Folder Name" : "Copy File Name", run: () => copyText(name || basename(full), isDir ? "the folder name" : "the file name") });
  return items;
}

function tabCopyItems(tab) {
  if (tab.wholeCommit) return pathCopyItems(tab.repo || tab.path, { name: tab.commitData?.commit?.short || tab.rev?.slice(0, 7) || tab.name });
  return tab.kind ? reviewCopyItems(tab) : pathCopyItems(tab.path);
}

function explorerRootItem(path) {
  return { label: "Set as Explorer Root", run: () => rootTreeAt(path).catch((error) => flash(error.message, true)) };
}

function tabCloseItems(tab) {
  const at = tabs.items.indexOf(tab);
  const right = tabs.items.slice(at + 1).map((other) => other.id);
  const all = tabs.items.map((other) => other.id);
  return ["-",
    { label: tab.kind === "terminal" ? "Close terminal" : "Close Tab", run: () => closeTab(tab.id) },
    { label: right.length ? "Close " + right.length + " to the Right" : "Close to the Right", run: right.length ? () => closeTabs(right) : null },
    { label: "Close All " + all.length, danger: true, run: () => closeTabs(all) }];
}

function documentMenu(event) {
  const tab = tabs.current;
  if (event.target.closest?.("#fileActions")) return;
  if (!tab?.path || tab.kind === "terminal" || event.defaultPrevented || nativeDocumentContext(event)) return;
  event.preventDefault();
  event.stopPropagation();
  const items = [...tabCopyItems(tab), ...tabCloseItems(tab)];
  if (!tab.kind) items.push(...entryMenuItems(event, { path: tab.path, is_dir: false }));
  else if (!tab.wholeCommit) items.push(...wholeFileItems(tab.path));
  showMenu(event.clientX, event.clientY, items);
}

async function showPathInfo(event, path) {
  try {
    const info = await api.info(path), usage = info.usage;
    const rows = [["Name", info.name], ["Where", absolutePath(parentOf(path))],
      ["Kind", info.is_symlink ? "Symbolic link" : info.is_dir ? "Folder" : kindOf(path)],
      ["Size", formatSize(usage ? usage.bytes : info.size) + (usage && !usage.complete ? " (at least)" : "")],
      ["Modified", info.modified ? info.modified.replace("T", " ") : "unknown"], ["Mode", info.mode || "unknown"]];
    if (usage) rows.splice(4, 0, ["Contains", `${usage.files} files, ${usage.folders} folders, ${usage.links} links${usage.complete ? "" : " (partial)"}`]);
    showInfo(event.clientX, event.clientY, rows);
  } catch (error) { flash(error.message, true); }
}

function entryMenuItems(event, entry) {
  const folder = entry.is_dir ? entry.path : parentOf(entry.path);
  const items = ["-", { label: "Info", run: () => showPathInfo(event, entry.path) }];
  if (entry.is_dir && entry.path !== tree.root) items.push("-", explorerRootItem(entry.path));
  if (!entry.is_dir && /\.(html?|png|jpe?g|gif|webp|svg|bmp|ico|pdf)$/i.test(entry.path)) {
    const url = /\.(html?|svg)$/i.test(entry.path)
      ? "/preview" + entry.path.split("/").map(encodeURIComponent).join("/")
      : "/api/raw?path=" + encodeURIComponent(entry.path);
    items.push("-", { label: "Open Raw in New Tab", run: () => window.open(url, "_blank", "noopener,noreferrer") });
  }
  if (app.config.writable) items.push("-",
    { label: "New File", run: () => promptCreate(folder, "file") },
    { label: "New Folder", run: () => promptCreate(folder, "folder") }, "-",
    { label: "Rename", run: entry.path !== "/" ? () => promptRename(entry) : null },
    { label: "Delete", danger: true, run: entry.path !== "/" ? () => confirmDelete(entry) : null });
  return items;
}

function fileMenuItems(event, entry) {
  return [...pathCopyItems(entry.path, { isDir: entry.is_dir }), ...entryMenuItems(event, entry)];
}

function treeMenu(event, entry) {
  showMenu(event.clientX, event.clientY, fileMenuItems(event, entry));
}

async function promptCreate(folder, kind) {
  const name = await ask("New " + kind, "Name of the new " + kind + ":", { value: "", accept: "Create" });
  if (!name) return;
  const target = (folder === "/" ? "" : folder) + "/" + name;
  try {
    const created = await api.create(target, kind);
    tree.expanded.add(folder);
    await refreshAround(target);
    if (kind === "file") {
      await openFile(created.path);
      const tab = tabs.find(created.path);
      if (tab && app.config.writable) {
        if (previewable(tab.path)) await setView(tab, "edit");
        else drawContent();
      }
    }
    flash("Created " + name);
  } catch (err) {
    flash(err.message, true);
  }
}

async function promptRename(entry) {
  const name = await ask(entry.is_dir ? "Rename folder" : "Rename file", "New name:", { value: basename(entry.path), accept: "Rename" });
  if (!name || name === basename(entry.path)) return;
  const folder = parentOf(entry.path);
  const target = (folder === "/" ? "" : folder) + "/" + name;
  try {
    await relocate(entry, target);
    flash("Renamed to " + name);
  } catch (err) {
    flash(err.message, true);
  }
}

async function moveEntry(entry, folder) {
  const target = moveDestination(entry, folder);
  if (!target) return;
  await relocate(entry, target);
  flash("Moved " + entry.name);
}

async function relocate(entry, target) {
  app.moving = true;
  try {
  const collisions = tabs.items.filter((tab) => !tab.kind && tab.dirty &&
    tabs.items.some((other) => other !== tab && !other.kind && retargetPath(other.path, entry.path, target) === tab.path));
  if (collisions.length) throw new Error("Save or close the unsaved destination tab before moving this path.");
  rememberScroll(); saveSession();
  const moved = await api.rename(entry.path, target);
  const from = moved.from, to = moved.to;
  const moving = tabs.items.filter((tab) => !tab.kind && retargetPath(tab.path, from, to) !== tab.path);
  const destinations = new Set(moving.map((tab) => retargetPath(tab.path, from, to)));
  tabs.items = tabs.items.filter((tab) => {
    if (moving.includes(tab) || tab.kind || !destinations.has(tab.path)) return true;
    disposeTabResources(tab); return false;
  });
  for (const tab of tabs.items) {
    const previous = tab.path, oldId = tab.id;
    Object.assign(tab, retargetTab(tab, from, to));
    if (!tab.kind) watcher?.prime(tab.path, tab.stamp);
    if (tabs.active === oldId) tabs.active = tab.id;
    if (!tab.kind && previous !== tab.path && app.models.has(previous)) {
      const model = app.models.get(previous);
      app.models.delete(previous); app.models.set(tab.path, model);
      app.monaco.editor.setModelLanguage(model, languageFor(tab.name));
    }
  }
  tabs.recentlyClosed = tabs.recentlyClosed.map((tab) => retargetTab(tab, from, to));
  tabs.persistClosed();
  for (const record of sessions.readAll()) {
    const updated = retargetWorkspace(record, from, to);
    if (JSON.stringify(updated) !== JSON.stringify(record)) sessions.save(updated);
  }
  for (const key of [VIEW_KEY, "ficelo.scroll", "ficelo.recent"]) store.set(key, retargetMap(store.get(key, {}), from, to));
  const home = store.get(HOME_KEY, null);
  if (home) store.set(HOME_KEY, retargetPath(home, from, to));
  tree.retarget(from, to);
  treeNavigation++;
  folderHistory.retarget(from, to);
  updateFolderButtons();
  store.set(TREE_ROOT_KEY, tree.root);
  review.setScope(tree.root);
  if (review.repo) review.repo = retargetPath(review.repo, from, to);
  tree.expanded.add(parentOf(to));
  await tree.draw();
  await review.refresh();
  saveSession(); tabs.draw(); drawContent();
  } finally { app.moving = false; }
}

async function confirmDelete(entry) {
  const affected = tabs.items.filter((tab) => !tab.rev && tab.kind !== "terminal" &&
    (tab.path === entry.path || (entry.is_dir && tab.path.startsWith(entry.path + "/"))));
  const dirty = affected.filter((tab) => tab.dirty).length;
  const message = "Delete " + basename(entry.path) + "?" + (dirty ?
    " This also discards unsaved changes in " + dirty + (dirty === 1 ? " open tab." : " open tabs.") : "");
  const generation = app.workspaceGeneration;
  if (!await ask(entry.is_dir ? "Delete folder" : "Delete file", message, { accept: "Delete", danger: true })) return;
  if (generation !== app.workspaceGeneration) return;
  const model = (tab) => tab.kind ? tab.model : app.models.get(tab.path);
  const snapshots = new Map(affected.map((tab) => {
    const original = model(tab);
    return [tab, { model: original, version: original?.getVersionId() }];
  }));
  try {
    await api.remove(entry.path);
    if (generation !== app.workspaceGeneration) return;
    // A slow delete must not discard edits typed after its confirmation.
    const closing = affected.filter((tab) => {
      const original = snapshots.get(tab);
      return tabs.find(tab.id) === tab && model(tab) === original.model &&
        original.model?.getVersionId() === original.version;
    });
    await closeTabs(closing.map((tab) => tab.id), { confirmed: true });
    for (const tab of affected) if (!closing.includes(tab) && tabs.find(tab.id) === tab) tab.diskChanged = true;
    await refreshAround(entry.path);
    flash("Deleted " + basename(entry.path) + (closing.length < affected.length ? ". Newer unsaved edits remain open." : ""));
  } catch (err) {
    flash(err.message, true);
  }
}

function tabMenu(event, tab) {
  const items = tab.kind === "terminal" ? [] : tabCopyItems(tab);
  const at = tabs.items.indexOf(tab);
  const move = (delta) => { if (tabs.move(tab.id, delta)) { saveSession(); tabs.draw(); } };
  if (items.length) items.push("-");
  items.push(
    { label: "Move Left", run: at > 0 ? () => move(-1) : null },
    { label: "Move Right", run: at >= 0 && at < tabs.items.length - 1 ? () => move(1) : null },
    ...tabCloseItems(tab));
  if (!tab.kind) items.push(...entryMenuItems(event, { path: tab.path, is_dir: false }));
  else if (tab.kind !== "terminal" && !tab.wholeCommit) items.push(...wholeFileItems(tab.path));
  showMenu(event.clientX, event.clientY, items);
}

async function start() {
  const initialUrl = location.href;
  collect();
  /* Before anything reads a setting: wireChrome already draws controls
     from remembered state. If the server cannot be reached the page starts
     with defaults, and the config request below reports why. */
  await loadStore();
  const initialSession = sessions.initialize();
  tabs.setWorkspace(sessions.currentId());
  wireChrome();

  try {
    app.config = await api.config();
  } catch (err) {
    dom.notice.textContent = "Cannot reach the server: " + err.message;
    dom.notice.className = "notice is-error";
    showOnly("notice");
    return;
  }
  const terminalButton = document.getElementById("terminalButton");
  terminalButton.hidden = !app.config.terminal;
  terminalButton.addEventListener("click", () => openTerminal());
  dom.mode.textContent = app.config.writable ? "Read/Write" : "read-only";
  dom.mode.hidden = app.config.writable;
  dom.mode.dataset.writable = String(app.config.writable);
  for (const id of ["newFile", "newFolder"]) document.getElementById(id).disabled = !app.config.writable;
  document.title = app.config.root;

  app.appearance = readAppearance();
  applyAppearance(app.appearance, {});
  applyTheme(app.appearance.theme);
  tree.showHidden = app.appearance.showHidden === true;
  dom.showHiddenFiles.checked = tree.showHidden;
  dom.showHiddenFiles.disabled = false;
  tree.writable = app.config.writable;
  /* Serving a whole filesystem is a real configuration; starting the tree
     at / is useless there, and search from / never reaches anything of
     yours before its budget runs out. */
  const remembered = initialSession?.root || store.get(TREE_ROOT_KEY, null);
  tree.expanded = new Set(initialSession?.expanded || ["/"]);
  tree.root = typeof remembered === "string" && remembered.startsWith("/")
    ? remembered
    : readHome();
  tree.root = await usableTreeRoot(tree.root);
  tree.expanded.add(tree.root);

  app.monaco = await loadMonaco();
  applyEditorTheme(document.documentElement.dataset.theme);
  app.editor = app.monaco.editor.create(dom.editorHost, {
    automaticLayout: true,
    fontFamily: CODE_FONT,
    bracketPairColorization: { enabled: true },
    smoothScrolling: true,
    cursorBlinking: "smooth",
    padding: { top: 8 },
    readOnly: !app.config.writable,
    minimap: { enabled: true },
    scrollBeyondLastLine: false,
    renderWhitespace: "selection",
  });
  applyAppearance(app.appearance, { editor: app.editor });
  app.editor.onDidScrollChange(scheduleReloadControl);
  app.editor.onDidLayoutChange(scheduleReloadControl);
  panelLayout = new PanelLayout(app.editor, app.monaco, () => {
    if (app.diffEditor && !dom.diffHost.hidden) app.diffEditor.layout();
  });

  await tree.draw();

  /* Fetched now rather than on first visit to Review, because the marks it
     produces belong in the Files tree. Deliberately not awaited: a slow or
     absent git must not hold up the page. */
  review.setScope(tree.root);
  review.restore(initialSession?.review);
  review.load().catch(() => {});

  const session = initialSession;
  if (session) dom.sessionName.value = session.name || "";
  drawSessionChrome();
  let asked, locationError;
  try { asked = routeFromUrl(initialUrl, app.config.root); }
  catch (error) { locationError = error.message; }
  if (session) await restoreTabs(session);
  else {
    const saved = Tabs.restored();
    await restoreTabs(saved);
  }
  if (asked) await openLocation(asked);
  else if (session?.mode) setMode(session.mode);
  app.restoring = false;
  resetFolderHistory();
  for (const [path, stamp] of tree.stamps) watcher.prime(path, stamp);
  watcher.start();
  if (asked?.path && !sessions.tabRecords(session || {}).some((t) => t.path === asked.path)) saveSession();
  if (initialSession) sessions.markActive();
  else saveSession();

  let openingLocation = null;
  const navigateLocation = async (href = location.href) => {
    if (openingLocation === href) return;
    openingLocation = href;
    try {
      const url = new URL(href);
      const legacy = legacyRoute(url.hash);
      // With an explicit query, #review may name a Markdown heading. Legacy
      // path prefixes remain usable for in-page navigation compatibility.
      const hasQueryRoute = url.searchParams.has("path") || url.searchParams.has("review");
      const route = legacy && !(hasQueryRoute && legacy.kind === "review")
        ? legacy : routeFromUrl(href, app.config.root);
      if (route === legacy) {
        url.hash = "";
        history.replaceState(null, "", url.href);
      }
      const requestedSession = sessions.canonical(url.searchParams.get("session"));
      if (requestedSession && requestedSession !== sessions.currentId()) {
        await loadSession(requestedSession);
        if (requestedSession !== sessions.currentId()) { writeLocation(); return; }
      }
      await openLocation(route, { widen: true });
    } catch (error) { flash(error.message, true); }
    finally { if (openingLocation === href) openingLocation = null; }
  };
  window.addEventListener("hashchange", (event) => {
    // Native hash navigation also emits popstate first; it may already have
    // consumed and canonicalized this legacy URL before hashchange arrives.
    if (event.newURL === location.href) navigateLocation(event.newURL);
  });
  window.addEventListener("popstate", () => navigateLocation());

  tabs.draw();
  drawContent();
  if (locationError) flash(locationError, true);

  /* Read by the browser tests to know the first render is complete. Cheaper
     and far more reliable than sleeping and hoping. */
  document.body.dataset.ficeloReady = "1";
}

start();
