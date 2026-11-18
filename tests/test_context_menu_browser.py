"""Context actions keep their clicked paths and document identities."""

import json
from urllib.parse import quote
from urllib.request import urlopen

from test_browser import BrowserCase


class HeaderCopyBrowserTest(BrowserCase):
    def prepare(self):
        self.folder = self.root / "a space's µ"
        self.folder.mkdir()
        (self.folder / "note.md").write_text("# A document\n\nSelectable text.\n")

    def click_copy(self):
        point = self.browser.evaluate("""(() => {
          const box = document.querySelector('#copyPath').getBoundingClientRect();
          return {x: box.x + box.width / 2, y: box.y + box.height / 2};
        })()""")
        for kind in ("mousePressed", "mouseReleased"):
            self.browser._call("Input.dispatchMouseEvent", {
                "type": kind, **point, "button": "left", "clickCount": 1,
            })

    def test_header_copy_uses_clipboard_fallbacks(self):
        self.loaded("/#/" + quote("a space's µ/note.md"))
        self.browser._call("Emulation.setFocusEmulationEnabled", {"enabled": True})
        self.browser._call("Browser.grantPermissions", {
            "origin": self.url().rstrip("/"),
            "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
        })
        self.browser.evaluate("window.nativeClipboard = navigator.clipboard")
        for mode in ("native", "missing", "denied"):
            with self.subTest(mode=mode):
                self.browser.evaluate("""(async () => {
                  await nativeClipboard.writeText('not copied');
                  const mode = """ + json.dumps(mode) + """;
                  const clipboard = mode === 'native' ? nativeClipboard : mode === 'missing' ? undefined
                    : {writeText: async () => { throw new DOMException('Denied', 'NotAllowedError'); }};
                  Object.defineProperty(navigator, 'clipboard', {value: clipboard, configurable: true});
                  document.querySelector('#flash').textContent = '';
                })()""")
                self.click_copy()
                self.browser.wait_for("document.querySelector('#flash').textContent.includes(" + json.dumps(str(self.folder)) + ")")
                self.assertEqual(self.browser.evaluate("nativeClipboard.readText()"), str(self.folder))
        self.assertNoPageErrors()

    def test_header_copy_uses_current_folder_without_an_open_file(self):
        self.loaded()
        self.browser.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.copied = text; }}, configurable: true})")
        self.click_copy()
        self.browser.wait_for("window.copied === " + json.dumps(str(self.root)))
        self.browser.evaluate("document.querySelector('#tree .is-dir').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
        self.browser.wait_for("!!document.querySelector('#tree .row[data-path$=\"/note.md\"]')")
        self.click_copy()
        self.browser.wait_for("window.copied === " + json.dumps(str(self.folder)))
        self.assertNoPageErrors()

    def test_blocked_copy_offers_selected_text_without_claiming_success(self):
        self.loaded("/#/" + quote("a space's µ/note.md"))
        self.browser.evaluate("Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})")
        for fails in ("return false", "throw new Error('Copy blocked')"):
            with self.subTest(fails=fails):
                self.browser.evaluate("document.execCommand = () => {" + fails + ";}")
                self.click_copy()
                self.waitFor("dialog[open] .dialog-input")
                self.assertEqual(self.browser.evaluate("""(() => {
                  const input = document.querySelector('dialog .dialog-input');
                  return [input.value, input.selectionStart, input.selectionEnd,
                    document.querySelectorAll('body > textarea').length];
                })()"""), [str(self.folder), 0, len(str(self.folder)), 0])
                self.browser.click('dialog button[type="button"]')
                self.browser.wait_for("!document.querySelector('dialog[open]')")
                self.assertFalse(self.browser.evaluate("document.querySelector('#flash').textContent.startsWith('Copied ')"))
        self.assertNoPageErrors()


class ContextMenuBrowserTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "docs").mkdir()
        (self.root / "docs/note.md").write_text("# A document\n\nSelectable text.\n")
        (self.root / "docs/a space's µ.HTML").write_text("<h1>A page</h1>")
        (self.root / "docs/diagram.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
        (self.root / "first.txt").write_text("first file\n")

    def loaded(self, *args, **kwargs):
        result = super().loaded(*args, **kwargs)
        self.browser.evaluate("Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text => { window.copied = text; }}, configurable:true})")
        return result

    def menu(self, selector):
        self.browser.evaluate("document.querySelector(" + json.dumps(selector) + ").dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true,button:2,clientX:100,clientY:140}))")

    def menu_path(self, path, selector="#tree .row"):
        self.browser.evaluate("[...document.querySelectorAll(" + json.dumps(selector) + ")].find(row => row.dataset.path === " + json.dumps(path) + ").dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true,button:2,clientX:100,clientY:140}))")

    def action(self, label):
        self.browser.evaluate("[...document.querySelectorAll('.menu-item')].find(item => item.textContent === " + json.dumps(label) + ").click()")

    def assert_menu(self, labels, disabled=()):
        self.assertEqual(self.browser.evaluate("[...document.querySelector('.menu').children].map(item => item.classList.contains('menu-divider') ? '|' : item.textContent)"), labels)
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('.menu-item:disabled')].map(item => item.textContent)"), list(disabled))

    def mutations(self):
        return ["|", "New File", "New Folder", "|", "Rename", "Delete"] if self.writable else []

    def test_explorer_and_breadcrumb_menus_use_the_clicked_folder(self):
        self.loaded("/#/docs/note.md")
        selected = self.browser.evaluate("document.querySelector('#tree [aria-selected=true]').dataset.path")
        self.menu_path("/first.txt")
        self.assert_menu(["Copy Full Path", "Copy File Name", "|", "Info", *self.mutations()])
        self.assertEqual(self.browser.evaluate("document.querySelector('#tree [aria-selected=true]').dataset.path"), selected)
        self.action("Copy Full Path")
        self.assertEqual(self.browser.evaluate("window.copied"), str(self.root / "first.txt"))
        self.menu_path("/docs")
        self.assert_menu(["Copy Full Path", "Copy Folder Name", "|", "Info", "|", "Set as Explorer Root", *self.mutations()])
        self.assertEqual(self.browser.evaluate("document.querySelector('#tree [aria-selected=true]').dataset.path"), selected)
        self.action("Copy Folder Name")
        self.assertEqual(self.browser.evaluate("window.copied"), "docs")
        self.menu_path("/docs")
        self.action("Set as Explorer Root")
        self.browser.wait_for("document.querySelector('.crumb.is-root')?.textContent === 'docs'")
        self.menu("#tree")
        self.assert_menu(["Copy Full Path", "Copy Folder Name", "|", "Info", *self.mutations()])
        self.menu(".crumb.is-root")
        self.assert_menu(["Copy Full Path", "Copy Folder Name"])
        self.menu(".crumb")
        self.assert_menu(["Copy Full Path", "Copy Folder Name", "|", "Set as Explorer Root"])
        self.action("Copy Full Path")
        self.assertEqual(self.browser.evaluate("window.copied"), str(self.root))
        self.menu(".crumb")
        self.action("Set as Explorer Root")
        self.browser.wait_for("document.querySelector('.crumb.is-root')?.textContent === " + json.dumps(self.root.name))
        self.menu("#tree")
        self.assert_menu(["Copy Full Path", "Copy Folder Name", "|", "Info", *self.mutations()],
                         disabled=["Rename", "Delete"] if self.writable else [])
        self.assertNoPageErrors()

    def test_raw_body_and_tab_actions_keep_counts_order_and_encoded_paths(self):
        path = "/docs/a space's µ.HTML"
        self.loaded("/#" + quote(path))
        self.browser.wait_for("!!document.querySelector('iframe.html-preview')")
        self.browser.evaluate("window.open = (...args) => { window.openedRaw = args; }")
        self.menu("#viewerHost")
        copies = ["Copy Full Path", "Copy File Name"]
        close = ["|", "Close Tab", "Close to the Right", "Close All 1"]
        entry = ["|", "Info", "|", "Open Raw in New Tab", *self.mutations()]
        self.assert_menu([*copies, *close, *entry], disabled=["Close to the Right"])
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('.menu-item')].find(item => item.textContent === 'Close All 1').classList.contains('is-danger')"))
        self.action("Open Raw in New Tab")
        raw = self.browser.evaluate("window.openedRaw")
        self.assertEqual(raw, ["/preview" + quote(path, safe="/~()*!.'-"), "_blank", "noopener,noreferrer"])
        for requested, mime in ((path, "text/html"), ("/docs/diagram.svg", "image/svg+xml")):
            if requested != path:
                self.menu_path(requested)
                self.action("Open Raw in New Tab")
                raw = self.browser.evaluate("window.openedRaw")
            with urlopen(self.url(raw[0]), timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertEqual(response.headers["Content-Disposition"], "inline")
                self.assertEqual(response.headers["Content-Security-Policy"], "sandbox allow-scripts")
                self.assertEqual(response.read(), (self.root / requested.lstrip("/")).read_bytes())
        self.browser.evaluate("location.hash = '#/docs/note.md'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected=true]')?.dataset.path === '/docs/note.md'")
        self.menu_path(path, "#tabs .tab")
        self.assert_menu([*copies, "|", "Move Left", "Move Right", "|", "Close Tab", "Close 1 to the Right", "Close All 2", *entry], disabled=["Move Left"])
        self.action("Close 1 to the Right")
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 1")
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs .tab').dataset.path"), path)
        self.assertNoPageErrors()

    def test_selected_text_keeps_native_body_menu_but_tree_and_modifiers_do_not(self):
        self.loaded("/#/docs/note.md")
        self.browser.evaluate("""(() => { const range = document.createRange(); range.selectNodeContents(document.querySelector('#preview h1'));
          window.getSelection().removeAllRanges(); window.getSelection().addRange(range); })()""")
        self.menu("#preview h1")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"))
        self.menu_path("/first.txt")
        self.assert_menu(["Copy Full Path", "Copy File Name", "|", "Info", *self.mutations()])
        self.browser.evaluate("window.getSelection().removeAllRanges(); document.body.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}))")
        self.browser.evaluate("document.querySelector('#preview').dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true,shiftKey:true,ctrlKey:true,clientX:100,clientY:140}))")
        self.assert_menu(["Copy Full Path", "Copy File Name", "|", "Close Tab", "Close to the Right", "Close All 1", "|", "Info", *self.mutations()], disabled=["Close to the Right"])
        self.browser.evaluate("document.body.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}))")
        self.browser.click('#viewToggle [data-view="edit"]')
        self.browser.wait_for("!!document.querySelector('#editorHost .view-line')")
        self.menu("#editorHost .view-line")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"))
        self.assertNoPageErrors()


class ReadOnlyContextMenuBrowserTest(ContextMenuBrowserTest):
    writable = False
