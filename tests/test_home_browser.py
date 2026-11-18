"""Home preferences validate when Settings closes without moving the explorer."""

import json
import os
from pathlib import Path
from unittest import mock

from test_browser import Browser, BrowserCase


class HomeChooserBrowserTest(BrowserCase):
    writable = False  # Navigation preferences also work on read-only servers.
    home_label = "Configure Home folder (default folder to display when you open ficelo)"

    def prepare(self):
        self.user_home = self.root / "users/person"
        for path in (self.user_home / "notes", self.root / "work", self.root / "blocked"):
            path.mkdir(parents=True)
            (path / "marker.txt").write_text("a folder marker\n")
        (self.root / "work/note.txt").write_text("a file, not a folder\n")
        patch = mock.patch.dict(os.environ, {"HOME": str(self.user_home)})
        patch.start()
        self.addCleanup(patch.stop)

    def open_settings(self):
        if self.browser.evaluate("document.querySelector('#settings').hidden"):
            self.browser.click("#settingsToggle")
        self.waitFor("#settings #homePath")

    def type_path(self, value):
        # Closing/rebuilding Settings must read the actual input; no typing
        # listener should be needed to preserve or save this draft.
        self.browser.evaluate(
            "document.querySelector('#homePath').value = " + json.dumps(value)
        )

    def key(self, key, keycode):
        for event in ("keyDown", "keyUp"):
            params = {"type": event, "key": key, "code": key, "windowsVirtualKeyCode": keycode}
            if event == "keyDown" and key == "Enter":
                params.update(text="\r", unmodifiedText="\r")
            self.browser._call("Input.dispatchKeyEvent", params)

    def close_settings(self, action="gear"):
        if action == "gear":
            self.browser.click("#settingsToggle")
        elif action == "escape":
            self.key("Escape", 27)
        else:
            self.browser.click("body")
        self.browser.wait_for("document.querySelector('#settings').hidden")

    def saved(self, path):
        self.browser.wait_for(
            "(async () => (await (await fetch('/api/state')).json()).settings['ficelo.home'] === "
            + json.dumps(path) + ")()",
            what="the validated Home preference to be saved",
        )

    def tree_paths(self):
        return self.browser.evaluate("[...document.querySelectorAll('#tree .row')].map(row => row.dataset.path)")

    def error(self, raw):
        shown = raw or '""'
        self.browser.wait_for(
            "document.querySelector('#flash').textContent === "
            + json.dumps(f"Home folder change failed - {shown} is not a valid folder")
        )

    def test_inline_draft_survives_rebuilds_and_saves_only_on_close(self):
        self.loaded()
        self.waitFor('#tree [data-path="/users/person/notes"]')
        self.assertEqual(self.browser.evaluate("document.title"), "Home")
        self.browser.evaluate("document.querySelector('#sessionName').value = 'Reading'; document.querySelector('#sessionName').dispatchEvent(new Event('change'))")
        self.browser.wait_for("document.title === 'Reading'")
        self.browser.evaluate("document.querySelector('#sessionName').value = ''; document.querySelector('#sessionName').dispatchEvent(new Event('change'))")
        self.browser.wait_for("document.title === 'Home'")
        before = self.tree_paths()
        self.open_settings()
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), str(self.user_home.resolve()))
        self.assertIn(self.home_label, self.browser.evaluate(
            "[...document.querySelectorAll('#settings label')].map(label => label.textContent.trim())"
        ))
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#setHome, .home-dialog')"))
        self.assertFalse(self.browser.evaluate(
            "[...document.querySelectorAll('#settings button')].some(button => /home/i.test(button.textContent))"
        ))
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'), info = api.info;
          window.homeChecks = 0;
          api.info = (...args) => { if (args[0] === '/work') homeChecks++; return info(...args); };
        })()""")
        draft = str((self.root / "work").resolve())
        self.type_path("/work")
        self.browser.evaluate("document.querySelector('#homePath').dispatchEvent(new Event('input', {bubbles:true}))")
        self.type_path(draft)
        self.browser.evaluate("document.querySelector('#homePath').focus(); document.querySelector('#homePath').blur()")
        self.browser.evaluate("document.querySelector('#homePath').focus()")
        self.key("Enter", 13)
        self.assertFalse(self.browser.evaluate("document.querySelector('#settings').hidden"))
        self.assertEqual(self.browser.evaluate("homeChecks"), 0)
        self.assertNotEqual(self.server.settings.read().get("ficelo.home"), "/work")
        self.browser.click("#settings .set-choice button:last-child")
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), draft)
        self.assertFalse(self.browser.evaluate("document.querySelector('#settings').hidden"))
        self.browser.click("#settings .set-reset")
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), draft)
        self.assertFalse(self.browser.evaluate("document.querySelector('#settings').hidden"))
        self.assertEqual(self.browser.evaluate("homeChecks"), 0)
        self.close_settings("gear")
        self.saved("/work")
        self.assertEqual(self.tree_paths(), before, "changing Home must not navigate the explorer")
        self.assertEqual(self.browser.evaluate("document.title"), str(self.user_home.resolve()))

        for raw, expected, action in (
            ("users/person/notes", "/users/person/notes", "outside"),
            ("~", "/users/person", "escape"),
            ("~/notes", "/users/person/notes", "gear"),
        ):
            with self.subTest(raw=raw, close=action):
                self.open_settings()
                self.type_path(raw)
                self.close_settings(action)
                self.saved(expected)
                self.assertEqual(self.tree_paths(), before)
                self.assertEqual(self.browser.evaluate("document.title"),
                                 "Home" if expected == "/users/person" else str(self.user_home.resolve()))
        second = Browser()
        self.addCleanup(second.close)
        second.navigate(self.url())
        second.wait_for(
            "document.body.dataset.ficeloReady === '1' && "
            "document.querySelector('#tree [data-path=\"/users/person/notes/marker.txt\"]')"
        )
        self.assertEqual(second.evaluate("document.title"), "Home")
        self.assertEqual(second.errors(), [])
        self.browser.click("#homeButton")
        self.waitFor('#tree [data-path="/users/person/notes/marker.txt"]')
        self.assertEqual(self.browser.evaluate("document.querySelector('.crumb.is-root')?.textContent"), "notes")
        self.assertEqual(self.browser.evaluate("document.title"), "Home")
        self.browser.click('#tree .row[data-path="/users/person/notes/marker.txt"]')
        self.browser.wait_for("document.title === 'marker.txt'")
        self.assertNoPageErrors()

    def test_narrowed_root_prefix_is_unambiguous_when_displayed_and_saved(self):
        # Use the resolved root: macOS temporary folders can have a /private
        # prefix while Linux commonly uses /tmp directly.
        served_root = self.server.config.root
        canonical = str(served_root) + "/nested"
        physical = served_root / canonical.lstrip("/")
        physical.mkdir(parents=True)
        (physical / "marker.txt").write_text("intended Home\n")
        (self.root / "nested").mkdir()
        self.server.settings.put("ficelo.home", canonical)
        self.loaded()
        before = self.tree_paths()
        self.open_settings()
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), str(physical))
        self.type_path(str((self.root / "work").resolve()))
        self.close_settings()
        self.saved("/work")
        self.open_settings()
        self.type_path(str(physical))
        self.close_settings()
        self.saved(canonical)
        self.assertEqual(self.tree_paths(), before)
        self.open_settings()
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), str(physical))
        self.close_settings()
        self.assertEqual(self.server.settings.read()["ficelo.home"], canonical)
        self.assertNoPageErrors()

    def test_invalid_files_missing_blank_and_unreadable_folders_restore_home(self):
        from ficelo import files

        self.server.settings.put("ficelo.home", "/users/person")
        self.loaded()
        before = self.tree_paths()
        for raw in ("", "  ", "/missing", "/work/note.txt"):
            with self.subTest(raw=raw):
                self.open_settings()
                self.type_path(raw)
                self.close_settings()
                self.error(raw)
                self.assertEqual(self.server.settings.read()["ficelo.home"], "/users/person")
                self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), str(self.user_home.resolve()))
        listing = files.listing

        def unreadable(directory, *args, **kwargs):
            if Path(directory) == (self.root / "blocked").resolve():
                raise PermissionError("folder is unreadable")
            return listing(directory, *args, **kwargs)

        with mock.patch.object(files, "listing", side_effect=unreadable):
            self.open_settings()
            self.type_path("/blocked")
            self.close_settings("escape")
            self.error("/blocked")
        self.assertEqual(self.server.settings.read()["ficelo.home"], "/users/person")
        self.assertEqual(self.tree_paths(), before)
        self.open_settings()
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), str(self.user_home.resolve()))
        self.assertCountEqual(self.browser.errors(), [
            "Failed to load resource: the server responded with a status of 404 (Not Found)",
            "Failed to load resource: the server responded with a status of 403 (Forbidden)",
        ])

    def test_reopening_settings_and_newer_drafts_supersede_pending_validation(self):
        self.server.settings.put("ficelo.home", "/users/person")
        self.loaded()
        before = self.tree_paths()
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'), info = api.info;
          let hold = true; window.homeCheckHeld = false; window.homeCheckReturned = false;
          const gate = new Promise(resolve => { window.releaseHomeCheck = resolve; });
          api.info = async (...args) => {
            const result = await info(...args);
            if (args[0] === '/work' && hold) {
              hold = false; homeCheckHeld = true; await gate; homeCheckReturned = true;
            }
            return result;
          };
        })()""")
        self.open_settings()
        self.type_path("/work")
        self.close_settings()
        self.browser.wait_for("homeCheckHeld")
        self.open_settings()
        self.type_path("~/notes")
        self.browser.evaluate("releaseHomeCheck()")
        self.browser.wait_for("homeCheckReturned")
        self.browser.evaluate("""(async () => {
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await (await import('/js/util.js')).flushStore();
        })()""")
        self.assertEqual(self.server.settings.read()["ficelo.home"], "/users/person")
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), "~/notes")
        self.close_settings("escape")
        self.saved("/users/person/notes")
        self.assertEqual(self.tree_paths(), before)
        self.assertNoPageErrors()

    def test_failed_readability_check_can_be_retried_without_moving_the_workspace(self):
        (self.root / "blocked/deep").mkdir()
        (self.root / "blocked/deep/leaf.txt").write_text("expanded folder\n")
        self.server.settings.put("ficelo.home", "/users/person")
        self.server.settings.put("ficelo.workspace.before", {
            "id": "before", "name": "Before", "root": "/blocked", "mode": "files",
            "tabs": {}, "order": [], "expanded": ["/blocked", "/blocked/deep"],
            "state": {"ficelo.treeroot": "/blocked"},
        })
        self.loaded("/?session=before")
        self.waitFor('#tree [data-path="/blocked/deep/leaf.txt"]')
        before = self.tree_paths()
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'), tree = api.tree;
          let fail = true; window.homeTreeReads = 0;
          api.tree = async (...args) => {
            if (args[0] === '/work') {
              homeTreeReads++;
              if (fail) { fail = false; throw new Error('temporary listing failure'); }
            }
            return tree(...args);
          };
        })()""")
        self.open_settings()
        self.type_path("/work")
        self.close_settings()
        self.error("/work")
        self.assertEqual(self.server.settings.read()["ficelo.home"], "/users/person")
        self.assertEqual(self.tree_paths(), before)
        self.open_settings()
        self.assertEqual(self.browser.evaluate("document.querySelector('#homePath').value"), str(self.user_home.resolve()))
        self.type_path("/work")
        self.close_settings("outside")
        self.saved("/work")
        self.assertEqual(self.browser.evaluate("homeTreeReads"), 2)
        self.assertEqual(self.tree_paths(), before)
        settings = self.server.settings.read()
        self.assertEqual(settings["ficelo.workspace.before"]["root"], "/blocked")
        self.assertEqual(settings["ficelo.workspace.before"]["state"]["ficelo.treeroot"], "/blocked")
        self.assertNoPageErrors()
