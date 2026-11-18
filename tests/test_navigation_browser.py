"""Navigation interactions checked against the shipped DOM modules."""

import json
import time
from contextlib import contextmanager
from urllib.parse import quote

from cdp import WebSocket
from test_browser import BrowserCase
from test_modules import ModuleTest


class NavigationModuleTest(ModuleTest):
    checks = r'''
import { FolderHistory, Suggestions, ChangeWatcher, moveDestination, retargetPath, retargetMap, retargetTab, retargetWorkspace, legacyRoute, routeFromUrl, urlForRoute } from "/js/navigation.js";
import { api } from "/js/api.js";
import { showMenu, closeMenu, wireDismissal } from "/js/menu.js";

// These checks share document focus and outside-pointer listeners. Run them
// in one browser, sequentially, so one test cannot dismiss another's popup.
const reportCheck = check;
let sequence = Promise.resolve();
check = (name, fn) => reportCheck(name, () => {
  const pending = sequence.then(fn);
  sequence = pending.catch(() => {});
  return pending;
});

function mountedTree(options = {}) {
  const mount = document.createElement("ul"); document.body.append(mount);
  const tree = new Tree(mount, { onOpenFile: () => {}, ...options });
  tree.listing = async () => [];
  return tree;
}
function suggestionBox(options = {}) {
  const input = document.createElement("input"), mount = document.createElement("div");
  document.body.append(input, mount); mount.hidden = true;
  const picker = new Suggestions(input, mount, { query: async () => ({ matches: [] }), onPick: () => {}, ...options });
  return { input, mount, picker, dispose: () => { picker.dispose(); input.remove(); mount.remove(); } };
}
const keyEvent = (key, options = {}) => new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options });

check("folder history keeps visits separate, branches, and follows renames", () => {
  const history = new FolderHistory('/');
  history.visit('/docs'); history.visit('/docs/nested'); history.move(-1);
  history.visit('/docs');
  const same = history.destination(1);
  history.retarget('/docs', '/notes');
  const renamed = [history.destination(-1), history.destination(1)];
  history.visit('/work');
  const branched = [history.destination(-1), history.destination(1)];
  history.reset('/new-session');
  return eq([same, renamed, branched, history.destination(-1), history.destination(1)],
    ['/docs/nested', ['/', '/notes/nested'], ['/notes', null], null, null]);
});

check("query routes round-trip absolute paths, fragments and unrelated parameters", () => {
  const path = '/notes/résumé ?#&+.md';
  const href = urlForRoute('http://localhost/?extra=a%2Bb&review=/old&base=old&sha=old#section',
    {path}, '/workspace', 'shared');
  const url = new URL(href), route = routeFromUrl(href, '/workspace');
  return eq([route.kind, route.path, route.anchor, url.searchParams.get('session'),
    url.searchParams.get('extra'), url.searchParams.has('review'), href.includes('path=/workspace/notes/')],
    ['path', path, 'section', 'shared', 'a+b', false, true]);
});
check("query routes preserve review identity and distinguish whole commits", () => {
  const route = {kind:'commit',repo:'/project',path:'/project/a.py',base:'main',rev:'abc123'};
  const file = routeFromUrl(urlForRoute('http://localhost/',route,'/workspace'),'/workspace');
  const whole = new URL(urlForRoute('http://localhost/?path=/old',{...route,wholeCommit:true},'/workspace'));
  return eq([file.kind,file.path,file.repo,file.base,file.rev,file.wholeCommit,
    whole.searchParams.has('path'),routeFromUrl(whole.href,'/workspace').wholeCommit],
    ['commit','/project/a.py','/project','main','abc123',false,false,true]);
});
check("query paths cannot be mistaken for paths outside a restricted root", () => {
  const bad = ['?path=/outside/file.md','?review=/outside','?path=/workspace/../outside','?path=relative.md'];
  const rejected = bad.map(query=>{try{routeFromUrl('http://localhost/'+query,'/workspace');return false;}catch(_){return true;}});
  return eq([rejected,routeFromUrl('http://localhost/?path=/workspace','/workspace').path], [[true,true,true,true],'/']);
});
check("legacy hash routes stay readable and canonical URLs remove their routing hash", () =>
  eq([legacyRoute('#/notes/a%20b.md'),legacyRoute('#diff/src/a.py'),legacyRoute('#commit/abc/src/a.py'),
    legacyRoute('#bad%escape'),new URL(urlForRoute('http://localhost/#/old',{path:'/new'},'/workspace')).hash],
    [{kind:'file',path:'/notes/a b.md'},{kind:'diff',path:'/src/a.py'},
      {kind:'commit',rev:'abc',path:'/src/a.py'},null,'']));
check("explicit file queries keep headings that also name legacy routes", () => {
  const href = urlForRoute('http://localhost/?path=/workspace/note.md#review', {path:'/note.md'}, '/workspace');
  return eq([new URL(href).hash, routeFromUrl(href, '/workspace').anchor], ['#review', 'review']);
});

check("move destinations reject no-op and descendant moves", () => {
  const file = { path: "/a/file", is_dir: false }, folder = { path: "/a", is_dir: true };
  return eq([moveDestination(file, "/a"), moveDestination(folder, "/a"),
             moveDestination(folder, "/a/child"), moveDestination(file, "/"), moveDestination(folder, "/ab")],
            [null, null, null, "/file", "/ab/a"]);
});
check("renaming uses path boundaries and preserves unrelated names", () =>
  eq([retargetPath("/a/child/file", "/a", "/b"), retargetPath("/ab/file", "/a", "/b"),
      retargetMap({"/a/file": 42, "/ab/file": 9}, "/a", "/b")],
     ["/b/child/file", "/ab/file", {"/b/file": 42, "/ab/file": 9}]));
check("moving folders retargets saved workspace tabs and reading state", () => {
  const record = {id: "workspace", tabs: {"/old/file": {id: "/old/file", path: "/old/file", name: "file"}},
    order: ["/old/file"], active: "/old/file", root: "/old", expanded: ["/old/deep"],
    state: {"ficelo.scroll": {"/old/file": {line: 42}}, "ficelo.mdview": {"/old/file": "split"}}};
  const moved = retargetWorkspace(record, "/old", "/new");
  const unchanged = retargetWorkspace(record, "/unrelated", "/other") === record;
  return eq([moved.active, moved.tabs["/new/file"].path, moved.root, moved.expanded,
    moved.state["ficelo.scroll"]["/new/file"].line, moved.state["ficelo.mdview"]["/new/file"], unchanged],
    ["/new/file", "/new/file", "/new", ["/new/deep"], 42, "split", true]);
});
check("working renames leave historical filenames intact and repository moves keep their relative paths", () => {
  const tab = {id: "history", kind: "commit", rev: "abc", repo: "/project", path: "/project/old.txt"};
  const renamed = retargetTab(tab, "/project/old.txt", "/project/new.txt");
  const moved = retargetTab(tab, "/project", "/relocated");
  return eq([renamed.path, moved.path, moved.repo], ["/project/old.txt", "/relocated/old.txt", "/relocated"]);
});
check("creation uses selected expanded folder or explorer root", () => {
  const tree = mountedTree(); tree.root = "/project";
  tree.expanded = new Set(["/project", "/project/open", "/outside"]);
  tree.select("/project/open", true); const expanded = tree.creationFolder();
  tree.select("/project/closed", true); const collapsed = tree.creationFolder();
  tree.select("/project/open/file", false); const file = tree.creationFolder();
  tree.select("/outside", true); const outside = tree.creationFolder();
  tree.mount.remove();
  return eq([expanded, collapsed, file, outside], ["/project/open", "/project", "/project", "/project"]);
});
check("tree retargets every descendant and prunes vanished expanded folders", async () => {
  const tree = mountedTree(); tree.root = "/old/sub"; tree.selected = "/old/sub/file";
  tree.expanded = new Set(["/old", "/old/sub", "/old/sub/deep", "/older"]);
  tree.retarget("/old", "/new");
  const moved = [tree.root, tree.selected, [...tree.expanded]];
  await tree.refreshVisible(["/new/sub/deep"]);
  const watched = tree.watchedPaths(); tree.mount.remove();
  return eq([moved, watched], [["/new/sub", "/new/sub/file", ["/new", "/new/sub", "/new/sub/deep", "/older"]], ["/new/sub"]]);
});
check("folder double-click does not toggle the second click", async () => {
  const tree = mountedTree(); let toggles = 0, rooted = null;
  tree.toggle = async () => { toggles++; }; tree.rootHere = async (path) => { rooted = path; };
  const row = tree.row({path: "/folder", name: "folder", is_dir: true}); tree.mount.append(row);
  row.dispatchEvent(new MouseEvent("click", { detail: 1 }));
  row.dispatchEvent(new MouseEvent("click", { detail: 2 }));
  row.dispatchEvent(new MouseEvent("dblclick", { detail: 2 }));
  tree.mount.remove(); return eq([toggles, rooted], [1, "/folder"]);
});
check("double-click still roots after expansion replaces the row", async () => {
  const tree = mountedTree(); let toggles = 0, rooted = null;
  tree.toggle = async () => { toggles++; }; tree.rootHere = async (path) => { rooted = path; };
  const entry = {path: "/folder", name: "folder", is_dir: true};
  tree.row(entry).dispatchEvent(new MouseEvent("click", { detail: 1 }));
  tree.row(entry).dispatchEvent(new MouseEvent("click", { detail: 1 }));
  tree.mount.remove(); return eq([toggles, rooted], [1, "/folder"]);
});
check("tree keyboard navigation does not enter an empty sibling", async () => {
  const tree = mountedTree(); tree.expanded.add("/empty");
  tree.listing = async (path) => path === "/" ? [
    { path: "/empty", name: "empty", is_dir: true }, { path: "/next", name: "next", is_dir: false }
  ] : [];
  await tree.draw();
  const row = tree.mount.querySelector(".row"); row.focus();
  row.dispatchEvent(keyEvent("ArrowRight"));
  const stayed = document.activeElement === row;
  row.dispatchEvent(keyEvent("ArrowDown"));
  const next = document.activeElement.dataset.path;
  tree.mount.remove(); return eq([stayed, next], [true, "/next"]);
});
check("refresh keeps keyboard focus on the same file", async () => {
  const tree = mountedTree(); tree.listing = async () => [{ path: "/file", name: "file", is_dir: false }];
  await tree.draw(); tree.mount.querySelector(".row").focus();
  await tree.draw(); const selected = document.activeElement.dataset.path;
  tree.mount.remove(); return eq(selected, "/file");
});
check("drop delegates exactly one valid move and clears drag state", async () => {
  const moved = []; const tree = mountedTree({ onMove: (entry, folder) => moved.push([entry.path, folder]) });
  tree.writable = true; tree.dragged = {path: "/a/file", is_dir: false};
  const invalid = new Event("drop", {cancelable: true}); await tree.drop(invalid, "/a");
  const valid = new Event("drop", {cancelable: true}); await tree.drop(valid, "/b");
  const dragging = tree.dragged; tree.mount.remove();
  return eq([moved, invalid.defaultPrevented, valid.defaultPrevented, dragging], [[["/a/file", "/b"]], false, true, null]);
});
check("stale listings cannot overwrite the watcher baseline or evict a fresh request", async () => {
  const original = api.tree; const responses = []; const primed = [];
  const mount = document.createElement("ul");
  const tree = new Tree(mount, { onOpenFile: () => {}, onListing: (...args) => primed.push(args) });
  api.tree = () => new Promise((resolve, reject) => responses.push({resolve, reject}));
  try {
    const old = tree.listing("/"); tree.refresh("/"); const fresh = tree.listing("/");
    responses[1].resolve({entries: [], stamp: "new"}); await fresh;
    responses[0].resolve({entries: [], stamp: "old"}); await old;
    tree.refresh("/"); const failure = tree.listing("/").catch(() => {});
    tree.refresh("/"); const newest = tree.listing("/");
    responses[2].reject(new Error("gone")); await failure;
    const retained = tree.listings.has("/");
    responses[3].resolve({entries: [], stamp: "newest"}); await newest;
    return eq([primed, retained], [[["/", "new"], ["/", "newest"]], true]);
  } finally { api.tree = original; }
});
check("suggestions discard a stale response after typing changes", async () => {
  const pending = {};
  const box = suggestionBox({query: (value) => new Promise((resolve) => { pending[value] = resolve; })});
  try {
    box.input.value = "old"; const old = box.picker.run();
    box.input.value = "new"; const fresh = box.picker.run();
    pending.new({matches: [{path: "/new"}]}); await fresh;
    pending.old({matches: [{path: "/old"}]}); await old;
    return eq(box.picker.rows.map((row) => row.path), ["/new"]);
  } finally { box.dispose(); }
});
check("suggestions select first result and keep keyboard choice as deep results arrive", async () => {
  let rows = [{path: "/first"}, {path: "/second"}], picked = null;
  const box = suggestionBox({query: async () => ({matches: rows}), onPick: (row) => {picked = row.path;}});
  try {
    box.input.value = "s"; await box.picker.run();
    const first = box.picker.active;
    box.input.dispatchEvent(keyEvent("ArrowDown"));
    rows = [{path: "/new"}, ...rows]; await box.picker.run();
    box.input.dispatchEvent(keyEvent("Enter"));
    return eq([first, picked, box.mount.hidden], [0, "/second", true]);
  } finally { box.dispose(); }
});
check("Tab completes a directory with a trailing slash", async () => {
  const box = suggestionBox({query: async () => ({matches: [{path: "/folder", is_dir: true}]})});
  try {
    await box.picker.run(); const key = keyEvent("Tab"); box.input.dispatchEvent(key);
    return eq([box.input.value, key.defaultPrevented], ["/folder/", true]);
  } finally { box.dispose(); }
});
check("hidden suggestion requests cannot reopen the popup", async () => {
  let answer; const box = suggestionBox({query: () => new Promise((resolve) => {answer = resolve;})});
  try {
    const request = box.picker.run(); box.picker.hide(); answer({matches: [{path: "/late"}]}); await request;
    return eq(box.mount.hidden, true);
  } finally { box.dispose(); }
});
check("watcher compares against the listing baseline and reports deletions", async () => {
  const changes = [], visible = [];
  const watcher = new ChangeWatcher({paths: () => ["/folder", "/file", "/folder"],
    snapshot: async () => ({"/folder": "new", "/file": null}),
    onVisible: () => visible.push(true), onChange: (change) => changes.push(change)});
  watcher.prime("/folder", "old"); watcher.prime("/file", "saved"); watcher.started = true;
  try {
    await watcher.check(true); await watcher.check();
    return eq([visible.length, changes.map(({changed, gone}) => [changed, gone])], [1, [[["/folder"], ["/file"]]]]);
  } finally { watcher.stop(); }
});
check("watcher does not overlap stat requests and retries after a pause", async () => {
  let paused = true, calls = 0, release;
  const watcher = new ChangeWatcher({paths: () => ["/folder"], paused: () => paused,
    snapshot: () => {calls++; return new Promise((resolve) => { release = resolve; });}, onChange: () => {}});
  watcher.started = true;
  try {
    await watcher.check(); const scheduled = Boolean(watcher.timer);
    paused = false; const active = watcher.check(); await watcher.check();
    release({"/folder": "ready"}); await active;
    return eq([scheduled, calls, watcher.running], [true, 1, false]);
  } finally { watcher.stop(); }
});
check("watcher retries failed refreshes without losing newer listing baselines", async () => {
  let attempts = 0;
  const watcher = new ChangeWatcher({paths: () => ["/file"], snapshot: async () => ({"/file": "changed"}),
    onChange: () => { if (++attempts === 1) throw new Error("temporary read failure"); watcher.prime("/file", "read-baseline"); }});
  watcher.prime("/file", "original"); watcher.started = true;
  try {
    await watcher.check(); const failed = watcher.known.get("/file");
    await watcher.check();
    return eq([attempts, failed, watcher.known.get("/file")], [2, "original", "read-baseline"]);
  } finally { watcher.stop(); }
});
check("context menu arrows skip disabled actions and Escape restores focus", () => {
  const button = document.createElement("button"); document.body.append(button); button.focus();
  const menu = showMenu(0, 0, [{label: "first", run: () => {}}, {label: "disabled"}, {label: "last", run: () => {}}]);
  const first = document.activeElement.textContent;
  menu.dispatchEvent(keyEvent("ArrowDown")); const last = document.activeElement.textContent;
  menu.dispatchEvent(keyEvent("Escape")); const restored = document.activeElement === button;
  closeMenu(); button.remove(); return eq([first, last, restored], ["first", "last", true]);
});
check("menu dismissal uses outside press rather than a selection release", () => {
  wireDismissal(); const menu = showMenu(0, 0, [{label: "copy", run: () => {}}]);
  document.body.dispatchEvent(new MouseEvent("click", {bubbles: true})); const releaseKept = menu.isConnected;
  document.body.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true}));
  return eq([releaseKept, menu.isConnected], [true, false]);
});
'''

    def test_navigation_interactions(self):
        self.assertAllPass()


