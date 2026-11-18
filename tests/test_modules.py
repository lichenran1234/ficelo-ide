"""Unit tests for the frontend modules, run inside a real browser.

The modules under test are the ones that ship — imported by URL from a
temporary web root holding the real `js/` directory, with a harness page
added alongside. Nothing test-only is ever put into the package.

These modules use browser APIs such as localStorage, createElement and
dataset. Running them in a real browser checks their actual storage and DOM
behavior without substituting mocks for those APIs.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from cdp import CHROME

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import server as server_module  # noqa: E402
from ficelo.config import Config  # noqa: E402
from ficelo.server import serve  # noqa: E402

HERE = Path(__file__).resolve().parent
WEB = Path(server_module.__file__).resolve().parent / "web"

HARNESS = """
import { store, ancestors, formatSize, basename, parentOf, debounce } from "/js/util.js";
import { DEFAULTS, FULL_WIDTH, readAppearance, writeAppearance, applyAppearance,
         resolveTheme, readScroll, writeScroll, buildPanel } from "/js/settings.js";
import { parseDelimited, viewerFor } from "/js/viewers.js";
import { ageClass, AGE_DAYS, Tabs, prefersStillness } from "/js/tabs.js";
import { interpretPath } from "/js/util.js";
import * as sessions from "/js/sessions.js";
import { groupByFolder } from "/js/review.js";
import { Tree } from "/js/tree.js";
import { markdownBlocks, outline, sanitise } from "/js/markdown.js";
import { topForLine, lineForTop, anchorsFrom } from "/js/sync.js";

const results = [];
const waiting = [];
/* A check returns null for pass or a string saying what went wrong — or a
   promise of either, for behaviour that only shows up asynchronously. */
function check(name, fn) {
  const record = (bad) => results.push({ name, ok: !bad, detail: bad || "" });
  const fail = (err) => results.push({ name, ok: false, detail: "threw: " + err.message });
  try {
    const outcome = fn();
    if (outcome && typeof outcome.then === "function") waiting.push(outcome.then(record, fail));
    else record(outcome);
  } catch (err) {
    fail(err);
  }
}
function eq(got, want) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  return a === b ? null : "got " + a + " want " + b;
}

__CHECKS__

