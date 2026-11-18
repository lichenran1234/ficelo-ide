"""File controls preserve manual refresh and protect edits in automatic mode."""

import json
from urllib.parse import quote

from test_browser import BrowserCase
from test_modules import ModuleTest


class ChangeIndicatorModuleTest(ModuleTest):
    checks = r'''
import { changedRanges, mapChanges, changeDirections, domSnapshot, domChangeRegions } from "/js/changes.js";
check("independent changes retain unchanged content between them", () =>
  eq(changedRanges(["a","b","c","d","e"], ["A","b","c","d","E"]), [
    {start:0,end:1,newStart:0,newEnd:1}, {start:4,end:5,newStart:4,newEnd:5}]));
check("insertions and deletions use positions in the loaded document", () =>
  eq([changedRanges(["a","b"], ["x","a","b","y"]), changedRanges(["a","b","c"], ["b"])],
    [[{start:0,end:0,newStart:0,newEnd:1},{start:2,end:2,newStart:3,newEnd:4}],
     [{start:0,end:1,newStart:0,newEnd:0},{start:2,end:3,newStart:1,newEnd:1}]]));
check("bounded comparison still includes the whole changed span", () =>
  eq(changedRanges(["same","a","b","end"], ["same","x","y","end"], 0),
    [{start:1,end:3,newStart:1,newEnd:3}]));
check("line edits reconstruct arbitrary repeated-line documents", () => {
  let seed = 13;
  const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed; };
  for (let trial = 0; trial < 200; trial++) {
    const before = Array.from({length:random()%20}, () => String(random()%5));
    const after = Array.from({length:random()%20}, () => String(random()%5));
    const changes = changedRanges(before,after), reconstructed = [];
    let at = 0;
    for (const change of changes) {
      reconstructed.push(...before.slice(at,change.start),...after.slice(change.newStart,change.newEnd));
      at = change.end;
    }
    reconstructed.push(...before.slice(at));
    if (eq(reconstructed,after)) return "reconstruction failed for " + JSON.stringify([before,after,changes]);
  }
});
check("local insertions move external changes without inventing new ones", () =>
  eq(mapChanges([{start:2,end:3,newStart:2,newEnd:3}], [{start:0,end:0,newStart:0,newEnd:5}]),
    [{start:7,end:8,newStart:2,newEnd:3}]));
check("off-screen changes choose their directions; visible changes choose one half", () => {
  const region = (start,end,weight=1) => ({start,end,weight});
  return eq([
    changeDirections([region(-20,-10)],100), changeDirections([region(110,120)],100),
    changeDirections([region(-20,-10),region(110,120)],100),
    changeDirections([region(10,20,3),region(70,80)],100),
    changeDirections([region(10,20),region(70,80,3)],100),
    changeDirections([region(10,20),region(70,80)],100), changeDirections([],100,false),
  ], [{top:true,bottom:false},{top:false,bottom:true},{top:true,bottom:true},
      {top:true,bottom:false},{top:false,bottom:true},{top:false,bottom:true},{top:false,bottom:false}]);
});
check("HTML snapshots preserve text and attributes without executing scripts", () => {
  const a = new DOMParser().parseFromString('<p style="height:20px">old value</p><script>window.ran=true</script>',"text/html");
  const b = new DOMParser().parseFromString('<p style="height:20px">new value</p><script>window.ran=true</script>',"text/html");
  const before=domSnapshot(a.documentElement,true), after=domSnapshot(b.documentElement,true);
  const changes=changedRanges(before.tokens,after.tokens);
  return eq([changes.length, before.tokens.slice(changes[0].start,changes[0].end), !!window.ran], [1,["text:old"],false]);
});
check("large rewrites bound layout queries and still identify both off-screen directions", () => {
  const host = document.createElement("div"), pre = document.createElement("pre");
  host.style.cssText = "position:fixed;top:0;left:0;width:500px;height:400px;overflow:auto";
  host.append(pre); document.body.append(host);
  const original = document.createRange;
  let queries = 0;
  document.createRange = function() { queries++; return original.call(this); };
  try {
    for (const layout of ["text","siblings","scrollport"]) {
      queries = 0; pre.replaceChildren(); host.replaceChildren();
      const content = layout === "scrollport" ? host : pre;
      if (content === pre) host.append(pre);
      for (let n=0; n<3000; n++) {
        const text = `row ${n} has several words\n`;
        if (layout !== "text") {
          const row=document.createElement("div"); row.textContent=text; content.append(row);
        } else content.append(document.createTextNode(text));
      }
      if (layout === "text") content.normalize();
      host.scrollTop = host.scrollHeight / 2;
      const snapshot = domSnapshot(content);
      const regions = domChangeRegions(snapshot.anchors,
        [{start:0,end:snapshot.tokens.length,newStart:0,newEnd:snapshot.tokens.length}]);
      const error = eq(changeDirections(regions,host.clientHeight),{top:true,bottom:true});
      if (error) return error;
      if (queries > (layout === "text" ? 10 : 1000)) return "too many layout queries: " + queries;
    }
  } finally { document.createRange = original; host.remove(); }
});
'''

    def test_change_indicator_modules(self):
        self.assertAllPass()


