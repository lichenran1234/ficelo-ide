"""Reader regressions batched by browser to keep iteration fast."""

import json

from cdp import Browser
from test_browser import BrowserCase
from test_modules import ModuleTest


class ReaderModuleTest(ModuleTest):
    checks = r'''
import { renderMarkdown } from "/js/markdown.js";
import { decorateShell } from "/js/highlighter.js";
import { renderViewer } from "/js/viewers.js";

check("nested and HTML headings agree with the rendered document", () => {
  const source = '# Notes\n\n> ## Quoted *heading*\n\n- item\n\n  ### Nested\n\n<h2>Notes-1</h2>\n\n## Notes\n';
  const host = document.createElement("div"); renderMarkdown(source, window.marked, host);
  const headings = outline(source, window.marked);
  return eq([headings.map(h => h.text), headings.map(h => h.slug),
    [...host.querySelectorAll("h1,h2,h3")].map(h => h.id)],
    [["Notes", "Quoted heading", "Nested", "Notes-1", "Notes"],
     ["notes", "quoted-heading", "nested", "notes-1", "notes-2"],
     ["notes", "quoted-heading", "nested", "notes-1", "notes-2"]]);
});
check("ragged CSV rows keep their surplus fields", () => {
  const host = document.createElement("div");
  renderViewer("csv", {path:"/data.csv",text:"a,b\n1,2,3\n4\n"}, host, window.marked);
  return eq([...host.querySelectorAll("tr")].map(row => [...row.children].map(c => c.textContent)),
    [["","a","b",""],["1","1","2","3"],["2","4","",""]]);
});
check("heading anchors follow rendered text when contents are available", () => {
  const host = document.createElement("div"), source = '# Chapter target\n\n<h2 id="chapter-target">Heading</h2>\n\n[Jump](#chapter-target)';
  renderMarkdown(source,window.marked,host);
  return eq([host.querySelector("h1").id,host.querySelector("h2").id,host.querySelector("a").getAttribute("href"),
    outline(source,window.marked).map(h=>h.slug)], ["chapter-target","heading","#chapter-target",["chapter-target","heading"]]);
});
check("wide CSV rendering has a bounded cell budget and a download fallback", () => {
  const host = document.createElement("div");
  renderViewer("csv",{path:"/wide.csv",text:"header\n"+"x,".repeat(1000)+"\n"+"one\n".repeat(100)},host);
  return host.querySelectorAll("td,th").length <= 20000 && host.textContent.includes("columns") &&
    host.querySelector("a[download]") ? null : "unbounded table or missing truncation notice";
});
check("SVG is an image using the sandboxed resource route", () => {
  const host = document.createElement("div");
  renderViewer(viewerFor("/a #.svg"), {path:"/a #.svg",name:"a #.svg"}, host);
  return eq([host.querySelector("img").getAttribute("src"),!!host.querySelector("a[download]")],
    ["/preview/a%20%23.svg",true]);
});
check("media errors offer a download and preserve native seeking", () => {
  const host = document.createElement("div");
  renderViewer(viewerFor("/movie.mkv"), {path:"/movie.mkv"}, host);
  const player = host.querySelector("video"); player.dispatchEvent(new Event("error"));
  return player.controls && player.preload === "metadata" && host.textContent.includes("another player") &&
    host.querySelector("a[download]") ? null : "missing native controls or error fallback";
});
check("native resources reuse their nodes until the file changes", () => {
  const host = document.createElement("div"), tab = {path:"/image.png",stamp:1};
  renderViewer("image",tab,host); const first = host.querySelector("img");
  host.replaceChildren(); renderViewer("image",tab,host);
  const reused = first === host.querySelector("img");
  tab.stamp = 2; renderViewer("image",tab,host);
  return reused && first !== host.querySelector("img") && !first.hasAttribute("src") ? null : "resource lifecycle lost";
});
check("closed tab identities survive reconstruction and remain workspace scoped", () => {
  const mount = document.createElement("div");
  const make = () => new Tabs(mount,{onSelect(){},onClose(){}});
  const first = make(); first.setWorkspace("reader-history-a");
  first.add("commit:r", {path:"/repo",kind:"commit",wholeCommit:true,rev:"abcdef",repo:"/repo",base:"main",text:"secret buffer"});
  first.close("commit:r");
  const second = make(); second.setWorkspace("reader-history-a"); const recovered = second.takeLastClosed();
  second.setWorkspace("reader-history-b");
  return eq([recovered.path,recovered.wholeCommit,recovered.rev,recovered.repo,recovered.at,
    recovered.text,second.takeLastClosed()], ["/repo",true,"abcdef","/repo",0,undefined,null]);
});
check("terminal processes never enter closed history", () => {
  const tabs = new Tabs(document.createElement("div"),{onSelect(){},onClose(){}});
  tabs.setWorkspace("reader-history-terminal");
  for (let i=0;i<25;i++) { tabs.add("/"+i); tabs.close("/"+i); }
  tabs.add("terminal:x",{kind:"terminal"}); tabs.close("terminal:x");
  return eq([tabs.recentlyClosed.length,tabs.takeLastClosed().path],[20,"/24"]);
});
check("shell decoration preserves grammar spans, assignments and continuations", () => {
  const source = 'FLAG=<span class="hljs-string">"a b"</span> uv run --flag \\\n  --other <span class="hljs-string">"quoted | name"</span>\ncat file | custom-tool --flag\n';
  const host = document.createElement("div"); host.innerHTML = decorateShell(source);
  return eq([[...host.querySelectorAll(".hljs-built_in")].map(node=>node.textContent),
    [...host.querySelectorAll(".hljs-attr")].map(node=>node.textContent),
    [...host.querySelectorAll(".hljs-string")].map(node=>node.textContent)],
    [["uv","cat","custom-tool"],["--flag","--other","--flag"],['"a b"','"quoted | name"']]);
});
check("shell decoration leaves highlighted comments and strings intact", () => {
  const source = '<span class="hljs-comment"># ignored --flag</span>\necho <span class="hljs-string">"$(nested)"</span>';
  const host = document.createElement("div"); host.innerHTML = decorateShell(source);
  return eq([host.querySelector(".hljs-comment").innerHTML, host.querySelector(".hljs-string").innerHTML,
    [...host.querySelectorAll(".hljs-built_in")].map(node=>node.textContent)], ['# ignored --flag','"$(nested)"',["echo"]]);
});
'''

    def test_reader_modules(self):
        self.assertAllPass()