await Promise.all(waiting);
document.getElementById("report").textContent = "REPORT" + JSON.stringify(results) + "ENDREPORT";
"""


@unittest.skipUnless(CHROME, "no Chromium; module tests cannot run")
class ModuleTest(unittest.TestCase):
    """Runs one batch of in-browser checks and asserts each passed."""

    checks = ""

    def setUp(self):
        # A web root holding the real js/ plus the harness. Monaco is not
        # copied: 24 MB per test, and none of these modules touch it.
        self.web = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.web, True)
        shutil.copytree(WEB / "js", self.web / "js")
        (self.web / "vendor").mkdir()
        shutil.copytree(WEB / "vendor" / "marked", self.web / "vendor" / "marked")
        shutil.copy(HERE / "harness" / "harness.html", self.web / "index.html")
        (self.web / "harness.js").write_text(HARNESS.replace("__CHECKS__", self.checks))

        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.real_web = server_module.WEB_ROOT
        server_module.WEB_ROOT = self.web
        self.addCleanup(setattr, server_module, "WEB_ROOT", self.real_web)

        self.server = serve(Config(root=self.root, port=0, state_dir=tempfile.mkdtemp()), quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def results(self):
        profile = tempfile.mkdtemp()
        out = subprocess.run(
            [
                CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
                "--disable-dev-shm-usage", "--user-data-dir=" + profile,
                "--virtual-time-budget=15000", "--dump-dom",
                "http://127.0.0.1:%d/" % self.port,
            ],
            capture_output=True, text=True, timeout=120,
        ).stdout
        shutil.rmtree(profile, ignore_errors=True)
        found = re.search(r"REPORT(\[.*?\])ENDREPORT", out, re.S)
        self.assertIsNotNone(found, "the harness produced no report:\n" + out[-2000:])
        # The DOM dump escapes the JSON's angle brackets and quotes.
        raw = found.group(1).replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return json.loads(raw)

    def assertAllPass(self):
        results = self.results()
        self.assertGreater(len(results), 0, "no checks ran")
        failed = ["%s: %s" % (r["name"], r["detail"]) for r in results if not r["ok"]]
        self.assertEqual(failed, [], "\n" + "\n".join(failed))
        return results


class UtilModuleTest(ModuleTest):
    checks = '''
check("ancestors of a nested path", () =>
  eq(ancestors("/a/b/c.txt"), ["/", "/a", "/a/b"]));
check("ancestors of a root-level file", () => eq(ancestors("/x.txt"), ["/"]));
check("basename", () => eq([basename("/a/b.txt"), basename("/")], ["b.txt", "/"]));
check("parentOf", () => eq([parentOf("/a/b.txt"), parentOf("/a")], ["/a", "/"]));
check("formatSize under a kilobyte", () => eq(formatSize(512), "512 B"));
check("formatSize keeps a decimal below ten", () => eq(formatSize(1536), "1.5 KB"));
check("formatSize drops it above ten", () => eq(formatSize(20 * 1024), "20 KB"));
check("formatSize scales past megabytes", () => eq(formatSize(3 * 1024 * 1024), "3.0 MB"));
check("store round-trips", () => { store.set("t.a", {x: 1}); return eq(store.get("t.a", null), {x: 1}); });
check("store returns the fallback when absent", () => eq(store.get("t.missing", "fb"), "fb"));
check("a value read from the store is a copy", () => {
  // Several callers read, edit and write back; editing what was read must
  // not change the store behind set()'s back.
  store.set("t.obj", { list: [1] });
  const got = store.get("t.obj", null);
  got.list.push(2);
  return eq(store.get("t.obj", null), { list: [1] });
});
check("setting null removes a value", () => {
  store.set("t.gone", 1);
  store.set("t.gone", null);
  return eq(store.get("t.gone", "fb"), "fb");
});
'''

    def test_util(self):
        self.assertAllPass()


class SettingsModuleTest(ModuleTest):
    checks = '''
check("defaults when nothing is stored", () => eq(readAppearance(), DEFAULTS));
check("interface and reading backgrounds have independent defaults", () =>
  eq([readAppearance().interfaceBackground, readAppearance().mdBackground, readAppearance().codeBackground], [242,242,242]));
check("older appearance settings acquire independent background defaults", () => {
  store.set("ficelo.appearance", {theme:"dark",codeFontSize:15});
  const value=readAppearance();
  return eq([value.interfaceBackground,value.mdBackground,value.codeBackground,value.theme,value.codeFontSize], [242,242,242,"dark",15]);
});
check("older preferences retain the existing file control transparency", () => {
  store.set("ficelo.appearance", {theme:"dark",showReloadButton:false});
  const value = readAppearance();
  return eq([value.fileControlTransparency,value.theme,value.showReloadButton], [40,"dark",false]);
});
check("file control transparency round-trips and rejects out-of-range settings", () => {
  for (const [input,expected] of [[0,0],[40,40],[75,75],[100,100],[-20,0],[150,100],["invalid",40]]) {
    writeAppearance({...DEFAULTS,fileControlTransparency:input});
    const error = eq(readAppearance().fileControlTransparency, expected);
    if (error) return JSON.stringify(input) + ": " + error;
  }
});
check("all gray levels round-trip independently, including black and white", () => {
  for (let level=0;level<=255;level++) {
    writeAppearance({...DEFAULTS,interfaceBackground:level,mdBackground:level,codeBackground:255-level});
    const value=readAppearance();
    if(value.interfaceBackground!==level || value.mdBackground!==level || value.codeBackground!==255-level)return "lost level "+level;
  }
});
check("background values are bounded, rounded and validated", () => {
  for (const [input,expected] of [[-10,0],[300,255],[123.6,124],[null,242],["0",242],[false,242],[{},242]]) {
    writeAppearance({...DEFAULTS,interfaceBackground:input,mdBackground:input,codeBackground:input});
    const value=readAppearance();
    const interfaceExpected=typeof input==="number"?expected:242;
    const error=eq([value.interfaceBackground,value.mdBackground,value.codeBackground],[interfaceExpected,expected,expected]);
    if(error)return JSON.stringify(input)+": "+error;
  }
});
check("prose and code have separate sizes", () => {
  // One shared number makes one of them wrong: 15px reading text next to
  // 15px monospace is too big, 13px prose too small.
  return DEFAULTS.mdFontSize !== DEFAULTS.codeFontSize ? null : "they are the same";
});
check("Contents settings share workspace visibility and reset without losing a Home draft", () => {
  const mount = document.createElement("div"), values = {...DEFAULTS};
  document.body.append(mount);
  let changed = 0;
  const update = () => { changed++; };
  store.set("ficelo.toc", {hidden: true});
  buildPanel(mount, values, update, {home: "/home"});
  const initiallyHidden = !mount.querySelector("#setting-toc").checked;
  mount.querySelector("#homePath").value = "/draft";
  mount.querySelector("#setting-toc").click();
  const shown = !store.get("ficelo.toc", {}).hidden;
  store.set("ficelo.toc", {hidden: true});
  buildPanel(mount, values, update);
  const followsExternalChange = !mount.querySelector("#setting-toc").checked;
  mount.querySelector(".set-reset").click();
  const result = [initiallyHidden, shown, followsExternalChange, mount.querySelector("#setting-toc").checked,
    store.get("ficelo.toc", {}).hidden, mount.querySelector("#homePath").value, changed,
    Object.keys(values).sort()];
  mount.remove();
  return eq(result, [true, true, true, true, false, "/draft", 2, Object.keys(DEFAULTS).sort()]);
});
check("a stored value is honoured", () => {
  writeAppearance({ ...DEFAULTS, mdFontSize: 20 });
  return eq(readAppearance().mdFontSize, 20);
});
check("an absurd value is clamped, not obeyed", () => {
  writeAppearance({ ...DEFAULTS, codeFontSize: 900 });
  return readAppearance().codeFontSize <= 24 ? null : "not clamped";
});
check("a negative value is clamped", () => {
  writeAppearance({ ...DEFAULTS, codeFontSize: -5 });
  return readAppearance().codeFontSize >= 9 ? null : "not clamped";
});
check("garbage falls back to the default", () => {
  writeAppearance({ ...DEFAULTS, mdLineHeight: "banana" });
  return eq(readAppearance().mdLineHeight, DEFAULTS.mdLineHeight);
});
check("an unknown theme falls back rather than being applied", () => {
  writeAppearance({ ...DEFAULTS, theme: "chartreuse" });
  return eq(readAppearance().theme, DEFAULTS.theme);
});
check("both themes are accepted", () => {
  for (const t of ["light", "dark"]) {
    writeAppearance({ ...DEFAULTS, theme: t });
    if (readAppearance().theme !== t) return "lost " + t;
  }
  return null;
});
check("resolveTheme passes light and dark straight through", () =>
  eq([resolveTheme("light"), resolveTheme("dark")], ["light", "dark"]));
check("legacy Auto falls back to Light regardless of the system theme", () => {
  const original = window.matchMedia;
  try {
    const got = [false, true].map(matches => {
      window.matchMedia = () => ({matches});
      store.set("ficelo.appearance", {theme: "auto"});
      return readAppearance().theme;
    });
    window.matchMedia = undefined;
    got.push(readAppearance().theme);
    return eq(got, ["light", "light", "light"]);
  } finally { window.matchMedia = original; }
});
check("a partial stored object still yields every key", () => {
  store.set("ficelo.appearance", { mdFontSize: 15 });
  return eq(Object.keys(readAppearance()).sort(), Object.keys(DEFAULTS).sort());
});
check("full width is reachable and means no maximum", () => {
  writeAppearance({ ...DEFAULTS, mdWidth: FULL_WIDTH });
  applyAppearance(readAppearance(), {});
  const got = document.documentElement.style.getPropertyValue("--content-width");
  return got.trim() === "none" ? null : "got " + got;
});
check("an ordinary width becomes pixels", () => {
  writeAppearance({ ...DEFAULTS, mdWidth: 800 });
  applyAppearance(readAppearance(), {});
  return eq(document.documentElement.style.getPropertyValue("--content-width").trim(), "800px");
});
check("scroll positions round-trip per path", () => {
  writeScroll("/a.txt", { top: 120 });
  writeScroll("/b.txt", { top: 40 });
  return eq([readScroll("/a.txt").top, readScroll("/b.txt").top], [120, 40]);
});
check("an unseen path has no position", () => eq(readScroll("/never.txt"), null));
check("remembered positions are capped", () => {
  for (let i = 0; i < 340; i += 1) writeScroll("/f" + i + ".txt", { top: i });
  const all = store.get("ficelo.scroll", {});
  return Object.keys(all).length <= 300 ? null : "grew to " + Object.keys(all).length;
});
'''

    def test_settings(self):
        self.assertAllPass()


class ViewerModuleTest(ModuleTest):
    checks = '''
check("viewerFor picks by extension", () =>
  eq([viewerFor("/a.png"), viewerFor("/a.pdf"), viewerFor("/a.csv"),
      viewerFor("/a.ipynb"), viewerFor("/a.py")],
     ["image", "pdf", "csv", "notebook", null]));
check("viewerFor is case-insensitive", () => eq(viewerFor("/A.PNG"), "image"));
check("a plain row", () => eq(parseDelimited("a,b\\nc,d\\n", ","), [["a","b"],["c","d"]]));
check("a quoted comma stays in its field", () =>
  eq(parseDelimited('a,"b,c"\\n', ","), [["a", "b,c"]]));
check("a doubled quote becomes one", () =>
  eq(parseDelimited('a,"say ""hi"""\\n', ","), [["a", 'say "hi"']]));