class FileActionsBrowserTest(BrowserCase):
    # Exercise the server's default read-write mode.
    writable = None
    controls = ("scrollFileTop", "reloadFile", "scrollFileBottom")
    labels = ("Go to top", "Refresh", "Go to bottom")
    editor = "monaco.editor.getEditors()[0]"

    def prepare(self):
        self.code = "".join(f"value_{n:03d} = {n}\n" for n in range(300))
        (self.root / "long.py").write_text(self.code)
        (self.root / "other.py").write_text("# Other file\n" + self.code)
        (self.root / "long.md").write_text(self.markdown("Before"))
        (self.root / "long.html").write_text(
            '<h1>A long page</h1><div style="height:4000px"></div><p>The end</p>'
        )

    @staticmethod
    def markdown(title):
        return "# " + title + "\n\n" + "\n\n".join(
            f"## Section {n}\n\nA paragraph to scroll past." for n in range(60)
        )

    def open_file(self, name):
        selector = '#tree .row[data-path="/' + name + '"]'
        self.browser.evaluate(f"document.querySelector({json.dumps(selector)}).click()")
        self.browser.wait_for(
            "document.querySelector('#tabs .tab[aria-selected=true]')?.dataset.path === "
            + json.dumps("/" + name)
        )

    def load_file(self, name):
        self.loaded("/?path=" + quote(str(self.root / name), safe="/"))

    def wait_code(self, text):
        self.browser.wait_for(
            self.editor + ".getModel()?.getValue() === " + json.dumps(text),
            what="the expected file text",
        )

    def setting(self, label, checked):
        if self.browser.evaluate("document.querySelector('#settings').hidden"):
            self.browser.click("#settingsToggle")
        self.browser.evaluate(f"""(() => {{
          const row = [...document.querySelectorAll('#settings label')].find(
            item => item.querySelector('.set-label')?.textContent === {json.dumps(label)});
          const box = row.querySelector('input[type=checkbox]');
          if (box.checked !== {json.dumps(checked)}) box.click();
        }})()""")

    def close_settings(self):
        if not self.browser.evaluate("document.querySelector('#settings').hidden"):
            self.browser.click("#settingsToggle")

    def wait_stale(self):
        self.browser.wait_for(
            "document.querySelector('#reloadFile').classList.contains('is-stale')",
            what="the refresh control to show the disk change",
        )

    def assert_not_stale(self):
        self.assertEqual(
            self.browser.evaluate("document.querySelectorAll('#fileActions .is-stale').length"), 0
        )

    def wait_edge(self, bottom, markdown=False):
        if markdown:
            condition = (
                "node.scrollHeight - node.clientHeight - node.scrollTop <= 2"
                if bottom else "node.scrollTop <= 1"
            )
            expression = f"(() => {{ const node = document.querySelector('#preview'); return {condition}; }})()"
        else:
            condition = (
                "node.getScrollHeight() - node.getLayoutInfo().height - node.getScrollTop() <= 2"
                if bottom else "node.getScrollTop() <= 1"
            )
            expression = f"(() => {{ const node = {self.editor}; return {condition}; }})()"
        self.browser.wait_for(expression, what="the reader to reach the requested edge")

    def watch_content_reads(self):
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'), read = api.read;
          window.lastContentRead = null;
          api.read = async (...args) => {
            const body = await read(...args); lastContentRead = body.text; return body;
          };
        })()""")

    def change_file(self, name, text):
        (self.root / name).write_text(text)
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("lastContentRead === " + json.dumps(text), what="the changed file to be compared")

    def wait_directions(self, top, bottom):
        self.browser.wait_for(f"""(() => {{
          const green = id => document.getElementById(id).classList.contains('is-stale');
          return green('reloadFile') && green('scrollFileTop') === {json.dumps(top)} && green('scrollFileBottom') === {json.dumps(bottom)};
        }})()""", what=f"change directions up={top}, down={bottom}")

    def scroll_code(self, line):
        self.browser.evaluate(f"{self.editor}.setScrollTop({self.editor}.getTopForLineNumber({line}))")
        self.browser.wait_for(f"Math.abs({self.editor}.getScrollTop() - {self.editor}.getTopForLineNumber({line})) <= 1")

    def test_code_changes_choose_direction_and_visible_half(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        self.scroll_code(130)
        self.watch_content_reads()
        visible = self.browser.evaluate(f"{self.editor}.getVisibleRanges().map(range => [range.startLineNumber, range.endLineNumber])")
        first, last = visible[0][0], visible[-1][1]
        upper, lower = first + 3, last - 4
        for indices, expected in (
            ([5], (True, False)), ([290], (False, True)), ([5, 290], (True, True)),
            ([upper, upper + 1, upper + 2, lower], (True, False)),
            ([upper, lower, lower + 1, lower + 2], (False, True)),
            ([upper, lower], (False, True)),
        ):
            with self.subTest(lines=indices):
                lines = self.code.splitlines(keepends=True)
                for line in indices:
                    lines[line - 1] = f"changed_{line} = True\n"
                self.change_file("long.py", "".join(lines))
                self.wait_directions(*expected)
                self.wait_code(self.code)
        for text, expected in (
            ("# Prepended\n" + self.code, (True, False)),
            (self.code + "# Appended\n", (False, True)),
            (self.code.split("\n", 1)[1], (True, False)),
            (self.code.rsplit("\n", 2)[0] + "\n", (False, True)),
        ):
            self.change_file("long.py", text)
            self.wait_directions(*expected)
            self.wait_code(self.code)
        self.assertNoPageErrors()

    def test_direction_changes_as_reader_scrolls_and_clears_on_reload(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        self.scroll_code(130)
        self.watch_content_reads()
        changed = self.code.replace("value_050 = 50", "value_050 = 500")
        self.change_file("long.py", changed)
        self.wait_directions(True, False)
        self.scroll_code(1)
        self.wait_directions(False, True)
        self.browser.click("#scrollFileBottom")
        self.wait_code(changed)
        self.wait_edge(True)
        self.assert_not_stale()
        self.assertNoPageErrors()

    def test_local_edits_move_external_change_positions_without_becoming_disk_changes(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        local = "".join(f"# Local line {n}\n" for n in range(300)) + self.code
        self.browser.evaluate(f"{self.editor}.setValue({json.dumps(local)})")
        self.scroll_code(400)
        self.watch_content_reads()
        changed = self.code.replace("value_250 = 250", "value_250 = 999")
        self.change_file("long.py", changed)
        self.wait_directions(False, True)
        self.browser.click("#scrollFileTop")
        self.waitFor("dialog[open]")
        self.browser.click('dialog[open] button[type="button"]')
        self.wait_code(local)
        self.wait_directions(False, True)
        self.assertTrue(self.browser.evaluate("!!document.querySelector('#tabs .tab.is-dirty')"))
        self.assertNoPageErrors()

    def test_markdown_changes_use_rendered_positions(self):
        original = "\n\n".join(f"## Heading {n}\n\nParagraph {n}." for n in range(100))
        (self.root / "regions.md").write_text(original)
        self.load_file("regions.md")
        self.waitFor("#preview h2", count=100)
        self.browser.evaluate("""(() => {
          const host = document.querySelector('#preview'), heading = host.querySelectorAll('h2')[40];
          host.scrollTop += heading.getBoundingClientRect().top - host.getBoundingClientRect().top;
        })()""")
        self.watch_content_reads()
        for top, bottom in ((True, False), (False, True), (True, True)):
            changed = original
            if top:
                changed = changed.replace("Paragraph 2.", "Changed paragraph 2.")
            if bottom:
                changed = changed.replace("Paragraph 95.", "Changed paragraph 95.")
            self.change_file("regions.md", changed)
            self.wait_directions(top, bottom)
            self.assertNotIn("Changed paragraph", self.browser.text("#preview"))
        self.assertNoPageErrors()

    def test_minified_html_changes_use_actual_layout_and_follow_scrolling(self):
        original = ('<style>body{margin:0}p{height:40px;margin:0}#row-5{position:absolute;top:5200px}</style>'
                    + "".join(f'<p id="row-{n}">Value_{n}</p>' for n in range(150))
                    + '<script>addEventListener("message", event => { if (Number.isInteger(event.data?.seek)) '
                      'document.getElementById("row-" + event.data.seek).scrollIntoView(); });</script>')
        (self.root / "regions.html").write_text(original)
        self.load_file("regions.html")
        self.waitFor("iframe.html-preview")
        self.browser.click("#scrollFileBottom")
        self.browser.wait_for("(async () => (await import('/js/settings.js')).readScroll('/regions.html')?.frac > 0.99)()")
        self.browser.evaluate("document.querySelector('iframe').contentWindow.postMessage({seek:50}, '*')")
        self.browser.wait_for("(async () => { const p=(await import('/js/settings.js')).readScroll('/regions.html'); return p.frac > 0.2 && p.frac < 0.6; })()")
        self.watch_content_reads()
        # The fifth source paragraph is positioned below the reader by CSS.
        self.change_file("regions.html", original.replace("Value_5<", "Changed_5<"))
        self.wait_directions(False, True)
        self.change_file("regions.html", original.replace("Value_2<", "Changed_2<"))
        self.wait_directions(True, False)
        self.change_file("regions.html", original.replace("Value_2<", "Changed_2<").replace("Value_140<", "Changed_140<"))
        self.wait_directions(True, True)
        height = self.browser.evaluate("document.querySelector('iframe').clientHeight")
        upper, lower = 52, 50 + int(height / 40) - 3
        for indices, expected in (([upper, upper + 1, upper + 2, lower], (True, False)),
                                  ([upper, lower, lower + 1, lower + 2], (False, True))):
            changed = original
            for index in indices:
                changed = changed.replace(f"Value_{index}<", f"Changed_{index}<")
            self.change_file("regions.html", changed)
            self.wait_directions(*expected)
        self.change_file("regions.html", original.replace("Value_75<", "Changed_75<"))
        self.wait_directions(False, True)
        self.browser.evaluate("document.querySelector('iframe').contentWindow.postMessage({seek:100}, '*')")
        self.wait_directions(True, False)
        self.assertNoPageErrors()

    def test_data_and_log_changes_use_the_visible_rows(self):
        for name, original in (
            ("regions.json", json.dumps([f"value_{n:03d}" for n in range(200)])),
            ("regions.csv", "name\n" + "\n".join(f"value_{n:03d}" for n in range(200))),
            ("regions.log", "\n".join(f"value_{n:03d}" for n in range(200))),
        ):
            with self.subTest(file=name):
                (self.root / name).write_text(original)
                self.load_file(name)
                self.waitFor("#viewerHost .view-scroll")
                self.browser.evaluate("""(() => {
                  const node = document.querySelector('#viewerHost .view-scroll');
                  node.scrollTop = (node.scrollHeight - node.clientHeight) / 2;
                })()""")
                self.watch_content_reads()
                self.change_file(name, original.replace("value_002", "CHANGED_002"))
                self.wait_directions(True, False)
                self.change_file(name, original.replace("value_195", "CHANGED_195"))
                self.wait_directions(False, True)
                self.change_file(name, original.replace("value_002", "CHANGED_002").replace("value_195", "CHANGED_195"))
                self.wait_directions(True, True)
                if name.endswith(".json"):
                    # Formatting changes still need one arrow even when the
                    # parsed, rendered data has no changed tokens to locate.
                    self.change_file(name, json.dumps(json.loads(original), indent=2))
                    self.wait_directions(False, True)
        self.assertNoPageErrors()

    def test_older_comparison_cannot_replace_newer_change_directions(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        self.scroll_code(130)
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'), read = api.read;
          window.heldComparison = false; window.lastContentRead = null;
          const gate = new Promise(resolve => { window.releaseComparison = resolve; });
          api.read = async (...args) => {
            const body = await read(...args);
            if (!heldComparison) { heldComparison = true; await gate; }
            lastContentRead = body.text;
            return body;
          };
        })()""")
        (self.root / "long.py").write_text(self.code.replace("value_005 = 5", "value_005 = 55"))
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("heldComparison")
        lower = self.code.replace("value_290 = 290", "value_290 = 999")
        self.change_file("long.py", lower)
        self.wait_directions(False, True)
        self.browser.evaluate("releaseComparison()")
        self.browser.wait_for("lastContentRead !== " + json.dumps(lower))
        self.wait_directions(False, True)
        self.wait_code(self.code)
        self.assertNoPageErrors()

    def test_controls_scroll_code_and_markdown_in_vertical_order(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        self.browser.wait_for(
            self.editor + ".getScrollHeight() > 2000 && " + self.editor + ".getLayoutInfo().height > 0",
            what="the code editor to finish layout",
        )
        self.assertTrue(self.server.config.writable)
        self.assertEqual(self.browser.evaluate("""(() => {
          const buttons = [...document.querySelectorAll('#fileActions button')];
          const boxes = buttons.map(button => button.getBoundingClientRect());
          return [buttons.map(button => button.id), buttons.every(button => !button.hidden),
            boxes.every(box => box.width > 0 && box.height > 0),
            boxes[0].bottom <= boxes[1].top && boxes[1].bottom <= boxes[2].top,
            Math.max(...boxes.map(box => box.left)) - Math.min(...boxes.map(box => box.left)) < 2];
        })()"""), [list(self.controls), True, True, True, True])
        for markdown in (False, True):
            with self.subTest(markdown=markdown):
                if markdown:
                    self.open_file("long.md")
                    self.waitFor("#preview h1")
                    self.browser.wait_for("document.querySelector('#preview').scrollHeight > 2000")
                self.browser.click("#scrollFileBottom")
                self.wait_edge(True, markdown)
                self.browser.click("#scrollFileTop")
                self.wait_edge(False, markdown)
                self.assert_not_stale()
        self.open_file("long.html")
        self.waitFor("iframe.html-preview")
        for button, condition in (("scrollFileBottom", ">= 0.999"), ("scrollFileTop", "=== 0")):
            with self.subTest(html_button=button):
                self.browser.click("#" + button)
                self.browser.wait_for(
                    "(async () => (await import('/js/settings.js')).readScroll('/long.html')?.frac "
                    + condition + ")()",
                    what="the sandboxed HTML preview to report its scroll position",
                )
        self.assertNoPageErrors()

    def test_manual_changes_reload_before_scrolling_to_either_edge(self):
        self.load_file("long.md")
        self.waitFor("#preview h1")
        for button, title, bottom in (
            ("scrollFileBottom", "Updated bottom", True),
            ("scrollFileTop", "Updated top", False),
        ):
            with self.subTest(button=button):
                previous = self.browser.text("#preview h1")
                (self.root / "long.md").write_text(self.markdown(title))
                self.wait_stale()
                self.assertEqual(self.browser.text("#preview h1"), previous)
                self.browser.click("#" + button)
                self.browser.wait_for(
                    "document.querySelector('#preview h1')?.textContent === " + json.dumps(title)
                )
                self.wait_edge(bottom, markdown=True)
                self.assert_not_stale()
        self.assertNoPageErrors()

    def test_cancelled_dirty_reload_preserves_buffer_and_scroll(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        local = "# Unsaved changes\n" + self.code
        self.browser.evaluate(self.editor + ".getModel().setValue(" + json.dumps(local) + ")")
        self.browser.evaluate(self.editor + ".setScrollTop(500)")
        self.browser.wait_for(self.editor + ".getScrollTop() >= 499")
        (self.root / "long.py").write_text("# Changed on disk\n" + self.code)
        self.wait_stale()
        before = self.browser.evaluate(self.editor + ".getScrollTop()")
        for button in self.controls:
            with self.subTest(button=button):
                self.browser.click("#" + button)
                self.waitFor("dialog[open]")
                self.browser.click('dialog[open] button[type="button"]')
                self.browser.wait_for("!document.querySelector('dialog[open]')")
                self.wait_code(local)
                self.assertAlmostEqual(self.browser.evaluate(self.editor + ".getScrollTop()"), before, delta=1)
                self.assertTrue(self.browser.evaluate("!!document.querySelector('#tabs .tab.is-dirty')"))
                self.wait_stale()
        self.assertNoPageErrors()

    def test_hidden_controls_and_refresh_preference_persist_and_reset(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        for button in self.controls:
            self.browser.evaluate(f"""document.getElementById({json.dumps(button)}).dispatchEvent(
              new MouseEvent('contextmenu', {{bubbles:true, cancelable:true, button:2,
                clientX:100, clientY:140}}))""")
            self.assertEqual(self.browser.evaluate(
                "[...document.querySelectorAll('.menu-item')].map(item => item.textContent)"
            ), ["Hide button"])
            self.browser.click(".menu-item")
            self.assertTrue(self.browser.evaluate(f"document.getElementById({json.dumps(button)}).hidden"))
        self.setting("Manual refresh", False)
        self.browser.evaluate("(async () => (await import('/js/util.js')).flushStore())()")
        route = self.browser.evaluate("location.pathname + location.search + location.hash")
        self.loaded(route)
        self.assertEqual(self.browser.evaluate(
            "[...document.querySelectorAll('#fileActions button')].map(button => button.hidden)"
        ), [True, True, True])
        self.browser.click("#settingsToggle")
        self.assertEqual(self.browser.evaluate("""[...document.querySelectorAll(
          '#settings section[aria-label="File controls"] input[type=checkbox]')].map(box => box.checked)"""),
          [False, False, False, False])
        for button, label in zip(self.controls, self.labels):
            self.setting(label, True)
            self.assertFalse(self.browser.evaluate(f"document.getElementById({json.dumps(button)}).hidden"))
        self.setting("Go to top", False)
        self.browser.click("#settings .set-reset")
        self.assertEqual(self.browser.evaluate("""[...document.querySelectorAll(
          '#settings section[aria-label="File controls"] input[type=checkbox]')].map(box => box.checked)"""),
          [True, True, True, True])
        self.assertEqual(self.browser.evaluate(
            "[...document.querySelectorAll('#fileActions button')].map(button => button.hidden)"
        ), [False, False, False])
        self.assertNoPageErrors()

    def test_button_transparency_updates_live_persists_and_resets(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        self.browser.click("#settingsToggle")
        slider = '#settings section[aria-label="File controls"] input[name="fileControlTransparency"]'
        self.waitFor(slider)

        def assert_transparency(value):
            opacity = (100 - value) / 100
            self.browser.wait_for(f"""[...document.querySelectorAll('#fileActions button')].every(
              button => Math.abs(Number(getComputedStyle(button).opacity) - {opacity}) < 0.001)""")

        def key(name, code):
            for kind in ("keyDown", "keyUp"):
                self.browser._call("Input.dispatchKeyEvent", {"type": kind, "key": name, "code": name, "windowsVirtualKeyCode": code})

        def assert_slider(value):
            self.assertEqual(self.browser.evaluate(f"""(() => {{const slider = document.querySelector({json.dumps(slider)});
              return [slider.value, slider.closest('label').querySelector('.set-value').textContent];}})()"""),
              [str(value), str(value) + "%"])

        assert_slider(40)
        assert_transparency(40)
        self.browser.evaluate(f"document.querySelector({json.dumps(slider)}).focus()")
        for name, code, value in (("ArrowRight", 39, 45), ("Home", 36, 0), ("End", 35, 100)):
            key(name, code)
            assert_slider(value)
            assert_transparency(value)
        self.browser.evaluate(f"""(() => {{const slider = document.querySelector({json.dumps(slider)});
          slider.value = '75'; slider.dispatchEvent(new Event('input', {{bubbles:true}}));}})()""")
        assert_slider(75)
        assert_transparency(75)
        self.close_settings()
        self.scroll_code(130)
        self.watch_content_reads()
        self.change_file("long.py", "# Changed above\n" + self.code + "# Changed below\n")
        self.wait_directions(True, True)

        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                if self.browser.evaluate("document.documentElement.dataset.theme") != theme:
                    self.toggle_theme()
                assert_transparency(75)
                for button in self.controls:
                    box = self.browser.evaluate(f"document.getElementById({json.dumps(button)}).getBoundingClientRect().toJSON()")
                    self.browser._call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2})
                    self.browser.wait_for(f"Number(getComputedStyle(document.getElementById({json.dumps(button)})).opacity) === 1")
                self.browser._call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1, "y": 1})
                key("Tab", 9)
                for button in self.controls:
                    self.browser.evaluate(f"document.getElementById({json.dumps(button)}).focus({{preventScroll:true}})")
                    self.browser.wait_for("document.activeElement.matches(':focus-visible') && Number(getComputedStyle(document.activeElement).opacity) === 1")
                self.browser.evaluate("document.activeElement.blur()")
                assert_transparency(75)

        self.browser.evaluate("(async () => (await import('/js/util.js')).flushStore())()")
        self.assertEqual(self.browser.evaluate("(async () => (await (await fetch('/api/state')).json()).settings['ficelo.appearance'].fileControlTransparency)()"), 75)
        route = self.browser.evaluate("location.pathname + location.search + location.hash")
        self.loaded(route)
        self.browser.click("#settingsToggle")
        assert_slider(75)
        assert_transparency(75)
        self.browser.click("#settings .set-reset")
        assert_slider(40)
        assert_transparency(40)
        self.assertNoPageErrors()

    def test_automatic_refresh_updates_clean_tabs_and_preserves_edits(self):
        self.load_file("long.py")
        self.wait_code(self.code)
        self.open_file("other.py")
        self.wait_code("# Other file\n" + self.code)
        self.browser.click('#tabs .tab[data-path="/long.py"]')
        self.wait_code(self.code)
        self.setting("Manual refresh", False)
        self.close_settings()
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js');
          const read = api.read, watch = api.watch;
          window.fileReads = []; window.watchReplies = 0; window.greenEvents = 0;
          api.read = async (...args) => {
            const body = await read(...args); fileReads.push(args[0]); return body;
          };
          api.watch = async (...args) => {
            const body = await watch(...args); watchReplies++; return body;
          };
          new MutationObserver(records => {
            greenEvents += records.filter(record => record.oldValue?.includes('is-stale') ||
              record.target.classList.contains('is-stale')).length;
          }).observe(document.querySelector('#fileActions'), {
            subtree:true, attributes:true, attributeFilter:['class'], attributeOldValue:true,
          });
        })()""")
        active = "# Automatic active update\n" + self.code
        inactive = "# Automatic inactive update\n" + self.code
        (self.root / "long.py").write_text(active)
        (self.root / "other.py").write_text(inactive)
        self.wait_code(active)
        self.browser.wait_for("fileReads.includes('/other.py')", what="the inactive tab to refresh")
        self.assertEqual(self.browser.evaluate(
            "document.querySelector('#tabs .tab[aria-selected=true]').dataset.path"
        ), "/long.py")
        self.browser.click('#tabs .tab[data-path="/other.py"]')
        self.wait_code(inactive)
        dirty = "# Keep these unsaved edits\n" + self.code
        self.browser.evaluate(self.editor + ".getModel().setValue(" + json.dumps(dirty) + ")")
        before = self.browser.evaluate("watchReplies")
        (self.root / "other.py").write_text("# Newer disk version\n" + self.code)
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for(f"watchReplies >= {before + 2}", what="the edited tab's disk change to be checked")
        self.wait_code(dirty)
        self.assertTrue(self.browser.evaluate("!!document.querySelector('#tabs .tab.is-dirty')"))
        self.assertFalse(self.browser.evaluate("!!document.querySelector('dialog[open]')"))

        # Hold a real file response, then type before the refresh can consume it.
        self.browser.click('#tabs .tab[data-path="/long.py"]')
        self.wait_code(active)
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js'), read = api.read;
          let hold = true; window.readHeld = false; window.readReturned = false;
          const gate = new Promise(resolve => { window.releaseRead = resolve; });
          api.read = async (...args) => {
            const body = await read(...args);
            if (args[0] === '/long.py' && hold) {
              hold = false; readHeld = true; await gate; readReturned = true;
            }
            return body;
          };
        })()""")
        (self.root / "long.py").write_text("# Pending automatic read\n" + self.code)
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("readHeld", what="the automatic file read to be pending")
        during_read = "# Typed while the read was pending\n" + self.code
        self.browser.evaluate(self.editor + ".getModel().setValue(" + json.dumps(during_read) + ")")
        self.browser.evaluate("releaseRead()")
        self.browser.wait_for("readReturned")
        self.browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        self.wait_code(during_read)
        self.assertTrue(self.browser.evaluate("!!document.querySelector('#tabs .tab[aria-selected=true].is-dirty')"))
        self.assertEqual(self.browser.evaluate("greenEvents"), 0)
        self.assert_not_stale()
        self.assertNoPageErrors()