class BreadcrumbTabTest(BrowserCase):
    writable = True
    parent = "/projects & drafts"
    folder = parent + "/résumé #1+%"
    source = folder + "/note.txt"

    def prepare(self):
        (self.root / self.folder[1:]).mkdir(parents=True)
        (self.root / self.source[1:]).write_text("original\n")

    def page_ids(self):
        return {item["targetId"] for item in self.browser._call("Target.getTargets")["targetInfos"]
                if item["type"] == "page"}

    @contextmanager
    def modified_crumb(self, path, modifiers):
        before = self.page_ids()
        self.browser._call("Page.bringToFront")
        point = self.browser.evaluate("""(() => {
          const crumb = [...document.querySelectorAll('.crumb')].find(node => node.title === """ + json.dumps(path) + """);
          crumb.scrollIntoView({block:'nearest',inline:'nearest'});
          const box = crumb.getBoundingClientRect();
          return {x:box.x + box.width / 2, y:box.y + box.height / 2};
        })()""")
        for kind in ("mousePressed", "mouseReleased"):
            self.browser._call("Input.dispatchMouseEvent", {
                "type": kind, **point, "button": "left", "clickCount": 1, "modifiers": modifiers,
            })
        deadline = time.monotonic() + 10
        opened = self.page_ids() - before
        while not opened and time.monotonic() < deadline:
            time.sleep(0.1)
            opened = self.page_ids() - before
        self.assertEqual(len(opened), 1, "modified breadcrumb click should open one browser tab")
        target = opened.pop()
        original = self.browser.ws
        connection = None
        try:
            connection = WebSocket(f"ws://127.0.0.1:{self.browser.port}/devtools/page/{target}")
            self.browser.ws = connection
            for domain in ("Runtime", "Page", "Log"):
                self.browser._call(domain + ".enable")
            self.browser.wait_for("document.body.dataset.ficeloReady === '1'", timeout=60)
            yield
        finally:
            if connection:
                connection.close()
            self.browser.ws = original
            self.browser._call("Target.closeTarget", {"targetId": target})
            self.browser._call("Page.bringToFront")

    def assert_empty_folder_session(self, folder, previous_ids):
        result = self.browser.evaluate("""(async () => {
          const sessions = await import('/js/sessions.js');
          await (await import('/js/util.js')).flushStore();
          const id = sessions.currentId(), url = new URL(location);
          return {id, root:sessions.readAll().find(row => row.id === id)?.root,
            tabs:document.querySelectorAll('#tabs .tab').length,
            name:document.querySelector('#sessionName').value,
            path:url.searchParams.get('path'), keys:[...url.searchParams.keys()].sort(),
            hash:url.hash, isolated:window.opener === null};
        })()""")
        self.assertRegex(result["id"], r"^[a-z0-9]{4}$")
        self.assertNotIn(result["id"], previous_ids)
        self.assertEqual({key: value for key, value in result.items() if key != "id"}, {
            "root": folder, "tabs": 0, "name": "", "path": str(self.root) + (folder if folder != "/" else ""),
            "keys": ["path", "session"], "hash": "", "isolated": True,
        })
        saved = self.server.settings.read()["ficelo.workspace." + result["id"]]
        self.assertEqual((saved["root"], saved.get("tabs", {}), saved["order"], saved["active"]), (folder, {}, [], None))
        return result["id"]

    def test_modified_breadcrumbs_open_empty_sessions_and_preserve_unsaved_edits(self):
        self.loaded("/?path=" + quote(str(self.root / self.source[1:]), safe="/"))
        self.assertEditorWritable(True)
        self.browser.evaluate("[...document.querySelectorAll('.crumb')].find(node => node.title === " + json.dumps(self.parent) + ").click()")
        self.browser.wait_for("document.querySelector('.crumb.is-root')?.title === " + json.dumps(self.parent))
        self.assertEqual(len(self.page_ids()), 1, "ordinary clicks stay in the current browser tab")
        self.browser.evaluate("window.keptModel = monaco.editor.getEditors()[0].getModel(); keptModel.setValue('unsaved edits\\n')")
        self.waitFor(".tab.is-dirty")
        original_url = self.browser.evaluate("location.href")
        original_id = self.browser.evaluate("new URL(location).searchParams.get('session')")
        seen = {original_id}
        for modifiers, folder in ((4, "/"), (4, self.parent), (4, self.folder), (2, self.folder)):
            with self.subTest(modifiers=modifiers, folder=folder):
                with self.modified_crumb(folder, modifiers):
                    seen.add(self.assert_empty_folder_session(folder, seen))
                self.assertEqual(self.browser.evaluate("location.href"), original_url)
                self.assertEqual(self.browser.evaluate("""[monaco.editor.getEditors()[0].getModel() === keptModel,
                  keptModel.getValue(), document.querySelectorAll('#tabs .tab').length,
                  document.querySelector('#tabs .tab.is-dirty')?.dataset.path]"""), [True, "unsaved edits\n", 1, self.source])
                self.assertEqual(self.browser.evaluate("""(async () => {const s = await import('/js/sessions.js');
                  return s.readAll().find(row => row.id === s.currentId()).root;})()"""), self.parent)
        self.assertEqual((self.root / self.source[1:]).read_text(), "original\n")
        self.assertNoPageErrors()

    def test_empty_breadcrumb_session_drops_old_routes_and_survives_reload(self):
        self.loaded("/?path=" + quote(str(self.root / self.folder[1:]), safe="/"))
        original_id = self.browser.evaluate("new URL(location).searchParams.get('session')")
        # Stale review/copy parameters and anchors must not leak into a folder tab.
        self.browser.evaluate("""const url = new URL(location);
          for (const [key, value] of [['review','/old/repo'],['base','topic'],['sha','deadbeef'],['copy',url.searchParams.get('session')]]) url.searchParams.set(key,value);
          url.hash = 'old-heading'; history.replaceState(null,'',url);""")
        original_url = self.browser.evaluate("location.href")
        with self.modified_crumb(self.folder, 4):
            created = self.assert_empty_folder_session(self.folder, {original_id})
            route = self.browser.evaluate("location.pathname + location.search + location.hash")
            self.loaded(route)
            self.assertEqual(self.assert_empty_folder_session(self.folder, {original_id}), created)
        self.assertEqual(self.browser.evaluate("location.href"), original_url)
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 0)
        self.assertNoPageErrors()


class NavigationIntegrationTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "docs/nested").mkdir(parents=True)
        (self.root / "docs/note.md").write_text("# A note\n")
        (self.root / "work").mkdir()
        (self.root / "work/note.txt").write_text("original\n")

    def wait_folder(self, path):
        self.browser.wait_for(
            "(async () => {const s = await import('/js/sessions.js');"
            "return s.readAll().find(row => row.id === s.currentId())?.root === "
            + json.dumps(path) + ";})()",
            what="the explorer root to become " + path,
        )

    def enter_folder(self, path):
        self.browser.evaluate(
            "document.querySelector('#tree .row[data-path=\"" + path
            + "\"]').dispatchEvent(new MouseEvent('dblclick', {bubbles:true}))"
        )
        self.wait_folder(path)

    def test_folder_history_preserves_dirty_files_and_discards_forward_branches(self):
        self.loaded("/#/work/note.txt")
        self.browser.evaluate("window.originalModel = monaco.editor.getEditors()[0].getModel(); originalModel.setValue('unsaved edit\\n')")
        self.waitFor(".tab.is-dirty")
        self.assertEqual(self.browser.evaluate(
            "[document.querySelector('#folderBack').disabled, document.querySelector('#folderForward').disabled]"
        ), [True, True])
        self.enter_folder("/docs")
        self.enter_folder("/docs/nested")
        self.browser.click("#folderBack")
        self.wait_folder("/docs")
        self.waitFor('#tree .row[data-path="/docs/note.md"]')
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#tree .row[data-path=\"/work\"]')"))
        self.browser.click("#folderForward")
        self.wait_folder("/docs/nested")
        self.browser.click("#folderBack")
        self.wait_folder("/docs")
        self.browser.click("#homeButton")
        self.wait_folder("/")
        self.assertTrue(self.browser.evaluate("document.querySelector('#folderForward').disabled"))
        self.browser.click("#folderBack")
        self.wait_folder("/docs")
        self.browser.click("#folderForward")
        self.wait_folder("/")
        self.assertEqual(self.browser.evaluate(
            "[monaco.editor.getEditors()[0].getModel() === originalModel, originalModel.getValue(),"
            "document.querySelector('#tabs .tab.is-dirty')?.dataset.path]"
        ), [True, "unsaved edit\n", "/work/note.txt"])
        self.assertEqual((self.root / "work/note.txt").read_text(), "original\n")
        self.assertNoPageErrors()

    def test_folder_history_failure_keeps_current_root_and_allows_retry(self):
        self.loaded()
        self.enter_folder("/docs")
        self.browser.click("#homeButton")
        self.wait_folder("/")
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'); window.originalTree = api.tree;
          api.tree = (path, ...args) => path === '/docs'
            ? Promise.reject(new Error('Folder unavailable')) : originalTree(path, ...args);
        })()""")
        self.browser.click("#folderBack")
        self.browser.wait_for("document.querySelector('#flash').textContent === 'Folder unavailable'")
        self.wait_folder("/")
        self.assertEqual(self.browser.evaluate(
            "[document.querySelector('#folderBack').disabled, document.querySelector('#folderForward').disabled]"
        ), [False, True])
        self.waitFor('#tree .row[data-path="/work"]')
        self.browser.evaluate("(async () => { (await import('/js/api.js')).api.tree = originalTree; })()")
        self.browser.click("#folderBack")
        self.wait_folder("/docs")
        self.assertNoPageErrors()

    def test_folder_history_resets_for_a_new_session(self):
        self.loaded()
        self.enter_folder("/docs")
        self.browser.click("#newSession")
        self.browser.wait_for("document.querySelector('#folderBack').disabled")
        self.wait_folder("/docs")
        self.assertTrue(self.browser.evaluate("document.querySelector('#folderForward').disabled"))
        self.assertNoPageErrors()

    def test_folder_history_ignores_a_slow_response_after_newer_navigation(self):
        self.loaded()
        self.enter_folder("/docs")
        self.browser.click("#homeButton")
        self.wait_folder("/")
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'); const original = api.tree;
          api.tree = (path, ...args) => path === '/docs' && !window.releaseFolder
            ? new Promise(resolve => { window.releaseFolder = () => resolve(original(path, ...args)); })
            : original(path, ...args);
        })()""")
        self.browser.click("#folderBack")
        self.browser.wait_for("!!window.releaseFolder && document.querySelector('#folderBack').disabled")
        self.browser.click("#homeButton")
        self.browser.evaluate("releaseFolder()")
        self.browser.wait_for("!document.querySelector('#folderBack').disabled")
        self.wait_folder("/")
        self.assertTrue(self.browser.evaluate("document.querySelector('#folderForward').disabled"))
        self.browser.click("#folderBack")
        self.wait_folder("/docs")
        self.assertNoPageErrors()

    def test_hidden_checkbox_overrides_the_server_default_for_tree_and_search(self):
        self.server.config.show_hidden = True
        (self.root / ".hidden-note.txt").write_text("hidden file\n")
        self.loaded()
        self.waitFor('#tree .row[data-path="/.hidden-note.txt"]')
        self.browser.evaluate("""(() => {const input=document.querySelector('#search');
          input.value='hidden-note';input.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        self.browser.wait_for("document.querySelector('#results').textContent.includes('.hidden-note.txt')")
        self.browser.click("#clearSearch")
        self.browser.click("#showHiddenFiles")
        self.browser.wait_for("!document.querySelector('#tree .row[data-path=\"/.hidden-note.txt\"]')")
        self.browser.evaluate("""(() => {const input=document.querySelector('#search');
          input.value='hidden-note';input.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        self.browser.wait_for("document.querySelector('#results').textContent === 'No matching paths'")
        self.assertFalse(self.browser.evaluate("document.querySelector('#showHiddenFiles').checked"))
        self.assertNoPageErrors()

    def test_query_links_restore_the_session_then_open_the_requested_file_or_folder(self):
        self.server.settings.put("ficelo.workspace.shared", {
            "id": "shared", "name": "Shared session", "root": "/", "expanded": ["/"],
            "tabs": {"/work/note.txt": {"id": "/work/note.txt", "path": "/work/note.txt"}},
            "order": ["/work/note.txt"], "active": "/work/note.txt",
        })
        (self.root / "docs/note.md").write_text("# A note\n\n" + "A paragraph.\n\n" * 100 + "## Review\n\nEnd.\n")
        path = str(self.root / "docs/note.md")
        self.loaded("/?session=shared&path=" + quote(path, safe="/") + "#review")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected=true]')?.dataset.path === '/docs/note.md'")
        self.browser.wait_for("document.querySelector('#preview').scrollTop > 100")
        self.assertEqual(self.browser.evaluate("[new URL(location).searchParams.get('session'),new URL(location).searchParams.get('path'),location.hash]"), ["shared", path, "#review"])
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 2)
        self.assertFalse(self.browser.evaluate("(async()=>new URL((await import('/js/sessions.js')).link('another')).searchParams.has('path'))()"))

        name = "résumé ?#&+.md"
        (self.root / name).write_text("# Encoded path\n")
        path = str(self.root / name)
        self.loaded("/?session=shared&path=" + quote(path, safe="/"))
        self.assertEqual(self.browser.text("#preview h1"), "Encoded path")
        self.assertEqual(self.browser.evaluate("new URL(location).searchParams.get('path')"), path)
        self.assertTrue(self.browser.evaluate("location.search.includes('path=/') && location.hash === ''"))

        folder = str(self.root / "docs")
        self.loaded("/?session=shared&path=" + quote(folder, safe="/"))
        self.waitFor('#tree .row[data-path="/docs/note.md"]')
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#tabs [aria-selected=true]')"))
        self.assertEqual(self.browser.evaluate("new URL(location).searchParams.get('path')"), folder)
        self.browser.evaluate("location.hash='#/work/note.txt'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected=true]')?.dataset.path === '/work/note.txt'")
        self.browser.evaluate("history.pushState(null, '', " + json.dumps("/?session=shared&path=" + quote(folder, safe="/")) + "); window.dispatchEvent(new PopStateEvent('popstate'))")
        self.browser.wait_for("document.querySelector('.crumb.is-root')?.textContent === 'docs' && !document.querySelector('#tabs [aria-selected=true]')")
        self.assertEqual(self.browser.evaluate("new URL(location).searchParams.get('path')"), folder)
        self.assertNoPageErrors()

    def test_legacy_history_navigation_restores_a_different_session_with_the_same_hash(self):
        for ident, path in (("first", "/docs/note.md"), ("second", "/work/note.txt")):
            self.server.settings.put("ficelo.workspace." + ident, {
                "id": ident, "name": ident, "root": "/", "expanded": ["/"],
                "tabs": {path: {"id": path, "path": path}}, "order": [path], "active": path,
            })
        self.loaded("/?session=second")
        self.browser.evaluate("history.pushState(null, '', '/?session=first#/docs/note.md'); history.pushState(null, '', '/?session=second#/docs/note.md'); history.back()")
        self.browser.wait_for("location.hash === '' && document.querySelector('#sessionName').value === 'first' && document.querySelector('#tabs [aria-selected=true]')?.dataset.path === '/docs/note.md'")
        self.assertEqual(self.browser.evaluate("[new URL(location).searchParams.get('session'),location.hash]"), ["first", ""])
        self.browser.evaluate("history.forward()")
        self.browser.wait_for("location.hash === '' && document.querySelector('#sessionName').value === 'second' && document.querySelector('#tabs [data-path=\"/work/note.txt\"]') && document.querySelector('#tabs [aria-selected=true]')?.dataset.path === '/docs/note.md'")
        self.assertEqual(self.browser.evaluate("[new URL(location).searchParams.get('session'),document.querySelectorAll('#tabs .tab').length,location.hash]"), ["second", 2, ""])
        self.assertNoPageErrors()

    def test_path_completion_refresh_and_selected_folder_creation(self):
        self.loaded()
        self.browser.evaluate("""const input = document.getElementById('search');
          input.focus(); input.value = '/docs/nest'; input.dispatchEvent(new Event('input', {bubbles: true}));""")
        self.browser.wait_for("Array.from(document.querySelectorAll('#results .result-path')).some(row => row.textContent === '/docs/nested')")
        self.browser.evaluate("document.getElementById('search').dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true}))")
        self.browser.wait_for("document.getElementById('search').value === '/docs/nested/'", what="folder completion")
        self.browser.click("#clearSearch")
        self.assertEqual(self.browser.evaluate("""[
          document.getElementById('search').value,
          document.getElementById('results').hidden,
          document.getElementById('tree').hidden
        ]"""), ["", True, False])
        self.browser.evaluate("""(() => { const input = document.getElementById('search');
          input.value = '/docs'; input.dispatchEvent(new Event('input', {bubbles: true}));
          input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); })()""")
        self.waitFor('#tree .row[data-path="/docs/nested"]')
        self.browser.click(".crumb")
        self.browser.wait_for("document.querySelector('.crumb.is-root')?.textContent === " + json.dumps(self.root.name))
        self.browser.evaluate("""(() => { const input = document.getElementById('search');
          input.value = '/docs/'; input.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        self.browser.wait_for("Array.from(document.querySelectorAll('#results .result-path')).some(row => row.textContent === '/docs/nested')")
        self.browser.evaluate("document.getElementById('search').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}))")
        self.browser.wait_for("""document.getElementById('search').value === '' &&
          document.querySelector('.crumb.is-root')?.textContent === 'docs' &&
          !document.getElementById('tree').hidden""", what="Enter to open the typed folder after suggestions arrive")
        (self.root / "docs/arrived.txt").write_text("created elsewhere")
        self.waitFor('#tree .row[data-path="/docs/arrived.txt"]', timeout=8, what="the watcher to refresh the folder")
        self.browser.click('#tree .row[data-path="/docs/nested"]')
        self.browser.click("#newFile")
        self.waitFor("dialog.app-dialog[open] input")
        self.browser._call("Input.insertText", {"text": "created.md"})
        self.browser.click('dialog[open] button[type="submit"]')
        self.waitFor('.tab[data-path="/docs/nested/created.md"]', what="the new file in the selected folder")
        self.browser.wait_for("!document.getElementById('editorHost').hidden && monaco.editor.getEditors()[0].getRawOptions().readOnly === false")
        self.assertTrue((self.root / "docs/nested/created.md").is_file())
        self.assertNoPageErrors()

    def test_folder_rename_keeps_unsaved_descendant_models_and_old_names_can_reopen(self):
        self.loaded("/#/work/note.txt")
        self.assertEditorWritable(True)
        self.browser.evaluate("window.movedModel = monaco.editor.getEditors()[0].getModel(); movedModel.setValue('unsaved edit\\n')")
        self.waitFor(".tab.is-dirty")
        self.browser.evaluate("""document.querySelector('#tree .row[data-path="/work"]').dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 80, clientY: 160}));
          Array.from(document.querySelectorAll('.menu-item')).find(button => button.textContent === 'Rename').click();""")
        self.waitFor("dialog.app-dialog[open] input")
        self.assertEqual(self.browser.text("dialog h2"), "Rename folder")
        self.assertEqual(self.browser.evaluate("document.querySelector('dialog input').value"), "work")
        self.browser._call("Input.insertText", {"text": "renamed"})
        self.browser.click('dialog[open] button[type="submit"]')
        self.waitFor('.tab[data-path="/renamed/note.txt"].is-dirty', what="the dirty tab to follow its folder")
        self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getModel() === movedModel"), True)
        self.assertEqual(self.browser.evaluate("movedModel.getValue()"), "unsaved edit\n")
        self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 's', metaKey: true, bubbles: true}))")
        self.browser.wait_for("!document.querySelector('.tab.is-dirty')", what="the moved buffer to save")
        self.assertEqual((self.root / "renamed/note.txt").read_text(), "unsaved edit\n")
        (self.root / "work").mkdir()
        (self.root / "work/note.txt").write_text("new file\n")
        self.browser.evaluate("location.hash = '#/work/note.txt'")
        self.waitFor('.tab[data-path="/work/note.txt"]')
        self.browser.wait_for("monaco.editor.getEditors()[0].getModel()?.getValue() === 'new file\\n'", what="a separate model for the reused filename")
        self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getModel() === movedModel"), False)
        self.assertNoPageErrors()

    def test_failed_tree_refresh_retries_but_file_reload_waits_for_a_click(self):
        self.loaded("/#/work/note.txt")
        self.browser.evaluate("""const originalFetch = window.fetch;
          window.failedTree = 0; window.failedRead = 0; window.successfulReads = 0; window.failNextRead = false;
          window.fetch = (url, options) => {
            const value = String(url);
            if (value.includes('/api/tree?') && !window.failedTree) {
              window.failedTree++;
              return Promise.resolve(new Response(JSON.stringify({error: 'temporary disconnect'}), {status: 503}));
            }
            if (value.includes('/api/file?') && (!window.failedRead || window.failNextRead)) {
              window.failedRead++;
              window.failNextRead = false;
              return Promise.resolve(new Response(JSON.stringify({error: 'temporary disconnect'}), {status: 503}));
            }
            return originalFetch(url, options).then(response => {
              if (value.includes('/api/file?')) window.successfulReads++;
              return response;
            });
          };""")
        (self.root / "arrived.txt").write_text("new entry\n")
        (self.root / "work/note.txt").write_text("new contents\n")
        self.waitFor('#tree .row[data-path="/arrived.txt"]', timeout=10, what="a failed folder refresh to retry")
        self.waitFor("#reloadFile.is-stale")
        self.assertEqual(self.editorText(), "original")
        # The background comparison retries its failure, without replacing
        # the displayed buffer or reporting an explicit reload error.
        self.browser.wait_for("window.successfulReads > 0")
        self.assertEqual(self.browser.evaluate("[window.failedTree, window.failedRead]"), [1, 1])
        self.assertNotEqual(self.browser.text("#flash"), "temporary disconnect")
        self.browser.evaluate("window.failNextRead = true")
        self.browser.click("#reloadFile")
        self.browser.wait_for("document.querySelector('#flash').textContent === 'temporary disconnect'")
        self.assertEqual(self.editorText(), "original")
        self.assertTrue(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.browser.click("#reloadFile")
        self.browser.wait_for("monaco.editor.getEditors()[0].getModel()?.getValue() === 'new contents\\n'", what="the explicit retry to reload the file")
        self.assertEqual(self.browser.evaluate("[window.failedTree, window.failedRead]"), [1, 2])
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))

    def test_disk_changes_mark_code_and_inactive_markdown_without_reloading(self):
        self.loaded("/#/docs/note.md")
        self.waitFor("#preview h1")
        self.browser.evaluate("location.hash='#/work/note.txt'")
        self.assertEqual(self.editorText(), "original")
        self.browser.evaluate("""(() => {
          window.heldModel=monaco.editor.getEditors()[0].getModel();window.fileReads=0;window.watchReplies=0;
          const fetchOriginal=window.fetch;
          window.fetch=async(url,options)=>{
            if(String(url).includes('/api/file?'))fileReads++;
            const response=await fetchOriginal(url,options);
            if(String(url).includes('/api/watch'))watchReplies++;
            return response;
          };
        })()""")
        (self.root / "docs/note.md").write_text("# Updated note\n")
        (self.root / "work/note.txt").write_text("new contents\n")
        self.waitFor("#reloadFile.is-stale")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("watchReplies>=2")
        self.browser.wait_for("fileReads > 0", what="the changed content to be read for its direction")
        self.assertEqual(self.browser.evaluate("[heldModel.getValue(),heldModel===monaco.editor.getEditors()[0].getModel()]"), ["original\n", True])
        for _ in range(2):
            color = self.browser.evaluate("getComputedStyle(document.querySelector('#reloadFile')).backgroundColor.match(/[0-9]+/g).map(Number)")
            self.assertGreater(color[1], max(color[0], color[2]), "the stale refresh button has a green background")
            self.toggle_theme()

        self.browser.evaluate("location.hash='#/docs/note.md'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected=true]')?.dataset.path==='/docs/note.md'")
        self.assertEqual(self.browser.text("#preview h1"), "A note")
        self.assertTrue(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.browser.click("#reloadFile")
        self.browser.wait_for("document.querySelector('#preview h1')?.textContent==='Updated note'")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))

        self.browser.evaluate("location.hash='#/work/note.txt'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected=true]')?.dataset.path==='/work/note.txt'")
        self.assertEqual(self.editorText(), "original")
        self.browser.click("#reloadFile")
        self.browser.wait_for("monaco.editor.getEditors()[0].getModel()?.getValue()==='new contents\\n'")
        (self.root / "work/note.txt").write_text("another external change\n")
        self.waitFor("#reloadFile.is-stale")
        self.assertEqual(self.editorText(), "new contents")
        self.browser.click("#reloadFile")
        self.browser.wait_for("monaco.editor.getEditors()[0].getModel()?.getValue()==='another external change\\n'")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.assertNoPageErrors()

    def test_deleted_file_keeps_its_buffer_until_recreated_and_reloaded(self):
        self.loaded("/#/work/note.txt")
        self.waitFor('#tree .row[data-path="/work/note.txt"]')
        (self.root / "work/note.txt").unlink()
        self.browser.wait_for("!document.querySelector('#tree .row[data-path=\"/work/note.txt\"]')")
        self.assertEqual(self.editorText(), "original")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        (self.root / "work/note.txt").write_text("recreated\n")
        self.waitFor("#reloadFile.is-stale")
        self.assertEqual(self.editorText(), "original")
        self.browser.click("#reloadFile")
        self.browser.wait_for("monaco.editor.getEditors()[0].getModel()?.getValue()==='recreated\\n'")
        self.assertNoPageErrors()


class TabMenuIntegrationTest(BrowserCase):
    writable = True

    def prepare(self):
        for name in ("alpha.txt", "beta.txt", "gamma.txt", "delta.txt"):
            (self.root / name).write_text(name + "\n")
        (self.root / "folder").mkdir()
        (self.root / "folder/child.txt").write_text("child\n")

    def open_file(self, path):
        self.browser.evaluate("location.hash = '#' + " + json.dumps(path))
        self.browser.wait_for("document.querySelector('#tabs .tab[aria-selected=true]')?.dataset.path === " + json.dumps(path))

    def menu(self, path, selector="#tabs .tab"):
        self.browser.evaluate("Array.from(document.querySelectorAll(" + json.dumps(selector) + ")).find(node => node.dataset.path === " + json.dumps(path) + ").dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, clientX: 90, clientY: 140}))")

    def action(self, label):
        self.browser.evaluate("Array.from(document.querySelectorAll('.menu-item')).find(button => button.textContent === " + json.dumps(label) + ").click()")

    def dialog(self, accept):
        self.waitFor("dialog[open]")
        selector = "dialog[open] button[type=submit]" if accept else "dialog[open] button[type=button]"
        self.browser.click(selector)

    def test_counted_close_to_the_right_asks_once_and_cancel_keeps_all_buffers(self):
        self.loaded("/#/alpha.txt")
        self.open_file("/beta.txt")
        self.browser.evaluate("window.betaModel = monaco.editor.getEditors()[0].getModel(); betaModel.setValue('beta edits')")
        self.open_file("/gamma.txt")
        self.browser.evaluate("window.gammaModel = monaco.editor.getEditors()[0].getModel(); gammaModel.setValue('gamma edits')")
        self.menu("/alpha.txt")
        self.action("Close 2 to the Right")
        self.waitFor("dialog[open]")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('dialog[open]').length"), 1)
        self.assertIn("2 tabs have unsaved changes", self.browser.evaluate("document.querySelector('dialog p').textContent"))
        self.dialog(False)
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 3)
        self.assertEqual(self.browser.evaluate("[betaModel.getValue(), gammaModel.getValue()]"), ["beta edits", "gamma edits"])
        self.menu("/alpha.txt")
        self.action("Close 2 to the Right")
        self.dialog(True)
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 1")
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs .tab').dataset.path"), "/alpha.txt")
        self.assertEqual(self.browser.evaluate("[betaModel.isDisposed(), gammaModel.isDisposed(), document.querySelectorAll('dialog[open]').length]"), [True, True, 0])
        self.assertNoPageErrors()

    def test_close_right_and_move_actions_follow_tab_order_and_boundaries(self):
        self.loaded("/#/alpha.txt")
        for path in ("/beta.txt", "/gamma.txt", "/delta.txt"):
            self.open_file(path)
        self.menu("/beta.txt")
        self.action("Close 2 to the Right")
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 2")
        self.menu("/beta.txt")
        self.assertEqual(self.browser.evaluate("Array.from(document.querySelectorAll('.menu-item')).find(button => button.textContent === 'Move Right').disabled"), True)
        self.action("Move Left")
        self.assertEqual(self.browser.evaluate("Array.from(document.querySelectorAll('#tabs .tab')).map(tab => tab.dataset.path)"), ["/beta.txt", "/alpha.txt"])
        self.menu("/beta.txt")
        self.assertEqual(self.browser.evaluate("Array.from(document.querySelectorAll('.menu-item')).find(button => button.textContent === 'Move Left').disabled"), True)
        self.action("Close All 2")
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 0")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('dialog[open]').length"), 0)
        self.assertNoPageErrors()

    def test_delete_uses_one_dialog_and_cleans_up_dirty_descendant_tabs(self):
        self.loaded("/#/folder/child.txt")
        self.browser.evaluate("window.childModel = monaco.editor.getEditors()[0].getModel(); childModel.setValue('child edits'); window.confirm = () => {throw new Error('native confirmation used');}")
        # The folder becomes empty on disk while its old child remains open.
        (self.root / "folder/child.txt").unlink()
        self.menu("/folder", "#tree .row")
        self.action("Delete")
        self.waitFor("dialog[open]")
        self.assertIn("1 open tab", self.browser.evaluate("document.querySelector('dialog p').textContent"))
        self.dialog(False)
        self.assertTrue((self.root / "folder").is_dir())
        self.assertEqual(self.browser.evaluate("childModel.getValue()"), "child edits")
        self.menu("/folder", "#tree .row")
        self.action("Delete")
        self.dialog(True)
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 0")
        self.assertFalse((self.root / "folder").exists())
        self.assertEqual(self.browser.evaluate("[childModel.isDisposed(), document.querySelectorAll('dialog[open]').length]"), [True, 0])
        self.assertNoPageErrors()

    def test_delete_keeps_edits_typed_after_confirmation(self):
        self.loaded("/#/alpha.txt")
        self.browser.evaluate("""window.alphaModel = monaco.editor.getEditors()[0].getModel();
          const originalFetch = window.fetch;
          window.fetch = (url, options) => String(url).includes('/api/delete')
            ? new Promise(resolve => { window.finishDelete = () => originalFetch(url, options).then(resolve); })
            : originalFetch(url, options);""")
        self.menu("/alpha.txt")
        self.action("Delete")
        self.dialog(True)
        self.browser.wait_for("typeof window.finishDelete === 'function'")
        self.browser.evaluate("alphaModel.setValue('newer edits'); finishDelete()")
        self.browser.wait_for("document.getElementById('flash').textContent.includes('Newer unsaved edits remain open')")
        self.assertFalse((self.root / "alpha.txt").exists())
        self.assertEqual(self.browser.evaluate("[alphaModel.isDisposed(), alphaModel.getValue(), document.querySelectorAll('#tabs .tab.is-dirty').length]"), [False, "newer edits", 1])
        self.assertNoPageErrors()

    def test_delete_cannot_close_a_reopened_tab_with_the_same_model_version(self):
        self.loaded("/#/beta.txt")
        self.open_file("/alpha.txt")
        self.browser.evaluate("""window.oldModel = monaco.editor.getEditors()[0].getModel(); oldModel.setValue('old edits');
          const originalFetch = window.fetch;
          window.fetch = (url, options) => String(url).includes('/api/delete')
            ? new Promise(resolve => { window.finishDelete = () => originalFetch(url, options).then(resolve); })
            : originalFetch(url, options);""")
        self.menu("/alpha.txt")
        self.action("Delete")
        self.dialog(True)
        self.browser.wait_for("typeof window.finishDelete === 'function'")
        old_version = self.browser.evaluate("oldModel.getVersionId()")
        self.browser.click('#tabs .tab[data-path="/alpha.txt"] .close')
        self.dialog(True)
        self.browser.wait_for("!document.querySelector('#tabs .tab[data-path=\"/alpha.txt\"]')")
        self.open_file("/alpha.txt")
        self.browser.evaluate("window.reopenedModel = monaco.editor.getEditors()[0].getModel(); reopenedModel.setValue('new edits')")
        self.assertEqual(self.browser.evaluate("reopenedModel.getVersionId()"), old_version)
        self.browser.evaluate("finishDelete()")
        self.browser.wait_for("document.getElementById('flash').textContent.includes('Newer unsaved edits remain open')")
        self.assertFalse((self.root / "alpha.txt").exists())
        self.assertEqual(self.browser.evaluate("[reopenedModel.isDisposed(), reopenedModel.getValue(), document.querySelectorAll('#tabs .tab[data-path=\"/alpha.txt\"].is-dirty').length]"), [False, "new edits", 1])
        self.assertNoPageErrors()