check("a newline inside quotes is not a row break", () =>
  eq(parseDelimited('a,"one\\ntwo"\\n', ","), [["a", "one\\ntwo"]]));
check("CRLF is one line ending", () =>
  eq(parseDelimited("a,b\\r\\nc,d\\r\\n", ","), [["a","b"],["c","d"]]));
check("a trailing row without a newline is kept", () =>
  eq(parseDelimited("a,b\\nc,d", ","), [["a","b"],["c","d"]]));
check("empty fields survive", () => eq(parseDelimited("a,,b\\n", ","), [["a","","b"]]));
check("tabs work as a delimiter", () => eq(parseDelimited("a\\tb\\n", "\\t"), [["a","b"]]));
'''

    def test_viewers(self):
        self.assertAllPass()


class SyncModuleTest(ModuleTest):
    checks = '''
// A heading at line 1 rendered at 0, a paragraph at line 3 at 100, a long
// code block at line 10 at 140, and the end of the document at line 30/600.
const A = [{line:1,top:0},{line:3,top:100},{line:10,top:140},{line:30,top:600}];

check("a line on an anchor maps to its exact top", () =>
  eq([topForLine(A,1), topForLine(A,3), topForLine(A,10)], [0,100,140]));
check("a line between anchors is interpolated", () =>
  eq(topForLine(A,2), 50));
