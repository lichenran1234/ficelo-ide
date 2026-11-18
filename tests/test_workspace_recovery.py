"""Recovery modules against real HTTP and browser storage, without an editor.

The page tests cover app integration. These transport/picker cases load only
the shipped modules they exercise, avoiding thirteen redundant Monaco starts.
"""

import json
import shutil
import tempfile
from pathlib import Path

from test_browser import BrowserCase

from ficelo import server as server_module

HARNESS = '''<!doctype html><html><body><script type="module">
import * as u from "/js/util.js";
import * as s from "/js/sessions.js";
window.u = u; window.s = s;
await u.loadStore(); s.initialize();
if (location.hash.startsWith("#/")) {
  const path = decodeURIComponent(location.hash.slice(1)), id = s.currentId();
  s.save({id, root:"/", expanded:["/"], tabs:{[path]:{id:path,path}}, order:[path], active:path});
  await u.flushStore();
}
window.addEventListener("pagehide", () => u.flushStore(true));
document.body.dataset.ficeloReady = "1";
</script></body></html>'''


class WorkspaceRecoveryTest(BrowserCase):
    writable = True

    def setUp(self):
        super().setUp()
        original = server_module.WEB_ROOT
        harness = tempfile.TemporaryDirectory()
        self.addCleanup(harness.cleanup)
        web = Path(harness.name)
        shutil.copytree(original / "js", web / "js")
        (web / "index.html").write_text(HARNESS)
        server_module.WEB_ROOT = web
        self.addCleanup(setattr, server_module, "WEB_ROOT", original)

    def prepare(self):
        for name in ("alpha.txt", "beta.txt", "gamma.txt"):
            (self.root / name).write_text(name + "\n")

    def evaluate(self, script):
        return self.browser.evaluate("(async () => {" + script + "})()")

    def ready(self):
        self.loaded("/#/alpha.txt")
        self.evaluate("window.u = await import('/js/util.js'); window.s = await import('/js/sessions.js'); await u.flushStore();")

    def offline(self, held=False):
        outcome = "new Promise(() => {})" if held else "Promise.reject(new Error('offline test'))"
        self.evaluate("window.originalFetch = window.fetch; window.fetch = (url, options) => "
                      "String(url) === '/api/state' && options?.method === 'POST' ? "
                      + outcome + " : window.originalFetch(url, options);")

    def record(self, ident="shared"):
        return {"id": ident, "name": "Research", "root": "/", "expanded": ["/"],
                "tabs": {"/alpha.txt": {"id": "/alpha.txt", "path": "/alpha.txt"}},
                "order": ["/alpha.txt"], "active": "/alpha.txt",
                "state": {"ficelo.scroll": {"/alpha.txt": {"top": 240, "anchor": "chapter"}}}}

    def seed(self, ident="shared"):
        self.server.settings.put("ficelo.workspace." + ident, self.record(ident))
        self.evaluate("await u.refreshStore();")

    def change_shared(self, flush=True):
        self.evaluate("const record = u.store.get('ficelo.workspace.shared');"
                      "record.tabs['/beta.txt']={id:'/beta.txt',path:'/beta.txt',view:'split'};"
                      "record.order.push('/beta.txt');record.active='/beta.txt';"
                      "u.store.set('ficelo.workspace.shared',record);"
                      + ("await u.flushStore();" if flush else ""))

    def test_failed_save_keeps_full_view_and_restores_to_a_fresh_identity(self):
        self.ready(); self.seed(); self.offline(); self.change_shared()
        result = self.evaluate("const entry=u.recoverySnapshots().find(e=>e.record.id==='shared');"
                               "const id=s.restoreRecovery(entry.key); return {entry,id,copy:u.store.get('ficelo.workspace.'+id)};")
        self.assertEqual(result["entry"]["reason"], "Save failed")
        self.assertEqual(result["copy"]["order"], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(result["copy"]["state"]["ficelo.scroll"]["/alpha.txt"], {"top": 240, "anchor": "chapter"})
        self.assertNotEqual(result["id"], "shared")
        self.evaluate("window.fetch=window.originalFetch;await u.flushStore();")
        self.assertIn("ficelo.workspace." + result["id"], self.server.settings.read())

    def test_acknowledged_normal_saves_remove_temporary_browser_snapshots(self):
        self.ready(); self.seed(); self.change_shared()
        self.assertEqual(self.evaluate("return Object.keys(localStorage).filter(key=>key.startsWith('ficelo.recovery.'));"), [])

    def test_deleted_workspace_acknowledgement_preserves_the_local_addition(self):
        self.ready(); self.seed(); self.offline(); self.change_shared()
        deleted = {**self.record(), "deletedAt": 1}
        self.server.settings.put("ficelo.workspace.shared", deleted)
        result = self.evaluate("window.fetch=window.originalFetch;await u.flushStore();"
                               "return u.recoverySnapshots().find(entry=>entry.record.id==='shared');")
        self.assertEqual(result["reason"], "Removed elsewhere")
        self.assertEqual(result["record"]["order"], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(self.server.settings.read()["ficelo.workspace.shared"], deleted)
        self.assertEqual(self.evaluate("return JSON.parse(sessionStorage.getItem('ficelo.pending'));"), {})

    def test_redirect_acknowledgement_preserves_the_unmerged_local_view(self):
        self.ready(); self.seed(); self.offline(); self.change_shared()
        self.server.settings.put("ficelo.workspace.destination", self.record("destination"))
        self.server.settings.put("ficelo.workspace.shared", {"id": "shared", "redirect": "destination"})
        result = self.evaluate("window.fetch=window.originalFetch;await u.flushStore();"
                               "return u.recoverySnapshots().find(entry=>entry.record.id==='shared');")
        self.assertEqual(result["reason"], "Merged elsewhere")
        self.assertEqual(result["record"]["active"], "/beta.txt")
        self.assertEqual(result["record"]["order"], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(self.server.settings.read()["ficelo.workspace.shared"]["redirect"], "destination")

    def test_unconfirmed_full_snapshot_survives_reload_after_server_deletion(self):
        self.ready(); self.seed(); self.offline(held=True); self.change_shared(flush=False)
        self.server.settings.put("ficelo.workspace.shared", {**self.record(), "deletedAt": 1})
        self.loaded("/")
        result = self.evaluate("const u=await import('/js/util.js');await u.flushStore();"
                               "return u.recoverySnapshots().find(entry=>entry.record.id==='shared');")
        self.assertEqual(result["reason"], "Removed elsewhere")
        self.assertEqual(result["record"]["order"], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(result["record"]["state"]["ficelo.scroll"]["/alpha.txt"]["top"], 240)

    def test_refresh_preserves_clean_records_removed_from_the_server(self):
        self.ready(); self.seed()
        self.server.settings.put("ficelo.workspace.shared", None)
        result = self.evaluate("await u.refreshStore();return {recovery:u.recoverySnapshots().find(entry=>entry.record.id==='shared'),live:u.store.get('ficelo.workspace.shared',null)};")
        self.assertIsNone(result["live"])
        self.assertEqual(result["recovery"]["reason"], "Removed elsewhere")
        self.assertEqual(result["recovery"]["record"]["order"], ["/alpha.txt"])

    def test_reload_keeps_full_recovery_when_a_pending_existing_workspace_is_removed(self):
        self.ready(); self.seed(); self.offline(held=True); self.change_shared(flush=False)
        self.server.settings.put("ficelo.workspace.shared", None)
        self.loaded("/")
        result = self.evaluate("const u=await import('/js/util.js');await u.flushStore();"
                               "return u.recoverySnapshots().find(entry=>entry.record.id==='shared');")
        self.assertEqual(result["reason"], "Removed elsewhere")
        self.assertEqual(result["record"]["order"], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(result["record"]["name"], "Research")
        self.assertEqual(result["record"]["state"]["ficelo.scroll"]["/alpha.txt"]["top"], 240)
        self.assertNotIn("ficelo.workspace.shared", self.server.settings.read())

    def test_reload_still_creates_a_genuinely_new_pending_workspace(self):
        self.ready(); self.offline(held=True)
        record = self.record("brandnew")
        self.evaluate("u.store.set('ficelo.workspace.brandnew'," + json.dumps(record) + ");")
        self.loaded("/")
        self.evaluate("const u=await import('/js/util.js');await u.flushStore();")
        self.assertEqual(self.server.settings.read()["ficelo.workspace.brandnew"], record)

    def test_successful_creation_acknowledgement_marks_later_snapshots_as_existing(self):
        self.ready()
        self.evaluate("u.store.set('ficelo.workspace.shared'," + json.dumps(self.record()) + ");await u.flushStore();")
        self.offline(held=True); self.change_shared(flush=False)
        self.server.settings.put("ficelo.workspace.shared", None)
        self.loaded("/")
        result = self.evaluate("const u=await import('/js/util.js');await u.flushStore();"
                               "return u.recoverySnapshots().find(entry=>entry.record.id==='shared');")
        self.assertEqual(result["reason"], "Removed elsewhere")
        self.assertEqual(result["record"]["order"], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(result["record"]["name"], "Research")
        self.assertNotIn("ficelo.workspace.shared", self.server.settings.read())

    def test_metadata_filter_excludes_buffers_credentials_and_terminal_state(self):
        self.ready()
        result = self.evaluate("return u.workspaceSnapshot({id:'safe',password:'do-not-save',text:'do-not-save',"
                               "tabs:{a:{id:'a',path:'/alpha.txt',text:'do-not-save',token:'do-not-save',"
                               "viewState:{cursorState:[{position:{lineNumber:8,column:3}}],buffer:'do-not-save'}},"
                               "terminal:{id:'terminal',path:'/',kind:'terminal',history:'do-not-save'}},"
                               "state:{credentials:'do-not-save','ficelo.scroll':{'/alpha.txt':{top:20,anchor:'chapter'}}}});")
        self.assertNotIn("do-not-save", json.dumps(result))
        self.assertEqual(list(result["tabs"]), ["a"])
        self.assertEqual(result["tabs"]["a"]["viewState"]["cursorState"][0]["position"]["lineNumber"], 8)

    def test_recovery_storage_is_bounded_by_count_and_total_bytes(self):
        self.ready(); self.offline()
        result = self.evaluate("for(let n=0;n<25;n++) u.store.set('ficelo.workspace.backup'+n,"
                               "{id:'backup'+n,name:'x'.repeat(110000),tabs:{},order:[]});"
                               "await u.flushStore(); const entries=u.recoverySnapshots();"
                               "return {count:entries.length,bytes:entries.reduce((sum,entry)=>sum+entry.bytes,0),ids:entries.map(entry=>entry.record.id)};")
        self.assertGreater(result["count"], 0)
        self.assertLessEqual(result["count"], 20)
        self.assertLessEqual(result["bytes"], 2 * 1024 * 1024)
        self.assertIn("backup24", result["ids"])

    def test_picker_searches_deleted_sessions_without_showing_local_recovery(self):
        self.ready(); self.seed(); self.offline(); self.change_shared()
        self.evaluate("window.fetch=window.originalFetch;await u.flushStore();")
        self.server.settings.put("ficelo.workspace.deleted", {**self.record("deleted"), "deletedAt": 1})
        result = self.evaluate("await u.refreshStore();const current={id:'current',name:'Unrelated current',tabs:{}};"
                               "const other={id:'other',name:'Research',group:'Existing group',starred:true,tabs:{'/beta.txt':{id:'/beta.txt',path:'/beta.txt'}}};"
                               "const mount=document.createElement('div');document.body.append(mount);"
                               "s.buildList(mount,[other,current],current.id,{onRefresh(){},onMerge(){},onStar(){},onDelete(){},onMenu(){},onPick(id){window.picked=id;},onAdd(){}});"
                               "const first=mount.querySelector('.session-name').textContent;"
                               "const search=mount.querySelector('.session-search');search.value='alpha Research';search.dispatchEvent(new Event('input'));"
                               "mount.querySelector('.session input').click();"
                               "return {first,names:[...mount.querySelectorAll('.session .session-name')].map(node=>node.textContent),"
                               "deletedNames:[...mount.querySelectorAll('.session-deleted .session-name')].map(node=>node.textContent),"
                               "trash:mount.querySelector('.session-trash').open,recovery:!!mount.querySelector('.session-recovery'),"
                               "recoveryCount:u.recoverySnapshots().length,"
                               "groupEnabled:!mount.querySelector('.session-group-choice').disabled,groups:[...mount.querySelectorAll('.session-group-choice option')].map(node=>node.textContent)};")
        self.assertEqual(result["first"], "Unrelated current")
        self.assertEqual(result["names"], ["Unrelated current"])
        self.assertEqual(result["deletedNames"], ["Research"])
        self.assertTrue(result["trash"])
        self.assertFalse(result["recovery"])
        self.assertGreater(result["recoveryCount"], 0)
        self.assertTrue(result["groupEnabled"])
        self.assertIn("Existing group", result["groups"])

    def test_additive_tab_selection_deduplicates_views_and_preserves_source_order(self):
        self.ready()
        result = self.evaluate("return s.missingTabs({tabs:{a:{id:'a',path:'/alpha.txt'},x:{id:'x',path:'/gamma.txt'}},order:['x','a']},"
                               "{tabs:{other:{id:'other',path:'/alpha.txt'},x:{id:'x',path:'/beta.txt'},d:{id:'d',path:'/alpha.txt',kind:'diff',repo:'/',base:'main'}},order:['d','other','x']});")
        self.assertEqual([tab["path"] for tab in result], ["/alpha.txt", "/beta.txt"])
        self.assertEqual(result[0]["kind"], "diff")
        self.assertNotEqual(result[1]["id"], "x")