class FileOpenScrollTest(BrowserCase):
    editor = "monaco.editor.getEditors()[0]"
    preview = "document.querySelector('#preview')"
    viewer = "document.querySelector('#viewerHost .view-scroll')"

    def prepare(self):
        markdown = "# Start\n\n" + "A paragraph.\n\n" * 100
        fixtures = {
            "md": markdown,
            "txt": "A line of text.\n" * 300,
            "py": "value = 1\n" * 300,
            "csv": "Name,Value\n" + "entry,1\n" * 300,
            "tsv": "Name\tValue\n" + "entry\t1\n" * 300,
            "ipynb": json.dumps({"cells": [{"cell_type": "markdown", "source": markdown}]}),
            "json": json.dumps(list(range(300))),
            "jsonl": '{"value": 1}\n' * 300,
            "log": "A log entry.\n" * 300,
            "patch": "@@ -1 +1 @@\n-old\n+new\n" * 100,
            "html": "<!doctype html><h1>Start</h1>" + "<p>A paragraph.</p>" * 300,
        }
        for suffix, text in fixtures.items():
            for name in ("first", "second"):
                (self.root / f"{name}.{suffix}").write_text(text)

    def settle(self):
        self.browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

    def open_file(self, name):
        selector = '#tree .row[data-path="/' + name + '"]'
        self.browser.evaluate("document.querySelector(" + json.dumps(selector) + ").click()")
        self.browser.wait_for(
            "document.querySelector('#tabs [aria-selected=true]')?.dataset.path === "
            + json.dumps("/" + name)
        )
        self.settle()

    def test_new_files_start_at_top_after_scrolling_another_file(self):
        self.loaded()
        for suffix in ("md", "txt", "py", "csv", "tsv", "ipynb", "json", "jsonl", "log", "patch"):
            with self.subTest(suffix=suffix):
                self.open_file("first." + suffix)
                editor = suffix in ("txt", "py")
                node = self.preview if suffix == "md" else self.viewer
                position = self.editor + ".getScrollTop()" if editor else node + ".scrollTop"
                scroll = self.editor + ".setScrollTop(240)" if editor else node + ".scrollTop = 240"
                self.browser.evaluate(scroll)
                self.browser.wait_for(position + " >= 240")
                self.open_file("second." + suffix)
                self.assertEqual(self.browser.evaluate(position), 0, "new file inherited a scroll offset")
                # Returning to a file with a saved position still resumes reading.
                self.open_file("first." + suffix)
                self.assertGreater(self.browser.evaluate(position), 0)
                self.open_file("second." + suffix)
                self.assertEqual(self.browser.evaluate(position), 0, "top-of-file position was not retained")
        self.assertNoPageErrors()

    def test_new_split_and_source_views_start_at_top(self):
        self.loaded()
        self.browser.evaluate("""(async () => {
          const {store} = await import('/js/util.js');
          store.set('ficelo.mdview', {'/first.md': 'split', '/second.md': 'split'});
        })()""")
        self.open_file("first.md")
        self.browser.evaluate(self.editor + ".setScrollTop(240)")
        self.browser.wait_for(self.preview + ".scrollTop > 0")
        self.open_file("second.md")
        self.assertEqual(self.browser.evaluate(self.editor + ".getScrollTop()"), 0)
        self.assertEqual(self.browser.evaluate(self.preview + ".scrollTop"), 0)
        self.browser.click('#viewToggle [data-view="edit"]')
        self.settle()
        self.assertEqual(self.browser.evaluate(self.editor + ".getScrollTop()"), 0)
        self.assertNoPageErrors()

    def test_open_tabs_keep_their_position_on_every_frame(self):
        self.loaded()
        for suffix in ("md", "txt", "py", "csv", "tsv", "ipynb", "json", "jsonl", "log", "patch"):
            with self.subTest(suffix=suffix):
                self.open_file("first." + suffix)
                editor = suffix in ("txt", "py")
                node = self.preview if suffix == "md" else self.viewer
                position = self.editor + ".getScrollTop()" if editor else node + ".scrollTop"
                identity = self.editor + ".getModel()" if editor else node + ".firstElementChild"
                self.browser.evaluate(self.editor + ".setScrollTop(240)" if editor else node + ".scrollTop = 240")
                self.settle()
                self.browser.evaluate("window.retainedContent = " + identity + "; void 0")
                self.open_file("second." + suffix)
                self.browser.evaluate(self.editor + ".setScrollTop(480)" if editor else node + ".scrollTop = 480")
                self.settle()
                samples = self.browser.evaluate("""(async () => {
                  document.querySelector('#tabs [data-path="/first.""" + suffix + """"]').click();
                  const positions = [""" + position + """];
                  for (let i = 0; i < 8; i++) {
                    await new Promise(requestAnimationFrame);
                    positions.push(""" + position + """);
                  }
                  return positions;
                })()""")
                self.assertEqual(samples, [240] * 9, "returning tab visibly moved")
                self.assertTrue(self.browser.evaluate("retainedContent === " + identity))
        self.assertNoPageErrors()

    def test_split_tabs_keep_both_positions_on_every_frame(self):
        self.loaded()
        self.browser.evaluate("""(async () => {
          const {store} = await import('/js/util.js');
          store.set('ficelo.mdview', {'/first.md': 'split', '/second.md': 'split'});
        })()""")
        self.open_file("first.md")
        self.browser.evaluate(self.editor + ".setScrollTop(240)")
        self.browser.wait_for(self.preview + ".scrollTop > 0")
        self.settle()
        position = "[" + self.editor + ".getScrollTop(), " + self.preview + ".scrollTop]"
        wanted = self.browser.evaluate(position)
        self.open_file("second.md")
        self.browser.evaluate(self.editor + ".setScrollTop(600)")
        self.settle()
        samples = self.browser.evaluate("""(async () => {
          document.querySelector('#tabs [data-path="/first.md"]').click();
          const positions = [""" + position + """];
          for (let i = 0; i < 8; i++) {
            await new Promise(requestAnimationFrame);
            positions.push(""" + position + """);
          }
          return positions;
        })()""")
        self.assertEqual(samples, [wanted] * 9)
        self.assertNoPageErrors()

    def test_html_tab_keeps_its_live_document_and_scroll(self):
        path = self.root / "first.html"
        path.write_text('<style>html{scroll-behavior:smooth}</style><input id="draft">' + path.read_text())
        self.browser.close()
        self.browser = Browser(extra_args=["--disable-site-isolation-trials"])
        self.addCleanup(self.browser.close)
        self.loaded("/#/first.html")
        self.waitFor("#viewerHost iframe")
        # Inspect the sandbox through DevTools, without weakening its origin policy.
        frames = self.browser._call("Page.getFrameTree")["frameTree"]["childFrames"]
        context = self.browser._call("Page.createIsolatedWorld", {
            "frameId": frames[0]["frame"]["id"], "worldName": "reader-test",
        })["executionContextId"]

        def inside(expression):
            result = self.browser._call("Runtime.evaluate", {
                "contextId": context, "expression": expression, "returnByValue": True,
            })
            self.assertNotIn("exceptionDetails", result)
            return result["result"].get("value")

        inside("document.querySelector('#draft').value = 'keep this'; window.scrollTo({top: 2400, behavior: 'instant'})")
        self.assertEqual(inside("scrollY"), 2400)
        self.browser.evaluate("window.retainedFrame = document.querySelector('#viewerHost iframe')")
        self.open_file("second.html")
        self.assertTrue(self.browser.evaluate("retainedFrame.isConnected && retainedFrame.parentElement.inert"))
        self.assertEqual(inside("scrollY"), 2400, "inactive frame lost its layout")
        inside("parent.postMessage({ficelo: 'navigate', href: 'second.txt'}, '*')")
        self.settle()
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs [aria-selected=true]').dataset.path"), "/second.html")
        self.browser.evaluate("document.querySelector('#tabs [data-path=\"/first.html\"]').click()")
        self.assertEqual(inside("[scrollY, document.querySelector('#draft').value]"), [2400, "keep this"])
        self.assertTrue(self.browser.evaluate("retainedFrame === document.querySelector('#viewerHost iframe')"))
        self.settle()
        self.assertEqual(inside("scrollY"), 2400)
        self.browser.click("#reloadFile")
        self.browser.wait_for("!retainedFrame.isConnected && document.querySelector('#viewerHost iframe') !== retainedFrame")
        self.assertNoPageErrors()

    def test_native_viewers_stay_mounted_until_their_tab_is_closed(self):
        fixtures = {
            "image.svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>',
            "document.pdf": b"%PDF-1.4\n%%EOF\n",
            "audio.wav": b"RIFF",
            "video.webm": b"\x1a\x45\xdf\xa3",
        }
        for name, content in fixtures.items():
            (self.root / name).write_bytes(content)
        self.loaded()
        for name in fixtures:
            with self.subTest(name=name):
                self.open_file(name)
                self.browser.evaluate("""window.nativeView = document.querySelector('#viewerHost');
                  window.nativeNode = nativeView.querySelector('img,embed,audio,video');
                  window.removedNativeView = false;
                  window.nativeObserver = new MutationObserver(records => {
                    if (records.some(r => [...r.removedNodes].some(node => node.contains(nativeNode)))) removedNativeView = true;
                  });
                  nativeObserver.observe(document.querySelector('.stage'), {childList:true, subtree:true});
                """)
                self.open_file("second.txt")
                self.assertTrue(self.browser.evaluate("""nativeNode.isConnected && nativeView.inert &&
                  getComputedStyle(nativeView).visibility === 'hidden' && nativeView.clientHeight > 0"""))
                self.open_file(name)
                self.assertTrue(self.browser.evaluate("""!removedNativeView && !nativeView.inert &&
                  nativeNode === document.querySelector('#viewerHost img,#viewerHost embed,#viewerHost audio,#viewerHost video')"""))
                self.browser.evaluate("nativeObserver.disconnect()")
                self.browser.click('#tabs [aria-selected=true] .close')
                self.browser.wait_for("!nativeNode.isConnected")
        self.assertNoPageErrors()

    def test_markdown_keeps_rendered_diagrams_and_nested_scroll(self):
        path = self.root / "first.md"
        path.write_text("```mermaid\ngraph TD\nA --> B\n```\n\n```python\nvalue = '" + "wide " * 300 + "'\n```\n\n" + path.read_text())
        self.loaded("/#/first.md")
        self.waitFor("#preview .mermaid-figure svg")
        self.waitAllColoured()
        self.browser.evaluate("""window.retainedDiagram = document.querySelector('#preview .mermaid-figure svg');
          window.codeScroll = document.querySelector('#preview pre > code');
          codeScroll.scrollLeft = 240;
          document.querySelector('#preview').scrollTop = 300;
        """)
        self.settle()
        self.assertEqual(self.browser.evaluate("codeScroll.scrollLeft"), 240)
        self.browser.evaluate("""(async () => {
          const {store} = await import('/js/util.js');
          store.set('ficelo.filebackgrounds', """ + json.dumps({str(self.root / "second.md"): "#101010"}) + """);
        })()""")
        self.open_file("second.md")
        self.browser.evaluate("document.querySelector('#tabs [data-path=\"/first.md\"]').click()")
        self.assertEqual(self.browser.evaluate("""[document.querySelector('#preview').scrollTop, codeScroll.scrollLeft,
          retainedDiagram === document.querySelector('#preview .mermaid-figure svg')]"""), [300, 240, True])
        self.settle()
        self.assertEqual(self.browser.evaluate("document.querySelector('#preview').scrollTop"), 300)
        self.toggle_theme()
        self.browser.wait_for("document.querySelector('#preview .mermaid-figure svg') !== retainedDiagram")
        self.assertNoPageErrors()

    def test_adding_workspace_tabs_preserves_current_scroll(self):
        for suffix in ("py", "md"):
            with self.subTest(suffix=suffix):
                self.loaded("/#/first." + suffix)
                self.browser.evaluate("""(async () => {
                  const sessions = await import('/js/sessions.js');
                  const path = '/second.' + """ + json.dumps(suffix) + """;
                  sessions.save({id: sessions.newId(), name: path, at: Date.now(),
                    tabs: {[path]: {id: path, path}}, order: [path]});
                  await (await import('/js/util.js')).flushStore();
                })()""")
                self.browser.click("#sessionsButton")
                row = "[...document.querySelectorAll('.session')].find(row => row.textContent.includes(" + json.dumps("/second." + suffix) + "))"
                self.browser.wait_for("!!" + row)
                position = self.editor + ".getScrollTop()" if suffix == "py" else self.preview + ".scrollTop"
                scroll = self.editor + ".setScrollTop(240)" if suffix == "py" else self.preview + ".scrollTop = 240"
                # Add the tabs before the debounced preview-position save.
                self.browser.evaluate(scroll + "; " + row + ".querySelector('.session-add').click()")
                self.browser.wait_for("document.querySelector('#flash').textContent === 'Added 1 tabs'")
                self.settle()
                self.assertEqual(self.browser.evaluate("document.querySelector('#tabs [aria-selected=true]').dataset.path"), "/first." + suffix)
                self.assertEqual(self.browser.evaluate(position), 240)
        self.assertNoPageErrors()

    def test_switching_tabs_while_adding_workspace_tabs_keeps_each_position(self):
        (self.root / "third.py").write_text("third = 3\n" * 300)
        self.loaded("/#/first.py")
        self.browser.evaluate(self.editor + ".setScrollTop(240)")
        self.settle()
        self.open_file("second.py")
        self.browser.evaluate(self.editor + ".setScrollTop(600)")
        self.settle()
        self.open_file("first.py")
        self.browser.evaluate("""(async () => {
          const sessions = await import('/js/sessions.js');
          sessions.save({id:sessions.newId(),name:'Additional files',at:Date.now(),
            tabs:{'/third.py':{id:'/third.py',path:'/third.py'}},order:['/third.py']});
          await (await import('/js/util.js')).flushStore();
          const {api} = await import('/js/api.js'), read = api.read;
          api.read = path => path === '/third.py' ? new Promise(resolve => {
            window.finishAddedRead = () => read(path).then(resolve);
          }) : read(path);
        })()""")
        self.browser.click("#sessionsButton")
        row = "[...document.querySelectorAll('.session')].find(row => row.textContent.includes('Additional files'))"
        self.browser.wait_for("!!" + row)
        self.browser.evaluate(row + ".querySelector('.session-add').click()")
        self.browser.wait_for("typeof finishAddedRead === 'function'")
        self.browser.click('#tabs [data-path="/second.py"]')
        self.browser.evaluate("finishAddedRead()")
        self.browser.wait_for("document.querySelector('#flash').textContent === 'Added 1 tabs'")
        self.assertEqual(self.browser.evaluate(self.editor + ".getScrollTop()"), 240)
        self.browser.click('#tabs [data-path="/second.py"]')
        self.assertEqual(self.browser.evaluate(self.editor + ".getScrollTop()"), 600)
        self.assertNoPageErrors()

    def test_new_html_preview_starts_at_top(self):
        self.loaded("/#/first.html")
        self.browser.click("#scrollFileBottom")
        self.browser.wait_for("(async () => (await import('/js/settings.js')).readScroll('/first.html')?.frac > 0.99)()")
        self.open_file("second.html")
        self.browser.wait_for("(async () => (await import('/js/settings.js')).readScroll('/second.html')?.frac === 0)()")
        self.assertNoPageErrors()

    def test_new_markdown_stays_at_top_after_a_delayed_diagram(self):
        path = self.root / "second.md"
        path.write_text("# Diagram\n\n```mermaid\ngraph TD\nA --> B\n```\n\n" + path.read_text())
        self.loaded("/#/first.md")
        self.browser.evaluate(self.preview + ".scrollTop = 240")
        self.browser.wait_for(self.preview + ".scrollTop === 240")
        self.browser.evaluate("""(() => {
          const append = document.head.append;
          document.head.append = function(node) {
            if (!node.src?.includes('mermaid.min.js')) return append.call(this, node);
            window.releaseDiagramDownload = () => append.call(this, node);
          };
        })()""")
        self.open_file("second.md")
        self.browser.wait_for("typeof releaseDiagramDownload === 'function'")
        self.assertEqual(self.browser.evaluate(self.preview + ".scrollTop"), 0)
        self.browser.evaluate("releaseDiagramDownload()")
        self.waitFor("#preview .mermaid-figure svg")
        self.settle()
        self.assertEqual(self.browser.evaluate(self.preview + ".scrollTop"), 0)
        self.assertNoPageErrors()


class ReaderInteractionTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "commands.md").write_text("# Commands\n\n```sh\nuv run --flag\ncat log | custom-tool\n```\n")
        (self.root / "long.md").write_text("# Start\n\n" + "A paragraph.\n\n" * 40 + "> ## Quoted section\n\n" + "Another paragraph.\n\n" * 40)
        (self.root / "extra.txt").write_text("another file\n")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "inside.txt").write_text("inside\n")

    def test_unknown_shell_commands_keep_the_existing_code_theme(self):
        self.loaded("/#/commands.md")
        self.waitAllColoured()
        words = self.browser.evaluate("[...document.querySelectorAll('.hljs-built_in')].map(n=>n.textContent)")
        self.assertEqual(words, ["uv", "cat", "log", "custom-tool"])
        self.assertNoPageErrors()

    def test_contents_follow_scroll_and_remember_visibility_while_history_survives_reload(self):
        self.loaded("/#/long.md")
        self.browser.evaluate("document.querySelector('#quoted-section').scrollIntoView()")
        self.browser.wait_for("document.querySelector('.toc-item[aria-current=location]')?.textContent === 'Quoted section'")
        self.browser.click("#tocHide")
        self.loaded("/#/long.md")
        self.assertTrue(self.browser.evaluate("document.querySelector('#tocPane').hidden"))
        self.assertFalse(self.browser.evaluate("document.querySelector('#tocToggle').hidden"))
        self.browser.click("#tocToggle")
        self.browser.evaluate("[...document.querySelectorAll('#viewToggle button')].find(b=>b.textContent==='Split').click()")
        self.assertTrue(self.browser.evaluate("document.querySelector('#tocPane').hidden"))
        self.browser.evaluate("[...document.querySelectorAll('#viewToggle button')].find(b=>b.textContent==='Preview').click()")
        self.assertFalse(self.browser.evaluate("document.querySelector('#tocPane').hidden"))
        self.browser.click('#tabs .tab[data-path="/long.md"] .close')
        self.loaded("/")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 0)
        self.browser.click("#reopenTab")
        self.browser.wait_for("!!document.querySelector('#tabs .tab[data-path=\"/long.md\"]')")
        self.assertNoPageErrors()

    def test_adding_tabs_keeps_existing_dirty_model_and_source_workspace(self):
        self.loaded("/#/commands.md")
        self.browser.click('#viewToggle [data-view="edit"]')
        self.browser.evaluate("window.keptModel = monaco.editor.getEditors()[0].getModel(); keptModel.setValue('unsaved text')")
        source_id = self.browser.evaluate("""(async () => {
          const sessions = await import('/js/sessions.js'); const {flushStore} = await import('/js/util.js');
          const id = sessions.newId(); sessions.save({id,name:'Addition source',at:Date.now(),
            tabs:{'/commands.md':{id:'/commands.md',path:'/commands.md',view:'preview'},'/extra.txt':{id:'/extra.txt',path:'/extra.txt'}},
            order:['/commands.md','/extra.txt']}); await flushStore(); return id;
        })()""")
        self.browser.click("#sessionsButton")
        self.browser.wait_for("[...document.querySelectorAll('.session')].some(r=>r.textContent.includes('Addition source'))")
        self.browser.evaluate("[...document.querySelectorAll('.session')].find(r=>r.textContent.includes('Addition source')).querySelector('.session-add').click()")
        self.browser.wait_for("!!document.querySelector('#tabs .tab[data-path=\"/extra.txt\"]')")
        self.browser.wait_for("document.querySelector('#flash').textContent === 'Added 1 tabs'")
        self.assertTrue(self.browser.evaluate("monaco.editor.getEditors()[0].getModel() === keptModel"))
        self.assertEqual(self.browser.evaluate("keptModel.getValue()"), "unsaved text")
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('#tabs .tab')].map(t=>t.dataset.path)"), ["/commands.md", "/extra.txt"])
        self.assertTrue(self.browser.evaluate("(async()=>{const s=await import('/js/sessions.js');return s.readAll().some(r=>r.id===" + repr(source_id) + ");})()"))
        self.assertNoPageErrors()

    def test_tab_selection_reveals_ancestors_and_unchanged_reload_keeps_undo(self):
        for number in range(60):
            (self.root / "nested" / f"a{number:02}.txt").write_text("entry\n")
        self.loaded("/#/nested/inside.txt")
        self.browser.evaluate("location.hash = '#/extra.txt'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path === '/extra.txt'")
        self.browser.click('#tree .row[data-path="/nested"]')
        self.browser.wait_for("!document.querySelector('#tree .row[data-path=\"/nested/inside.txt\"]')")
        self.browser.click('#tabs .tab[data-path="/nested/inside.txt"]')
        self.browser.wait_for("!!document.querySelector('#tree .row[data-path=\"/nested/inside.txt\"][aria-selected]')")
        self.browser.click('#tabs .tab[data-path="/extra.txt"]')
        self.browser.evaluate("document.querySelector('#tree').scrollTop=0")
        self.browser.click('#tabs .tab[data-path="/nested/inside.txt"]')
        self.assertTrue(self.browser.evaluate("(() => {const row=document.querySelector('#tree [aria-selected]').getBoundingClientRect();const tree=document.querySelector('#tree').getBoundingClientRect();return row.top>=tree.top && row.bottom<=tree.bottom;})()"))
        self.assertEditorWritable(True)
        self.browser.evaluate("""(() => { const editor=monaco.editor.getEditors()[0]; window.savedModel=editor.getModel();
          editor.executeEdits('test',[{range:new monaco.Range(1,1,1,1),text:'changed '}]);
          document.dispatchEvent(new KeyboardEvent('keydown',{key:'s',ctrlKey:true,bubbles:true})); })()""")
        self.browser.wait_for("!document.querySelector('#tabs .tab.is-dirty') && document.querySelector('#flash').textContent.startsWith('Saved ')")
        self.browser.click("#reloadFile")
        self.browser.wait_for("document.querySelector('#flash').textContent.startsWith('Reloaded ')")
        self.assertTrue(self.browser.evaluate("monaco.editor.getEditors()[0].getModel()===savedModel && savedModel.canUndo()"))
        self.browser.click("#newSession")
        self.browser.wait_for("!!document.querySelector('dialog[open]')")
        self.browser.evaluate("document.querySelector('dialog').dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:0,clientY:0}))")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('dialog[open]')"))
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"),2)
        self.assertNoPageErrors()