check("interpolation respects uneven density", () => {
  // Lines 3-10 span only 40px; lines 10-30 span 460.
  const mid = topForLine(A, 6);
  return (mid > 100 && mid < 140) ? null : "got " + mid;
});
check("before the first anchor clamps to the top", () => eq(topForLine(A,0), 0));
check("after the last clamps to the end", () => eq(topForLine(A,999), 600));
check("empty anchors do not throw", () => eq(topForLine([],5), 0));

check("a top on an anchor maps to its exact line", () =>
  eq([lineForTop(A,0), lineForTop(A,100), lineForTop(A,140)], [1,3,10]));
check("a top between anchors is interpolated", () => eq(lineForTop(A,50), 2));
check("before the first top clamps", () => eq(lineForTop(A,-20), 1));
check("after the last top clamps", () => eq(lineForTop(A,9999), 30));
check("empty anchors give line one", () => eq(lineForTop([],50), 1));

check("the two directions round-trip on anchors", () => {
  for (const a of A) {
    if (lineForTop(A, topForLine(A, a.line)) !== a.line) return "failed at line " + a.line;
  }
  return null;
});
check("a zero-height span does not divide by zero", () => {
  const flat = [{line:1,top:0},{line:5,top:0},{line:9,top:80}];
  const got = lineForTop(flat, 0);
  return Number.isFinite(got) ? null : "got " + got;
});
check("a zero-line span does not divide by zero", () => {
  const same = [{line:4,top:0},{line:4,top:60},{line:9,top:120}];
  const got = topForLine(same, 4);
  return Number.isFinite(got) ? null : "got " + got;
});
check("anchorsFrom reads data-src-line and appends an end sentinel", () => {
  const d = document.createElement("div");
  d.innerHTML = '<div class="md-block" data-src-line="1">a</div>' +
                '<div class="md-block" data-src-line="7">b</div>';
  document.body.append(d);
  const got = anchorsFrom(d, 12);
  d.remove();
  if (got.length !== 3) return "expected 3 anchors, got " + got.length;
  return eq([got[0].line, got[1].line, got[2].line], [1, 7, 12]);
});
check("anchorsFrom on an empty container is empty", () => {
  const d = document.createElement("div");
  return eq(anchorsFrom(d, 5), []);
});
'''

    def test_sync(self):
        self.assertAllPass()


class TabsModuleTest(ModuleTest):
    checks = '''
const NOW = 1750000000000;
const ago = (days) => (NOW - days * 86400000) / 1000;

check("a file touched moments ago is the freshest band", () =>
  eq(ageClass(ago(0.01), NOW), "age-0"));
check("the first week is finely divided", () => {
  const bands = [0.5, 1.5, 2.5, 4, 6].map(d => ageClass(ago(d), NOW));
  return new Set(bands).size === 5 ? null : "collapsed to " + JSON.stringify(bands);
});
check("beyond the last boundary everything shares a band", () =>
  eq([ageClass(ago(40), NOW), ageClass(ago(400), NOW)], ["age-6", "age-6"]));
