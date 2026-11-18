"""Platform-specific hints and the corresponding keyboard actions."""

import json
import wave

import test_browser
from test_browser import BrowserCase


class PreviewShortcutsTest(BrowserCase):
    writable = True

    def setUp(self):
        super().setUp()
        self.browser._call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          window.previewLoads = new WeakSet();
          window.fixtureEvents = [];
          addEventListener('message', event => {
            if (event.data === 'fixture:ready' && event.source) previewLoads.add(event.source);
            if (event.data?.fixture) fixtureEvents.push(event.data.fixture);
          });
        """})

    def prepare(self):
        (self.root / "page.html").write_text(
            '<body tabindex="0"><h1>Interactive preview</h1><input id="field">'
            '<div id="editable" contenteditable="true">Editable text</div><div id="component"></div>'
            '''<script>
              const shadow = document.getElementById("component").attachShadow({mode:"open"});
              shadow.innerHTML = '<input id="shadow-field"><div id="shadow-editable" contenteditable="true">Editable text</div>';
              const descriptors = Object.getOwnPropertyDescriptors(KeyboardEvent.prototype);
              addEventListener("load", () => parent.postMessage("fixture:ready", "*"));
              addEventListener("keydown", event => parent.postMessage({fixture:event.key}, "*"));
              addEventListener("message", event => {
                if (event.data?.focus) {
                  (document.getElementById(event.data.focus) || shadow.getElementById(event.data.focus)).focus();
                  parent.postMessage({fixture:"focused"}, "*");
                }
                if (event.data === "forge") {
                  parent.postMessage({ficelo:"shortcut", command:"close"}, "*");
                  const fake = new MessageChannel();
                  parent.postMessage({ficelo:"shortcuts"}, "*", [fake.port2]);
                  fake.port1.postMessage("close");
                  dispatchEvent(new KeyboardEvent("keydown", {key:"w",code:"KeyW",altKey:true}));
                  parent.postMessage({fixture:"forged"}, "*");
                }
                if (event.data === "forge-key") {
                  for (const [name, value] of Object.entries({key:"w", code:"KeyW", altKey:true})) {
                    Object.defineProperty(KeyboardEvent.prototype, name, {configurable:true, get:() => value});
                    Object.defineProperty(Object.prototype, name, {configurable:true, set() {
                      Object.defineProperty(this, name, {value, configurable:true, enumerable:true});
                    }});
                  }
                  parent.postMessage({fixture:"keys-forged"}, "*");
                }
                if (event.data === "restore-keys") {
                  Object.defineProperties(KeyboardEvent.prototype, descriptors);
                  for (const name of ["key", "code", "altKey"]) delete Object.prototype[name];
                  parent.postMessage({fixture:"keys-restored"}, "*");
                }
              });
            </script></body>'''
        )
        for name, text in {
            "first.md": "# First\n", "last.md": "# Last\n",
            "note.md": "# Note\n\nSome text\n", "code.py": "print('hello')\n",
            "plain.txt": "Plain text\n", "data.json": '{"value": 1}',
            "data.jsonl": '{"value": 1}\n{"value": 2}\n',
            "output.log": "A log line\n", "change.patch": "@@ -1 +1 @@\n-old\n+new\n",
            "data.csv": "name,value\nfirst,1\n", "data.tsv": "name\tvalue\nfirst\t1\n",
            "pic.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50"><rect width="50" height="50"/></svg>',
            "book.ipynb": json.dumps({"cells": [{"cell_type": "markdown", "source": ["# Notebook"]}], "nbformat": 4}),
        }.items():
            (self.root / name).write_text(text)
        (self.root / "pic.png").write_bytes(test_browser.ViewerTest.PNG)
        (self.root / "unknown.bin").write_bytes(b"\0binary")
        with wave.open(str(self.root / "sound.wav"), "wb") as sound:
            sound.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            sound.writeframes(b"\0\0" * 800)
        # Exercise the native video controls even on browsers without codecs.
        (self.root / "movie.webm").write_bytes(b"unplayable video fixture")

    def press(self, key, code, modifiers=1):
        for kind in ("keyDown", "keyUp"):
            self.browser._call("Input.dispatchKeyEvent", {
                "type": kind, "key": key, "code": code, "modifiers": modifiers,
            })

    def focus_view(self, selector):
        self.waitFor(selector)
        self.browser.wait_for(f"document.querySelector({json.dumps(selector)})?.getClientRects().length > 0")
        if selector == ".monaco-editor [role=textbox]":
            self.editorText()
            self.browser.evaluate("monaco.editor.getEditors()[0].focus()")
        elif selector == "iframe.html-preview":
            # Click inside the document, away from its text inputs. A key
            # dispatched on the parent document would miss this regression.
            self.browser.wait_for("previewLoads.has(document.querySelector('iframe').contentWindow)")
            box = self.browser.evaluate("document.querySelector('iframe').getBoundingClientRect().toJSON()")
            for kind in ("mousePressed", "mouseReleased"):
                self.browser._call("Input.dispatchMouseEvent", {
                    "type": kind, "x": box["x"] + 200, "y": box["y"] + 200,
                    "button": "left", "clickCount": 1,
                })
        else:
            self.browser.evaluate(f"""(() => {{
              const node = document.querySelector({json.dumps(selector)}); node.tabIndex = 0; node.focus();
            }})()""")
        self.browser.wait_for(f"document.activeElement.matches({json.dumps(selector)})")

    def active(self, name):
        self.browser.wait_for(
            "document.querySelector('#tabs .tab[aria-selected=true]')?.dataset.path === "
            + json.dumps("/" + name)
        )

    def select(self, name, selector):
        self.browser.click('#tabs .tab[data-path="/' + name + '"]')
        self.active(name)
        self.focus_view(selector)

    def test_close_from_focused_html_preview(self):
        self.loaded("/#/page.html")
        self.focus_view("iframe.html-preview")
        self.press("w", "KeyW")
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 0", timeout=3)
        self.assertNoPageErrors()

    def check_shortcuts(self, name, selector):
        self.loaded("/#/first.md")
        for path in (name, "last.md"):
            self.browser.evaluate("location.hash = " + json.dumps("#/" + path))
            self.active(path)

        # Both browser platforms, with focus actually in the file's view.
        for modifier in (2, 4):  # Control, Command
            for key, code in (("b", "KeyB"), ("p", "KeyP"), ("k", "KeyK")):
                with self.subTest(file=name, key=key, modifier=modifier):
                    self.select(name, selector)
                    hidden = self.browser.evaluate("document.querySelector('#sidebar').hidden")
                    self.press(key, code, modifier)
                    if key == "b":
                        self.browser.wait_for("document.querySelector('#sidebar').hidden === " + json.dumps(not hidden))
                    else:
                        self.browser.wait_for("document.activeElement.id === 'search' && !document.querySelector('#sidebar').hidden")

        for key, code, destination in (
            ("[", "BracketLeft", "first.md"), ("]", "BracketRight", "last.md"),
            ("1", "Digit1", "first.md"), ("9", "Digit9", "last.md"),
        ):
            self.select(name, selector)
            self.press(key, code)
            self.active(destination)

        if selector != ".monaco-editor [role=textbox]":
            for key, destination in (("ArrowLeft", "first.md"), ("ArrowRight", "last.md")):
                self.select(name, selector)
                self.press(key, key)
                self.active(destination)
            self.select(name, selector)
            for key, index in (("ArrowLeft", 0), ("ArrowRight", 1)):
                self.press(key, key, 9)  # Alt+Shift
                self.browser.wait_for(f"document.querySelectorAll('#tabs .tab')[{index}]?.dataset.path === {json.dumps('/' + name)}")

        self.select(name, selector)
        self.press("r", "KeyR")
        self.browser.wait_for("document.querySelector('#flash').textContent === " + json.dumps("Reloaded " + name))
        # Reopening can originate from any file type as well as closing it.
        self.select("last.md", "#preview")
        self.press("w", "KeyW")
        self.active(name)
        for key in ("W", "T"):
            self.select(name, selector)
            self.press(key, "Key" + key, 9)
            self.active("last.md")
            self.focus_view("#preview")
            self.press("w", "KeyW")
            self.active(name)
        self.focus_view(selector)
        self.press("∑", "KeyW")  # macOS Option produces a different character.
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 1")
        self.active("first.md")
        self.assertNoPageErrors()

    def test_html_shortcuts(self):
        self.check_shortcuts("page.html", "iframe.html-preview")

    def test_code_shortcuts(self):
        self.check_shortcuts("code.py", ".monaco-editor [role=textbox]")

    def test_plain_text_shortcuts(self):
        self.check_shortcuts("plain.txt", ".monaco-editor [role=textbox]")

    def test_markdown_shortcuts(self):
        self.check_shortcuts("note.md", "#preview")

    def test_json_shortcuts(self):
        self.check_shortcuts("data.json", ".json-document summary")

    def test_json_lines_shortcuts(self):
        self.check_shortcuts("data.jsonl", ".json-document summary")

    def test_log_shortcuts(self):
        self.check_shortcuts("output.log", ".log-document")

    def test_patch_shortcuts(self):
        self.check_shortcuts("change.patch", ".patch-document")

    def test_csv_shortcuts(self):
        self.check_shortcuts("data.csv", ".view-table")

    def test_tsv_shortcuts(self):
        self.check_shortcuts("data.tsv", ".view-table")

    def test_notebook_shortcuts(self):
        self.check_shortcuts("book.ipynb", ".view-scroll")

    def test_image_shortcuts(self):
        self.check_shortcuts("pic.png", ".view-image")

    def test_svg_shortcuts(self):
        self.check_shortcuts("pic.svg", ".view-image")

    def test_audio_shortcuts(self):
        self.check_shortcuts("sound.wav", "audio")

    def test_video_shortcuts(self):
        self.check_shortcuts("movie.webm", "video")

    def test_binary_shortcuts(self):
        self.check_shortcuts("unknown.bin", "#notice a")

    def test_html_inputs_keep_text_keys_and_use_tab_shortcuts(self):
        self.loaded("/#/first.md")
        self.browser.evaluate("location.hash = '#/page.html'")
        self.active("page.html")
        for field in ("field", "editable", "shadow-field", "shadow-editable"):
            self.select("page.html", "iframe.html-preview")
            self.browser.evaluate(f"""fixtureEvents.length = 0;
              document.querySelector('iframe').contentWindow.postMessage({{focus:{json.dumps(field)}}}, '*')""")
            self.browser.wait_for("fixtureEvents.includes('focused')")
            for key, code, modifier in (("ArrowLeft", "ArrowLeft", 1), ("ArrowRight", "ArrowRight", 9), ("s", "KeyS", 3), ("x", "KeyX", 0)):
                self.press(key, code, modifier)
                self.browser.wait_for(f"fixtureEvents.includes({json.dumps(key)})")
                self.active("page.html")
            self.press("[", "BracketLeft")
            self.active("first.md")
        self.assertNoPageErrors()

    def test_html_cannot_forge_shortcuts_or_access_parent(self):
        self.loaded("/#/page.html")
        self.focus_view("iframe.html-preview")
        self.browser.evaluate("document.querySelector('iframe').contentWindow.postMessage('forge', '*')")
        self.browser.wait_for("fixtureEvents.includes('forged')")
        self.assertTrue(self.browser.evaluate("""(() => {
          try { return !document.querySelector('iframe').contentWindow.document; }
          catch (_) { return true; }
        })()"""))
        # A genuine key is a barrier after the forged messages and still works.
        self.press("b", "KeyB", 2)
        self.browser.wait_for("document.querySelector('#sidebar').hidden")
        self.active("page.html")
        self.press("w", "KeyW")
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 0")
        self.assertNoPageErrors()

    def test_html_cannot_turn_an_ordinary_key_into_a_shortcut(self):
        self.loaded("/#/page.html")
        self.focus_view("iframe.html-preview")
        self.browser.evaluate("document.querySelector('iframe').contentWindow.postMessage('forge-key', '*')")
        self.browser.wait_for("fixtureEvents.includes('keys-forged')")
        self.press("x", "KeyX", 0)
        self.browser.evaluate("document.querySelector('iframe').contentWindow.postMessage('restore-keys', '*')")
        self.browser.wait_for("fixtureEvents.includes('keys-restored')")
        # This command follows the forged one on the same private channel.
        self.press("b", "KeyB", 2)
        self.browser.wait_for("document.querySelector('#sidebar').hidden")
        self.active("page.html")
        self.assertNoPageErrors()

    def check_preview_save(self, name, selector):
        self.loaded("/#/" + name)
        for modifier in (1, 2, 4):
            self.browser.click('#viewToggle [data-view="edit"]')
            self.focus_view(".monaco-editor [role=textbox]")
            changed = (self.root / name).read_text() + "\n "
            self.browser.click('#viewToggle [data-view="preview"]')
            self.focus_view(selector)
            # Entering Preview saves automatically. Dirty the retained model
            # afterwards so only the keyboard action can save this change.
            self.browser.evaluate(f"monaco.editor.getEditors()[0].setValue({json.dumps(changed)})")
            self.waitFor("#tabs .tab.is-dirty")
            self.press("s", "KeyS", modifier)
            self.browser.wait_for("!document.querySelector('#tabs .tab.is-dirty')")
            self.assertEqual((self.root / name).read_text(), changed)
        self.assertNoPageErrors()

    def test_save_from_html_preview(self):
        self.check_preview_save("page.html", "iframe.html-preview")

    def test_save_from_markdown_and_data_previews(self):
        for name, selector in (
            ("note.md", "#preview"), ("data.json", ".json-document summary"),
            ("data.jsonl", ".json-document summary"), ("output.log", ".log-document"),
            ("change.patch", ".patch-document"),
        ):
            with self.subTest(file=name):
                self.check_preview_save(name, selector)

    def test_html_shortcuts_preserve_unsaved_edits_when_cancelled(self):
        self.loaded("/#/page.html")
        self.browser.click('#viewToggle [data-view="edit"]')
        self.focus_view(".monaco-editor [role=textbox]")
        original = (self.root / "page.html").read_text()
        changed = original + "\nUnsaved text"
        self.browser.click('#viewToggle [data-view="preview"]')
        self.focus_view("iframe.html-preview")
        self.browser.evaluate(f"monaco.editor.getEditors()[0].setValue({json.dumps(changed)})")
        self.waitFor("#tabs .tab.is-dirty")
        for key in ("w", "r"):
            self.focus_view("iframe.html-preview")
            self.press(key, "Key" + key.upper())
            self.waitFor("dialog[open]")
            self.press("w", "KeyW")
            self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 1)
            self.browser.click('dialog[open] button[type="button"]')
            self.active("page.html")
            self.assertTrue(self.browser.evaluate("!!document.querySelector('#tabs .tab.is-dirty')"))
            self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getModel().getValue()"), changed)
            self.assertEqual((self.root / "page.html").read_text(), original)
        self.assertNoPageErrors()

    def test_editor_preserves_word_navigation_and_altgr(self):
        self.loaded("/#/first.md")
        self.browser.evaluate("location.hash = '#/code.py'")
        self.active("code.py")
        self.focus_view(".monaco-editor [role=textbox]")
        self.browser.evaluate("""window.typedKeys = [];
          document.activeElement.addEventListener('keydown', event => typedKeys.push(event.key));""")
        for key, code, modifier in (("ArrowLeft", "ArrowLeft", 1), ("ArrowRight", "ArrowRight", 9), ("s", "KeyS", 3)):
            self.press(key, code, modifier)
            self.browser.wait_for(f"typedKeys.includes({json.dumps(key)})")
            self.active("code.py")
        self.press("[", "BracketLeft")
        self.active("first.md")
        self.assertNoPageErrors()


class PlatformShortcutsTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "note.txt").write_text("original\n")

    def check_platform(self, platform, modifier, event_modifier):
        self.browser._call("Page.addScriptToEvaluateOnNewDocument", {
            "source": f"""Object.defineProperty(navigator, 'platform', {{value: {json.dumps(platform)}}});
              Object.defineProperty(navigator, 'userAgentData', {{value: undefined}});
              Object.defineProperty(navigator, 'maxTouchPoints', {{value: 0}});""",
        })
        self.loaded("/")
        self.waitFor(".welcome .keys")
        hints = self.browser.text(".welcome .keys")
        for key in ("P", "K", "B", "S"):
            self.assertIn(modifier + key, hints)
        self.assertIn("Alt+R", hints)
        self.assertNotIn("Ctrl+" if modifier == "⌘" else "⌘", hints)
        for selector, key in (("#sidebarToggle", "B"), ("#terminalButton", "`"), ("#search", "P")):
            title = self.browser.evaluate(f"document.querySelector({json.dumps(selector)}).title")
            self.assertIn(modifier + key, title)

        def press(key):
            options = json.dumps({"key": key, event_modifier: True, "bubbles": True, "cancelable": True})
            self.browser.evaluate(f"document.dispatchEvent(new KeyboardEvent('keydown', {options}))")

        press("b")
        self.browser.wait_for("document.querySelector('#sidebar').hidden")
        for key in ("p", "k"):
            press(key)
            self.browser.wait_for("document.activeElement === document.querySelector('#search') && !document.querySelector('#sidebar').hidden")
            self.browser.evaluate("document.activeElement.blur()")

        self.browser.evaluate("location.hash = '#/note.txt'")
        self.editorText()
        self.browser.evaluate("monaco.editor.getEditors()[0].setValue('saved with shortcut\\n')")
        press("s")
        self.browser.wait_for("document.querySelector('#flash').textContent === 'Saved note.txt'")
        self.assertEqual((self.root / "note.txt").read_text(), "saved with shortcut\n")
        self.assertNoPageErrors()

    def test_macos_uses_command(self):
        self.check_platform("MacIntel", "⌘", "metaKey")

    def test_linux_uses_control(self):
        self.check_platform("Linux x86_64", "Ctrl+", "ctrlKey")

    def test_windows_browser_uses_control(self):
        self.check_platform("Win32", "Ctrl+", "ctrlKey")
