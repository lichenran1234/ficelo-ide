"""Reader side controls and draggable panel boundaries in the real app."""

from test_browser import BrowserCase


class PanelLayoutTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "code.py").write_text("\n".join(f"value_{i} = {i}" for i in range(400)))
        (self.root / "note.md").write_text("# Start\n\n" + "A paragraph.\n\n" * 40 + "## End\n\nText.\n")

    def state(self):
        return self.browser.evaluate("""(() => {
          const button = document.querySelector('#tocToggle');
          const editor = monaco.editor.getEditors()[0];
          return {label:button.querySelector('span').textContent,
            visible:button.getClientRects().length > 0, expanded:button.getAttribute('aria-expanded'),
            map:editor.getOption(monaco.editor.EditorOption.minimap).enabled,
            mapWidth:editor.getLayoutInfo().minimap.minimapWidth,
            contents:!document.querySelector('#tocPane').hidden};
        })()""")

    def minimap_checkbox(self, click=False):
        return self.browser.evaluate("""(() => {
          const row = [...document.querySelectorAll('#settings .set-row')]
            .find(row => row.querySelector('.set-label')?.textContent === 'Minimap');
          const box = row.querySelector('input');
          %s
          return box.checked;
        })()""" % ("box.click();" if click else ""))

    def drag(self, selector, delta, cancel=False):
        point = self.browser.evaluate(f"""(() => {{
          const box = document.querySelector({selector!r}).getBoundingClientRect();
          return {{x:box.left + box.width / 2,y:box.top + box.height / 2}};
        }})()""")
        self.browser._call("Input.dispatchMouseEvent", {"type": "mousePressed", **point, "button": "left", "buttons": 1, "clickCount": 1})
        point["x"] += delta
        self.browser._call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point, "buttons": 1})
        if cancel:
            self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true,cancelable:true}))")
        self.browser._call("Input.dispatchMouseEvent", {"type": "mouseReleased", **point, "button": "left", "buttons": 0, "clickCount": 1})

    def width(self, selector):
        return self.browser.evaluate(f"document.querySelector({selector!r}).getBoundingClientRect().width")

    def test_right_control_and_settings_stay_in_sync_across_views(self):
        self.loaded("/#/code.py")
        self.assertEqual((self.state()["label"], self.state()["expanded"]), ("Minimap", "true"))
        self.browser.click("#tocToggle")
        self.assertFalse(self.state()["map"])
        self.assertEqual(self.state()["mapWidth"], 0)
        self.assertTrue(self.browser.evaluate("document.querySelector('#minimapResize').hidden"))
        self.browser.click("#settingsToggle")
        self.assertFalse(self.minimap_checkbox())
        self.assertTrue(self.minimap_checkbox(click=True))
        self.assertEqual(self.state()["expanded"], "true")
        self.browser.click("#settingsToggle")

        self.browser.evaluate("location.hash = '#/note.md'")
        self.browser.wait_for("!document.querySelector('#previewHost').hidden")
        self.assertEqual(self.state()["label"], "Contents")
        self.browser.click("#tocToggle")
        self.assertFalse(self.state()["contents"])
        self.browser.click("#settingsToggle")
        self.assertFalse(self.browser.evaluate("document.querySelector('#setting-toc').checked"))
        self.browser.click("#setting-toc")
        self.assertTrue(self.state()["contents"])
        self.assertEqual(self.state()["expanded"], "true")
        self.browser.click("#settingsToggle")
        self.browser.click("#tocHide")
        self.browser.click("#settingsToggle")
        self.assertFalse(self.browser.evaluate("document.querySelector('#setting-toc').checked"))
        self.browser.click("#settings .set-reset")
        self.assertTrue(self.browser.evaluate("document.querySelector('#setting-toc').checked"))
        self.assertTrue(self.state()["contents"])
        self.browser.click("#settingsToggle")

        self.browser.click('#viewToggle [data-view="edit"]')
        self.assertEqual(self.state()["label"], "Minimap")
        self.browser.click('#viewToggle [data-view="split"]')
        self.assertFalse(self.state()["visible"])
        self.assertFalse(self.state()["map"])
        self.browser.click("#settingsToggle")
        self.minimap_checkbox(click=True)
        self.minimap_checkbox(click=True)
        self.assertFalse(self.state()["map"], "Settings must keep the split editor's minimap hidden")
        self.browser.click("#settingsToggle")
        self.browser.click('#tabs [data-path="/code.py"]')
        self.assertEqual(self.state()["label"], "Minimap")
        self.browser.click("#tocToggle")
        self.browser.evaluate("(async () => (await import('/js/util.js')).flushStore())()")
        self.loaded("/#/code.py")
        self.assertFalse(self.state()["map"])
        self.assertEqual(self.state()["expanded"], "false")
        self.assertNoPageErrors()

    def test_dividers_resize_and_remember_widths_without_changing_edits(self):
        self.loaded("/#/note.md")
        tree = self.width("#sidebar")
        self.drag("#treeResize", 60)
        self.assertAlmostEqual(self.width("#sidebar"), tree + 60, delta=1)
        contents = self.width("#tocPane")
        self.drag("#contentsResize", -40)
        self.assertAlmostEqual(self.width("#tocPane"), contents + 40, delta=1)
        self.browser.click("#tocToggle")
        self.assertTrue(self.browser.evaluate("document.querySelector('#contentsResize').hidden"))
        self.browser.click("#tocToggle")
        self.assertAlmostEqual(self.width("#tocPane"), contents + 40, delta=1)

        self.browser.evaluate("location.hash = '#/code.py'")
        self.browser.wait_for("!document.querySelector('#editorHost').hidden")
        self.browser.evaluate("""(() => {
          const editor = monaco.editor.getEditors()[0];
          window.originalSource = editor.getValue();
          editor.getModel().setValue(originalSource + '\\n# unsaved');
          editor.setPosition({lineNumber:80,column:4});
        })()""")
        before = self.state()["mapWidth"]
        map_options = "(() => { const map = monaco.editor.getEditors()[0].getOption(monaco.editor.EditorOption.minimap); return [map.size,map.scale,map.maxColumn]; })()"
        initial_options = self.browser.evaluate(map_options)
        self.drag("#minimapResize", -40, cancel=True)
        self.assertEqual(self.browser.evaluate(map_options), initial_options)
        self.assertEqual(self.state()["mapWidth"], before)
        self.drag("#minimapResize", -50)
        resized = self.state()["mapWidth"]
        self.assertAlmostEqual(resized, before + 50, delta=2)
        self.assertTrue(self.browser.evaluate("monaco.editor.getEditors()[0].getValue().endsWith('# unsaved')"))
        self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getPosition()"), {"lineNumber": 80, "column": 4})
        self.assertNotIn("unsaved", (self.root / "code.py").read_text())

        self.browser._call("Emulation.setDeviceMetricsOverride", {"width": 600, "height": 800, "deviceScaleFactor": 1, "mobile": False})
        self.browser.wait_for("monaco.editor.getEditors()[0].getLayoutInfo().minimap.minimapWidth < " + str(resized))
        self.drag("#minimapResize", 30, cancel=True)
        self.drag("#treeResize", -50, cancel=True)
        self.browser._call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        self.browser.wait_for("Math.abs(monaco.editor.getEditors()[0].getLayoutInfo().minimap.minimapWidth - " + str(resized) + ") <= 2")
        self.assertAlmostEqual(self.width("#sidebar"), tree + 60, delta=1)
        self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue(originalSource)")
        self.browser.evaluate("(async () => (await import('/js/util.js')).flushStore())()")
        self.loaded("/#/code.py")
        self.assertAlmostEqual(self.width("#sidebar"), tree + 60, delta=1)
        self.assertAlmostEqual(self.state()["mapWidth"], resized, delta=2)
        self.browser.evaluate("location.hash = '#/note.md'")
        self.browser.wait_for("!document.querySelector('#previewHost').hidden")
        self.assertAlmostEqual(self.width("#tocPane"), contents + 40, delta=1)
        self.assertNoPageErrors()