check("a boundary belongs to the older band", () =>
  eq(ageClass(ago(AGE_DAYS[0]), NOW), "age-1"));
check("an unknown time is its own band, not the oldest", () =>
  eq(ageClass(null, NOW), "age-unknown"));
check("a future timestamp is treated as brand new", () => {
  // A bad clock, or a copy that preserved a timestamp.
  return eq(ageClass(ago(-5), NOW), "age-0");
});

'''

    def test_tabs(self):
        self.assertAllPass()


class PathInputModuleTest(ModuleTest):
    checks = '''
// The served root is "/" by default now, and home is a folder inside it.
const ROOT = "/";
const HOME = "/home/someone";
check("a bare relative path is relative to the root", () =>
  eq(interpretPath("etc/hosts", ROOT, HOME), "/etc/hosts"));
check("an absolute path", () => eq(interpretPath("/etc/hosts", ROOT, HOME), "/etc/hosts"));
check("~ is the home folder, not the served root", () =>
  eq(interpretPath("~", ROOT, HOME), "/home/someone"));
check("~/x is inside the home folder", () =>
  eq(interpretPath("~/notes/a.md", ROOT, HOME), "/home/someone/notes/a.md"));
check("~/ with a trailing slash is still home", () =>
  eq(interpretPath("~/", ROOT, HOME), "/home/someone"));
check("~user is not expanded, rather than guessed at", () =>
  eq(interpretPath("~other/x", ROOT, HOME), "/~other/x"));
check("surrounding space is ignored", () =>
  eq(interpretPath("  ~/a.md  ", ROOT, HOME), "/home/someone/a.md"));
check("doubled separators collapse", () => eq(interpretPath("/etc//hosts", ROOT, HOME), "/etc/hosts"));
check("empty is nothing", () => eq(interpretPath("   ", ROOT, HOME), null));

// A narrower served root still works as before.
const NARROW = "/home/someone/notes";
check("an absolute path inside a narrow root is rebased", () =>
  eq(interpretPath(NARROW + "/designs/a.md", NARROW, "/"), "/designs/a.md"));
check("the narrow root itself", () => eq(interpretPath(NARROW, NARROW, "/"), "/"));
check("a sibling sharing the root's prefix is not rebased", () => {
  const got = interpretPath("/home/someone/notes-old/x.md", NARROW, "/");
  return got === "/home/someone/notes-old/x.md" ? null : "rebased to " + got;
});

'''

    def test_path_input(self):
        self.assertAllPass()


class SessionsModuleTest(ModuleTest):
    checks = '''
const S = (id, over) => Object.assign({ id, name: "", starred: false, at: 0, paths: [] }, over);

check("activity age uses the requested boundaries and wording", () => {
  const now = 400 * 86400000;
  const cases = [[-1, "less than 1 min ago"], [0, "less than 1 min ago"],
    [59999, "less than 1 min ago"], [60000, "1 min ago"],
    [3599999, "59 min ago"], [3600000, "1 h ago"],
    [86399999, "23 h ago"], [86400000, "1 day ago"],
    [365 * 86400000 - 1, "364 day ago"], [365 * 86400000, "> 1 year ago"]];
  return eq(cases.map(([elapsed]) => sessions.activityAge(now - elapsed, now)), cases.map(([, text]) => text));
});
check("activity updates only the timestamp and ignores retired sessions", () => {
  const record = S("seen", {at:1, name:"Reading", paths:["/readme.md"]});
  store.set("ficelo.workspace.seen", record);
  try {
    sessions.save(record);
    if (store.get("ficelo.workspace.seen").at !== 1) return "an unchanged save counted as activity";
    sessions.markActive("seen", 200);
    const seen = store.get("ficelo.workspace.seen");
    if (eq(seen, {...record, at:200})) return "viewing changed session contents";
    sessions.markActive("seen", 100);
    if (store.get("ficelo.workspace.seen").at !== 200) return "activity moved backwards";
    sessions.save({...seen, name:"Renamed"});
    if (store.get("ficelo.workspace.seen").at <= 200) return "a change did not count as activity";
    for (const retired of [{...record, deletedAt:10}, {...record, redirect:"other"}]) {
      store.set("ficelo.workspace.seen", retired);
      sessions.markActive("seen", 300);
      if (eq(store.get("ficelo.workspace.seen"), retired)) return "a retired session was changed";
    }
    return null;
  } finally { store.set("ficelo.workspace.seen", null); }
});
check("ordered puts starred first", () => {
  const got = sessions.ordered([S("a",{at:9}), S("b",{starred:true,at:1})]).map(s => s.id);
  return eq(got, ["b", "a"]);
});
check("then most recent", () => {
  const got = sessions.ordered([S("a",{at:1}), S("b",{at:5}), S("c",{at:3})]).map(s => s.id);
  return eq(got, ["b", "c", "a"]);
});
check("prune keeps every starred session however old", () => {
  const many = [];
  for (let i = 0; i < 60; i += 1) many.push(S("u" + i, { at: i }));
  many.push(S("keep", { starred: true, at: -100 }));
  const kept = sessions.prune(many).map(s => s.id);
  return kept.includes("keep") ? null : "the starred one was pruned";
});
check("prune caps the unstarred ones", () => {
  const many = [];
  for (let i = 0; i < 60; i += 1) many.push(S("u" + i, { at: i }));
  const kept = sessions.prune(many).filter(s => !s.starred);
  return kept.length <= sessions.MAX_SESSIONS ? null : "kept " + kept.length;
});
check("prune drops the oldest unstarred first", () => {
  const many = [];
  for (let i = 0; i < sessions.MAX_SESSIONS + 10; i += 1) many.push(S("u" + i, { at: i }));
  const kept = sessions.prune(many).map(s => s.id);
  return kept.includes("u" + (sessions.MAX_SESSIONS + 9)) && !kept.includes("u0") ? null : "wrong end pruned";
});
check("a named session shows its name", () =>
  eq(sessions.describe(S("a", { name: "  Review work  " })), "Review work"));
check("an unnamed one is described by its files", () =>
  eq(sessions.describe(S("a", { paths: ["/x/one.md", "/y/two.md"] })), "one.md, two.md"));
check("and summarised when there are many", () =>
  eq(sessions.describe(S("a", { paths: ["/a.md","/b.md","/c.md","/d.md"] })), "a.md and 3 more"));
check("an empty one says so", () => eq(sessions.describe(S("a")), "Empty session"));
check("upsert replaces rather than duplicating", () => {
  const one = sessions.upsert([S("a", { name: "first" })], S("a", { name: "second" }));
  return eq([one.length, one[0].name], [1, "second"]);
});
check("upsert appends a new one", () =>
  eq(sessions.upsert([S("a")], S("b")).length, 2));
check("readAll survives junk in storage", () => {
  store.set("ficelo.sessions", ["nonsense", 4, null, {id:"ok"}]);
  const got = sessions.readAll();
  return eq(got.map(s => s.id), ["ok"]);
});
check("new IDs skip legacy, deleted, redirected, and unsaved identities", () => {
  store.set("ficelo.sessions", [S("aaaa")]);
  store.set("ficelo.workspace.bbbb", {...S("bbbb"), deletedAt:1});
  store.set("ficelo.workspace.cccc", {...S("cccc"), redirect:"aaaa"});
  const original = crypto.getRandomValues;
  let calls = 0;
  crypto.getRandomValues = bytes => bytes.fill(calls++);
  try {
    const first = sessions.newId();
    calls = 3;
    const second = sessions.newId();
    return eq([first, second, calls], ["dddd", "eeee", 5]);
  } finally { crypto.getRandomValues = original; }
});
check("new session IDs are four lowercase letters or digits and distinct before saving", () => {
  const ids = Array.from({length:100}, () => sessions.newId());
  return ids.every(id => /^[a-z0-9]{4}$/.test(id)) && new Set(ids).size === ids.length
    ? null : "session IDs must be short and unique";
});
check("short IDs also work without browser crypto", () => {
  const original = crypto.getRandomValues;
  crypto.getRandomValues = undefined;
  try { return /^[a-z0-9]{4}$/.test(sessions.newId()) ? null : "fallback ID has the wrong shape"; }
  finally { crypto.getRandomValues = original; }
});
'''

    def test_sessions(self):
        self.assertAllPass()


class TabOrderModuleTest(ModuleTest):
    checks = '''
function make() {
  const mount = document.createElement("div");
  const t = new Tabs(mount, { onSelect() {}, onClose() {} });
  ["/a", "/b", "/c"].forEach(p => t.add(p));
  t.active = "/b";
  return t;
}
check("move right", () => { const t = make(); t.move("/a", 1);
  return eq(t.items.map(x => x.id), ["/b", "/a", "/c"]); });
check("move left", () => { const t = make(); t.move("/c", -1);
  return eq(t.items.map(x => x.id), ["/a", "/c", "/b"]); });
check("moving past the end does nothing", () => { const t = make();
  const moved = t.move("/c", 1);
  return eq([moved, t.items.map(x => x.id).join("")], [false, "/a/b/c"]); });
check("next wraps", () => { const t = make(); t.active = "/c";
  return eq(t.neighbour(1), "/a"); });
check("previous wraps", () => { const t = make(); t.active = "/a";
  return eq(t.neighbour(-1), "/c"); });
check("nth is one-based", () => eq(make().nth(2), "/b"));
check("nine means the last, whatever the count", () => eq(make().nth(9), "/c"));
check("a number past the end is nothing", () => eq(make().nth(5), null));
/* The harness page carries no stylesheet, so tabs have to be given enough
   geometry here to have positions worth animating between. Motion
   preference is forced on: whether headless Chrome reports
   prefers-reduced-motion is not what these checks are about. */
function stripWith(ids) {
  const mount = document.createElement("div");
  mount.style.cssText = "display:flex; position:absolute; top:0; left:0; width:900px";
  document.body.append(mount);
  const t = new Tabs(mount, { onSelect() {}, onClose() {} });
  ids.forEach((id) => t.add(id));
  t.active = ids[0];
  const style = document.createElement("style");
  style.textContent = ".tab { display:flex; width:120px; flex:none; }";
  document.head.append(style);
  t.cleanup = () => { mount.remove(); style.remove(); };
  return { t, mount };
}

const realMatchMedia = window.matchMedia;
window.matchMedia = (q) => ({ matches: false, addEventListener() {}, addListener() {} });

check("a tab already on screen slides to its new place", () => {
  // Asserted on the transition and on the inverted start position, not on
  // style.transform: the play step clears that synchronously, so by the
  // time draw() returns it is always empty whether or not anything moved.
  const { t, mount } = stripWith(["/a", "/b"]);
  t.draw();
  const before = Array.from(mount.querySelectorAll(".tab"))
    .map((n) => [n.dataset.id, n.getBoundingClientRect().left]);
  if (before[0][1] === before[1][1]) return "the tabs were never laid out apart";

  t.items.reverse();          // /b now comes first, so both must travel
  t.draw();
  const nodes = Array.from(mount.querySelectorAll(".tab"));
  const animating = nodes.filter((n) => (n.style.transition || "").includes("transform"));
  // Each should currently be sitting where it used to be.
  const startedWhereItWas = nodes.every((n) => {
    const was = before.find((pair) => pair[0] === n.dataset.id)[1];
    return Math.abs(n.getBoundingClientRect().left - was) < 1;
  });
  t.cleanup();
  if (animating.length !== 2) return animating.length + " of 2 tabs are animating";
  return startedWhereItWas ? null : "a tab did not start from where it was";
});
check("closing remembers the path and its place", () => {
  const t = make(); t.close("/b");
  const last = t.takeLastClosed();
  return eq([last.path, last.at], ["/b", 1]);
});
check("the closed list is a stack", () => {
  const t = make(); t.close("/a"); t.close("/c");
  return eq(t.takeLastClosed().path, "/c");
});
check("nothing closed yields null", () => eq(make().takeLastClosed(), null));
check("a diff tab retains its identity for reopening", () => {
  const t = make();
  t.add("diff:work:/a", { kind: "diff", path: "/a" });
  t.close("diff:work:/a");
  return eq(t.takeLastClosed(), {path:"/a", at:3, kind:"diff"});
});
'''

    def test_tab_order(self):
        self.assertAllPass()


class ReviewGroupingModuleTest(ModuleTest):
    checks = '''
const F = (path, added, removed) => ({ path, added, removed });

check("files are gathered by folder", () => {
  const got = groupByFolder([F("a/one.py",1,0), F("b/two.py",2,0), F("a/three.py",3,0)]);
  return eq(got.map(g => g.folder + ":" + g.entries.length), ["a:2", "b:1"]);
});
check("folders come out in path order", () => {
  const got = groupByFolder([F("z/x.py",0,0), F("a/x.py",0,0), F("m/x.py",0,0)]);
  return eq(got.map(g => g.folder), ["a", "m", "z"]);
});
check("a top-level file gets its own empty folder", () => {
  const got = groupByFolder([F("/README.md",1,1)]);
  return eq(got[0].folder, "");
});
check("counts are summed per folder", () => {
  const got = groupByFolder([F("a/one.py",5,2), F("a/two.py",3,1)]);
  return eq([got[0].added, got[0].removed], [8, 3]);
});
check("a missing count is treated as zero, not NaN", () => {
  const got = groupByFolder([{ path: "a/bin.png" }, F("a/x.py", 2, 1)]);
  return Number.isFinite(got[0].added) ? null : "added came out " + got[0].added;
});
check("nothing in, nothing out", () => eq(groupByFolder([]), []));
'''

    def test_review_grouping(self):
        self.assertAllPass()


class TreeModuleTest(ModuleTest):
    checks = '''
const pause = (ms) => new Promise((r) => setTimeout(r, ms));
function treeWith(listings, delays) {
  const mount = document.createElement("ul");
  const t = new Tree(mount, { onOpenFile() {} });
  /* Listings served from memory, each after its own delay, so the order
     the draws finish in is chosen by the check rather than by luck. */
  t.listing = async (path) => {
    await pause(delays[path] || 0);
    return listings[path] || [];
  };
  return { t, mount };
}
const entry = (path) => ({ name: path.split("/").pop(), path, is_dir: false });

check("a slow draw for the old start does not paint over a newer one", async () => {
  const { t, mount } = treeWith(
    { "/old": [entry("/old/stale.txt")], "/new": [entry("/new/fresh.txt")] },
    { "/old": 200, "/new": 10 }
  );
  t.root = "/old";
  const first = t.draw();       // slow
  t.root = "/new";
  const second = t.draw();      // fast, finishes first
  await Promise.all([first, second]);
  const shown = Array.from(mount.querySelectorAll("[data-path]")).map((n) => n.dataset.path);
  return eq(shown, ["/new/fresh.txt"]);
});
check("a single draw still paints", async () => {
  const { t, mount } = treeWith({ "/": [entry("/a.txt")] }, {});
  t.root = "/";
  await t.draw();
  return eq(mount.querySelectorAll("[data-path]").length, 1);
});
'''

    def test_tree(self):
        self.assertAllPass()


class MarkdownModuleTest(ModuleTest):
    checks = '''
const doc = "# One\\n\\npara\\n\\n## Two\\n\\n```\\ncode\\n\\n# not a heading\\n```\\n\\n- item\\n";
check("blocks carry their start line", () => {
  const lines = markdownBlocks(doc, window.marked).map(b => b.line);
  // # One=1, para=3, ## Two=5, fence=7 (running to 11), list=13. The fence
  // contains a blank line and a "#" and must count as one block of five lines.
  return eq(lines, [1, 3, 5, 7, 13]);
});
check("a hash inside a fence is not a heading", () => {
  const heads = outline(doc, window.marked).map(h => h.text);
  return eq(heads, ["One", "Two"]);
});
check("heading depth is reported", () =>
  eq(outline(doc, window.marked).map(h => h.depth), [1, 2]));
check("duplicate headings get distinct slugs", () => {
  const slugs = outline("# Notes\\n\\n# Notes\\n\\n# Notes\\n", window.marked).map(h => h.slug);
  return new Set(slugs).size === 3 ? null : "collided: " + JSON.stringify(slugs);
});
check("blank lines do not become blocks", () => {
  const types = markdownBlocks("a\\n\\n\\n\\nb\\n", window.marked).map(b => b.type);
  return types.includes("space") ? "space token emitted" : null;
});
check("sanitise removes script elements", () => {
  const d = document.createElement("div");
  d.innerHTML = "<p>ok</p><script>window.bad=1<\\/script>";
  sanitise(d);
  return d.querySelector("script") ? "script survived" : null;
});
check("sanitise strips inline handlers", () => {
  const d = document.createElement("div");
  d.innerHTML = '<img src="x" onerror="window.bad=1">';
  sanitise(d);
  return d.querySelector("img").hasAttribute("onerror") ? "onerror survived" : null;
});
check("sanitise strips javascript: urls", () => {
  const d = document.createElement("div");
  d.innerHTML = '<a href="javascript:alert(1)">x</a>';
  sanitise(d);
  return d.querySelector("a").hasAttribute("href") ? "href survived" : null;
});
check("sanitise sees through obfuscated javascript: urls", () => {
  const d = document.createElement("div");
  d.innerHTML = '<a href="java\\tscript:alert(1)">x</a>';
  sanitise(d);
  return d.querySelector("a").hasAttribute("href") ? "obfuscated href survived" : null;
});
check("sanitise keeps ordinary links", () => {
  const d = document.createElement("div");
  d.innerHTML = '<a href="https://example.com">x</a>';
  sanitise(d);
  return d.querySelector("a").getAttribute("href") === "https://example.com" ? null : "link lost";
});
'''

    def test_markdown(self):
        self.assertAllPass()


if __name__ == "__main__":
    unittest.main()
