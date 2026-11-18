"""End-to-end tests that run the real page in a real browser.

A file browser is mostly frontend, and a Python-only suite can prove every
endpoint correct while the page shows nothing at all. These drive headless
Chromium against a live server and assert on the DOM it produces after the
JavaScript has run.

Skipped, loudly, when no Chromium is installed — a machine without one should
not report a green suite that silently checked none of this.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo.config import Config  # noqa: E402
from ficelo.server import serve  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import CHROME, Browser  # noqa: E402


@unittest.skipUnless(CHROME, "no Chromium; browser tests cannot run")
class BrowserCase(unittest.TestCase):
    """Drives a real headless Chromium over the DevTools protocol.

    Not `--dump-dom --virtual-time-budget`, which was the first attempt:
    virtual time advances timers but never fires requestAnimationFrame, and
    Monaco renders its text entirely inside rAF. Under virtual time the
    editor reports a correct size and zero rendered lines, so any test of
    what the editor actually shows is meaningless. See tests/cdp.py.
    """

    writable = False

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.prepare()
        launch_options = {} if self.writable is None else {"writable": self.writable}
        config = Config(root=self.root, port=0, state_dir=tempfile.mkdtemp(),
                        terminal=getattr(self, "terminal", False), **launch_options)
        self.server = serve(config, quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop)
        self.browser = Browser()
        self.addCleanup(self.browser.close)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def prepare(self):
        pass

    def url(self, route="/"):
        return "http://127.0.0.1:%d%s" % (self.port, route)

    def loaded(self, route="/", timeout=60):
        """Load the app fresh and wait until it says it has finished starting.

        The blank page in between is not ceremony. Navigating to a URL that
        differs from the current one only by its hash does not reload the
        document — it fires hashchange — so `loaded()` would return at once
        against the *previous* page, with data-ficelo-ready already set and
        the old document still on screen. A test then asserts on whatever
        the last one left behind, and passes or fails on timing.
        """
        self.browser.navigate("about:blank")
        self.browser.wait_for(
            "document.readyState === 'complete' && !document.body.dataset.ficeloReady",
            timeout=20,
            what="the page to be cleared",
        )
        self.browser.navigate(self.url(route))
        self.browser.wait_for(
            'document.body.dataset.ficeloReady === "1"', timeout=timeout, what="the app to start"
        )
        return self.browser.html(), self.browser

    def assertNoPageErrors(self, _ignored=None):
        bad = [line for line in self.browser.errors() if "favicon" not in line]
        self.assertEqual(bad, [], "the page logged errors:\n" + "\n".join(bad))

    def toggle_theme(self):
        """Change theme through Settings, then close the panel."""
        self.browser.evaluate("""(() => {
          const toggle = document.querySelector('#settingsToggle');
          if (document.querySelector('#settings').hidden) toggle.click();
          const label = document.documentElement.dataset.theme === 'dark' ? 'Light' : 'Dark';
          [...document.querySelectorAll('#settings .set-choice button')]
            .find(button => button.textContent === label).click();
          toggle.click();
        })()""")

    def assertEditorWritable(self, writable):
        self.assertEqual(
            self.browser.evaluate("monaco.editor.getEditors()[0].getOption(monaco.editor.EditorOption.readOnly)"),
            not writable,
            "source editability must follow the server launch mode",
        )

    def waitAllColoured(self, timeout=45):
        """Wait for tokens inside every named code fence in the fixture."""
        self.browser.wait_for(
            "(() => { const b = document.querySelectorAll('pre > code[class*=language-]');"
            " return b.length > 0 && Array.from(b).every("
            "  c => c.querySelector('span[class^=hljs-]')); })()",
            timeout=timeout,
            what="every fenced block to be coloured",
        )
        return self.browser.html()

    def waitFor(self, selector, count=1, timeout=45, what=None):
        """Wait until at least `count` elements match, then return the HTML.

        Needed because `loaded()` returns as soon as the app signals it has
        started, and several things finish afterwards on purpose: diagrams
        are rendered from a lazily-fetched bundle, code is coloured
        asynchronously, and the review panel is two more round trips.
        """
        self.browser.wait_for(
            "document.querySelectorAll(%s).length >= %d" % (json.dumps(selector), count),
            timeout=timeout,
            what=what or ("%d× %s" % (count, selector)),
        )
        return self.browser.html()

    def diffText(self):
        """Every rendered line of the diff, both sides, joined.

        Read from the .view-line elements rather than innerText: innerText
        on the diff editor returns the gutter and the collapsed-region
        widgets and stops early, which made a one-line render look
        plausible."""
        raw = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.monaco-diff-editor .view-line'))"
            ".map(n => n.textContent).join('\\n')"
        )
        return (raw or "").replace("\u00a0", " ")

    def editorText(self):
        """What Monaco is actually showing, with its non-breaking spaces
        turned back into ordinary ones.

        Waits for non-empty text rather than for the elements: Monaco
        inserts .view-line nodes a frame before it paints their content, so
        a count-based wait reads an empty string often enough to matter."""
        self.browser.wait_for(
            '(() => { const v = document.querySelector(".view-lines");'
            ' return v && v.innerText.trim().length > 0; })()',
            what="painted editor text",
        )
        raw = self.browser.evaluate('document.querySelector(".view-lines").innerText')
        return (raw or "").replace("\u00a0", " ")


class PageLoadTest(BrowserCase):
    def prepare(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("def hello():\n    return 1\n")
        (self.root / "notes.md").write_text("# Title\n\nSome prose.\n\n## Second\n")

    def test_the_app_starts(self):
        dom, console = self.loaded()
        self.assertNoPageErrors()

    def test_monaco_is_actually_loaded(self):
        """The single most expensive thing to get wrong: everything else can
        look right while the editor is a blank rectangle."""
        dom, _ = self.loaded()
        self.assertIn("monaco-editor", dom)

    def test_the_tree_lists_the_root(self):
        dom, _ = self.loaded()
        self.assertIn("notes.md", dom)

    def test_the_tree_is_lazy(self):
        """Only the root is listed at startup. A tree that walked everything
        up front would stall for minutes on a real home directory."""
        dom, _ = self.loaded()
        self.assertNotIn("main.py", dom, "src/ is collapsed, so its child is not in the DOM")

    def test_the_root_folder_shows_its_children_once_expanded(self):
        """`src` is a folder, so its child is behind a click; the top level
        is not."""
        dom, _ = self.loaded()
        self.assertIn(">src<", dom)

    def test_the_mode_badge_says_read_only(self):
        dom, _ = self.loaded()
        self.assertRegex(dom, r'id="mode"[^>]*>read-only<')

    def test_the_title_names_the_folder(self):
        self.loaded("/?path=" + str(self.root / "src"))
        self.assertEqual(self.browser.evaluate("document.title"), str(self.root / "src"))


class MarkdownRenderTest(BrowserCase):
    def prepare(self):
        (self.root / "doc.md").write_text(
            "# Heading One\n"
            "\n"
            "A paragraph with `code` in it.\n"
            "\n"
            "## Heading Two\n"
            "\n"
            "```python\n"
            "def f():\n"
            "    return 2\n"
            "```\n"
            "\n"
            "```js\n"
            "const x = 1;\n"
            "```\n"
            "\n"
            "```c++\n"
            "int main() { return 0; }\n"
            "```\n"
            "\n"
            "- one\n"
            "- two\n"
            "\n"
            "See [the reference][ref].\n"
            "\n"
            "[ref]: https://example.com\n"
        )

    def test_it_renders_as_html_not_as_source(self):
        dom, console = self.loaded("/#/doc.md")
        self.assertNoPageErrors()
        self.assertIn("<h1", dom)
        self.assertIn("Heading One", dom)
        self.assertIn("<li>one</li>", dom)

    def test_every_block_carries_its_source_line(self):
        """What makes an accurate two-pane scroll sync possible at v0.4. The
        heading is line 1 and the paragraph line 3."""
        dom, _ = self.loaded("/#/doc.md")
        self.assertIn('class="md-block" data-src-line="1"', dom)
        self.assertIn('data-src-line="3"', dom)

    def test_reference_links_resolve(self):
        """marked resolves reference definitions at lex time, so rendering
        token-by-token drops them unless `links` is carried across. Without
        that this renders as the literal text [the reference][ref]."""
        dom, _ = self.loaded("/#/doc.md")
        self.assertIn('href="https://example.com"', dom)
        self.assertNotIn("[the reference][ref]", dom)

    def test_fenced_code_is_coloured(self):
        """Require actual tokens inside the rendered fence, not CSS rules."""
        self.loaded("/#/doc.md")
        dom = self.waitAllColoured()
        block = re.search(r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>", dom, re.S)
        self.assertIsNotNone(block, "no fenced code block rendered")
        self.assertIn('<span class="hljs-', block.group(1))

    def test_a_fence_named_by_an_alias_is_also_coloured(self):
        """Both shorthand and punctuation-bearing fence names resolve."""
        self.loaded("/#/doc.md")
        dom = self.waitAllColoured()
        blocks = re.findall(r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>", dom, re.S)
        self.assertEqual(len(blocks), 3, "all three fenced blocks should render")
        self.assertIn('<span class="hljs-', blocks[1], "the ```js block stayed grey")
        self.assertIn('<span class="hljs-', blocks[2], "the ```c++ block stayed grey")

    def test_fence_detection_plaintext_shell_and_theme(self):
        self.loaded("/#/doc.md")
        result = self.browser.evaluate(r'''(async () => {
          const {renderMarkdown, colouriseCode} = await import('/js/markdown.js');
          const host = document.createElement('div'); host.className = 'markdown';
          document.body.append(host);
          const examples = [
            ['', 'import os\nprint("hello")'],
            ['plaintext', 'return "plain"'], ['unknown-grammar', 'return "plain"'],
            ['dockerfile', 'FROM alpine\nRUN echo hello'],
            ['sh', 'BUILD=fast custom-tool --flag "--quoted" | next-step -x'],
            ['zsh', 'custom-tool --flag'], ['console', '$ echo hello'],
          ];
          const before = document.documentElement.dataset.theme;
          try {
            const rows = [];
            for (const [language, text] of examples) {
              renderMarkdown('```' + language + '\n' + text + '\n```', window.marked, host);
              await colouriseCode(host);
              const code = host.querySelector('code');
              rows.push({language, source: code.textContent, tokens: code.querySelectorAll('span').length,
                commands: [...code.querySelectorAll('.hljs-built_in')].map(n => n.textContent),
                flags: [...code.querySelectorAll('.hljs-attr')].map(n => n.textContent)});
            }
            renderMarkdown('```python\nreturn "value"\n```', window.marked, host);
            await colouriseCode(host);
            const colours = ['light', 'dark'].map(theme => {
              document.documentElement.dataset.theme = theme;
              return getComputedStyle(host.querySelector('.hljs-keyword')).color;
            });
            return {rows, colours};
          } finally { host.remove(); document.documentElement.dataset.theme = before; }
        })()''')
        rows = result["rows"]
        self.assertGreater(rows[0]["tokens"], 0, "an unlabelled fence should use detection")
        self.assertEqual([row["tokens"] for row in rows[1:3]], [0, 0])
        self.assertGreater(rows[3]["tokens"], 0, "Dockerfile grammar did not load")
        self.assertEqual(rows[4]["commands"], ["custom-tool", "next-step"])
        self.assertEqual(rows[4]["flags"], ["--flag", "-x"])
        self.assertEqual(rows[4]["source"], 'BUILD=fast custom-tool --flag "--quoted" | next-step -x\n')
        self.assertEqual(rows[5]["commands"], ["custom-tool"])
        self.assertEqual(rows[5]["flags"], ["--flag"])
        self.assertGreater(rows[6]["tokens"], 0, "console prompts should be highlighted")
        self.assertEqual(result["colours"], ["rgb(166, 38, 164)", "rgb(198, 120, 221)"])

    def test_the_contents_pane_lists_the_headings(self):
        dom, _ = self.loaded("/#/doc.md")
        self.assertRegex(dom, r'class="toc-item depth-1"[^>]*>Heading One<')
        self.assertRegex(dom, r'class="toc-item depth-2"[^>]*>Heading Two<')

    def test_opening_a_file_opens_a_tab_for_it(self):
        dom, _ = self.loaded("/#/doc.md")
        self.assertRegex(dom, r'class="tab[^"]*"[^>]*data-path="/doc\.md"')

    def test_a_script_in_markdown_is_not_kept(self):
        """Rendered markdown is innerHTML, and a README from a repository you
        just cloned is not a document you wrote."""
        (self.root / "evil.md").write_text(
            "# Hi\n\n<script>window.pwned=1</script>\n\n<a href=\"javascript:alert(1)\">x</a>\n"
        )
        dom, _ = self.loaded("/#/evil.md")
        self.assertNotIn("window.pwned", dom)
        self.assertNotIn("javascript:alert", dom)


class SourceFileTest(BrowserCase):
    def prepare(self):
        (self.root / "main.py").write_text("def hello():\n    return 1\n")
        (self.root / "many.py").write_text(
            "".join("line_%02d = %d\n" % (n, n) for n in range(1, 41))
        )

    def test_a_python_file_opens_in_the_editor(self):
        dom, console = self.loaded("/#/main.py")
        self.assertNoPageErrors()
        self.assertIn("monaco-editor", dom)

    def test_the_editor_is_read_only_on_a_read_only_server(self):
        self.loaded("/#/main.py")
        self.browser.wait_for('document.querySelectorAll(".view-line").length > 0')
        self.assertEqual(self.browser.text("#mode"), "read-only")
        self.assertTrue(
            self.browser.evaluate("monaco.editor.getEditors()[0].getOption("
                                  "monaco.editor.EditorOption.readOnly)"),
            "the editor itself should refuse edits, not merely say so",
        )

    def test_the_status_bar_names_the_file_and_its_kind(self):
        self.loaded("/#/main.py")
        self.browser.wait_for("document.getElementById('sbPath').textContent.length > 0")
        self.assertIn("/main.py", self.browser.evaluate("document.getElementById('sbPath').textContent"))
        self.assertEqual(self.browser.text("#sbName"), "main.py", "the filename must survive clipping")
        self.assertEqual(self.browser.text("#sbKind"), "Python")
        self.assertRegex(self.browser.text("#sbSize"), r"\d")

    def test_it_renders_the_whole_visible_file_not_just_the_first_line(self):
        """The editor is created while its host is hidden, which is what
        left the diff editor 5px tall and rendering one line. Asserting on
        text from line 1 cannot tell the two states apart, so this looks
        for a line well down the file."""
        self.loaded("/#/many.py")
        shown = self.editorText()
        self.assertIn("line_01 = 1", shown)
        self.assertIn("line_20 = 20", shown, "only the top of the file rendered")
        rendered = self.browser.evaluate('document.querySelectorAll(".view-line").length')
        self.assertGreater(rendered, 10, "only %d lines rendered" % rendered)

    def test_the_editor_fills_its_container(self):
        self.loaded("/#/many.py")
        self.editorText()
        ratio = self.browser.evaluate(
            "(() => { const e = monaco.editor.getEditors()[0].getLayoutInfo();"
            " const h = document.getElementById('editorHost').getBoundingClientRect();"
            " return h.height > 0 ? e.height / h.height : 0; })()"
        )
        self.assertGreater(ratio, 0.5, "the editor is %.2f of its host" % ratio)

    def test_a_missing_file_says_so_rather_than_hanging(self):
        dom, _ = self.loaded("/#/nope.py")
        self.assertIn("no such file", dom)


class ArtifactReaderTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "math.md").write_text(
            "---\ntitle: A formula\ntags:\n  - algebra\n---\n# Formula\n\n"
            "$x_1^2$ and \\(y+1\\). Costs $5 and $10. ` $code$ `\n\n"
            "$$\na^2+b^2=c^2\n$$\n\n\\[z=3\\]\n\n```math\nf(x)=x^2\n```\n"
            "\n[next](next.md#destination)\n"
        )
        (self.root / "next.md").write_text("# Next\n\n" + "text\n\n" * 100 + "## Destination\n")
        (self.root / "data.json").write_text('{"safe":"<img onerror=alert(1)>","nested":{"a":[1,2]}}')
        (self.root / "events.jsonl").write_text('{"ok":true}\npartial {\n{"end":2}\n')
        (self.root / "run.log").write_text("\x1b[31mred\ncontinued\x1b[0m normal\n")
        (self.root / "change.patch").write_text("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n")
        (self.root / "page.html").write_text('<h1>Page</h1><a href="next.md">next</a><script>window.localScript=true;</script>')

    def test_math_frontmatter_and_document_links_render_together(self):
        self.loaded("/#/math.md")
        self.waitFor(".katex", 5)
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#preview .katex').length"), 5)
        self.assertEqual(self.browser.evaluate("document.querySelector('#preview h1').textContent"), "Formula")
        self.assertIn("A formula", self.browser.text(".front-matter"))
        self.assertIn("Costs $5 and $10", self.browser.text("#preview"))
        self.assertIn("$code$", self.browser.text("#preview code"))
        self.browser.evaluate("(async () => { const {writeScroll} = await import('/js/settings.js'); writeScroll('/next.md', {anchor:'next', top:0, frac:0}); })()")
        self.browser.click('#preview a[href="next.md#destination"]')
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]').dataset.path === '/next.md'")
        self.browser.wait_for("document.querySelector('#preview').scrollTop > 500")
        self.assertNoPageErrors()

    def test_literal_percent_in_an_image_path_keeps_the_reader_working(self):
        (self.root / "percent.md").write_text("# Before\n\n![percent](file%name.svg)\n\n# After\n")
        (self.root / "file%name.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
        self.loaded("/#/percent.md")
        self.browser.wait_for("document.querySelector('#preview img').naturalWidth === 1")
        self.assertIn("After", self.browser.text("#preview"))
        self.assertNoPageErrors()

    def test_html_fragment_links_stay_inside_the_document(self):
        (self.root / "fragment.html").write_text('<a href="#section">Jump</a><div style="height:1500px"></div><h1 id="section">Section</h1>')
        self.browser.close()
        self.browser = Browser(extra_args=["--disable-site-isolation-trials"])
        self.addCleanup(self.browser.close)
        self.loaded("/#/fragment.html")
        self.waitFor("iframe.html-preview")
        tree = self.browser._call("Page.getFrameTree")["frameTree"]
        frame_id = tree["childFrames"][0]["frame"]["id"]
        context = self.browser._call("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "test"})["executionContextId"]
        self.browser._call("Runtime.evaluate", {"contextId": context, "expression": "document.querySelector('a').click()"})
        self.browser.wait_for("(async () => (await import('/js/settings.js')).readScroll('/fragment.html')?.anchor === '#section')()")
        frame = self.browser._call("Page.getFrameTree")["frameTree"]["childFrames"][0]["frame"]
        self.assertTrue(frame["url"].startswith("about:srcdoc"), frame["url"])
        self.assertNoPageErrors()

    def test_structured_previews_preserve_invalid_records_and_escape_text(self):
        self.loaded("/#/data.json")
        self.assertIn("<img onerror=alert(1)>", self.browser.text(".json-document"))
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.json-document img').length"), 0)
        self.browser.click('.json-branch summary')
        self.assertFalse(self.browser.evaluate("document.querySelector('.json-branch').open"))
        self.loaded("/#/events.jsonl")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.json-record').length"), 3)
        self.assertIn("partial {", self.browser.text(".json-document"))

    def test_log_and_patch_previews_keep_colour_and_headers_distinct(self):
        self.loaded("/#/run.log")
        self.assertIn("red\ncontinued", self.browser.text(".log-document"))
        self.assertNotIn("\x1b", self.browser.text(".log-document"))
        self.assertNotEqual(self.browser.evaluate("getComputedStyle(document.querySelector('.log-document span')).color"),
                            self.browser.evaluate("getComputedStyle(document.querySelector('.log-document')).color"))
        self.loaded("/#/change.patch")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.patch-line.is-header').length"), 2)
        self.assertEqual(self.browser.text(".patch-line.is-add"), "+new")
        self.assertEqual(self.browser.text(".patch-line.is-remove"), "-old")

    def test_html_is_isolated_and_forged_position_reports_are_ignored(self):
        self.loaded("/#/page.html")
        self.waitFor("iframe.html-preview")
        self.assertTrue(self.browser.evaluate("(() => { try { return !document.querySelector('iframe').contentWindow.document; } catch (_) { return true; } })()"))
        self.browser.evaluate("window.postMessage({ficelo:'position',anchor:'forged',frac:0.8}, '*')")
        self.assertNotIn("forged", self.browser.evaluate("(async () => JSON.stringify(await (await fetch('/api/state')).json()))()"))
        self.assertNoPageErrors()

    def test_artifact_source_is_editable_and_saved_on_return_to_preview(self):
        self.loaded("/#/data.json")
        self.browser.click('#viewToggle [data-view="edit"]')
        self.browser.wait_for("!document.getElementById('editorHost').hidden")
        self.assertEditorWritable(True)
        self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue('{\"changed\":42}')")
        self.browser.evaluate("Array.from(document.querySelectorAll('#viewToggle button')).find(b => b.textContent === 'Preview').click()")
        self.browser.wait_for("!document.getElementById('viewerHost').hidden")
        self.assertEqual(json.loads((self.root / "data.json").read_text()), {"changed": 42})
        self.assertIn("changed", self.browser.text(".json-document"))


class SearchTest(BrowserCase):
    def prepare(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "parser.py").write_text("x")

    def test_the_search_endpoint_reaches_nested_files(self):
        """The tree is lazy, so search is the only way to a nested file
        without clicking; worth proving the wiring end to end."""
        import json
        import urllib.request

        with urllib.request.urlopen(
            "http://127.0.0.1:%d/api/search?q=parser" % self.port, timeout=5
        ) as res:
            body = json.load(res)
        self.assertEqual(body["matches"][0]["path"], "/src/parser.py")


class DiagramTest(BrowserCase):
    def prepare(self):
        (self.root / "plain.md").write_text("# No diagrams here\n\n```python\nx = 1\n```\n")
        (self.root / "chart.md").write_text(
            "# With a diagram\n"
            "\n"
            "```mermaid\n"
            "graph TD\n"
            "  A[Start] --> B[Finish]\n"
            "```\n"
        )
        (self.root / "broken.md").write_text(
            "# Bad diagram\n\n```mermaid\nthis is not a diagram at all {{{\n```\n"
        )

    def test_a_mermaid_block_becomes_an_svg(self):
        self.loaded("/#/chart.md")
        dom = self.waitFor(".mermaid-figure svg", what="a rendered diagram")
        self.assertNoPageErrors()
        self.assertIn("Finish", dom)

    def test_diagram_download_does_not_interrupt_the_code_editor(self):
        self.loaded("/#/plain.md")
        self.browser.evaluate("""(() => {
          const append = document.head.append;
          window.editorDefine = window.define;
          document.head.append = function(node) {
            if (!node.src?.includes('mermaid.min.js')) return append.call(this, node);
            window.releaseDiagramDownload = () => append.call(this, node);
          };
          location.hash = '#/chart.md';
        })()""")
        self.browser.wait_for("typeof releaseDiagramDownload === 'function'")
        self.assertTrue(self.browser.evaluate("window.define === editorDefine"))
        self.browser.click('#viewToggle [data-view="split"]')
        self.waitFor("#editorHost .view-line")
        self.browser.evaluate("releaseDiagramDownload()")
        self.waitFor(".mermaid-figure svg")
        self.assertNoPageErrors()

    def test_a_document_without_diagrams_never_loads_mermaid(self):
        """The bundle is 5 MB. Fetching it for every markdown file would be a
        real cost paid by the majority of documents, which have no diagram."""
        self.loaded("/#/plain.md")
        # Wait for the markdown to be on screen, so this is not merely early.
        self.waitFor(".md-block", what="the document to render")
        dom = self.browser.html()
        self.assertNotIn("mermaid.min.js", dom)
        self.assertNotIn("mermaid-figure", dom)

    def test_a_broken_diagram_shows_the_error_and_the_source(self):
        """It must not vanish silently, and it must not take the rest of the
        document with it."""
        self.loaded("/#/broken.md")
        dom = self.waitFor(".is-diagram-error", what="the diagram error")
        self.assertIn("Bad diagram", dom, "the rest of the document still rendered")


class ViewerTest(BrowserCase):
    PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def prepare(self):
        (self.root / "pic.png").write_bytes(self.PNG)
        (self.root / "doc.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        (self.root / "data.csv").write_text(
            'name,note,size\n'
            'alpha,"has, a comma",1\n'
            'beta,"says ""hi""",2\n'
            'gamma,"two\nlines",3\n'
        )
        (self.root / "nb.ipynb").write_text(json.dumps({
            "cells": [
                {"cell_type": "markdown", "source": ["# Notebook title\n"]},
                {"cell_type": "code", "execution_count": 4,
                 "source": ["print('hello')\n"],
                 "outputs": [{"output_type": "stream", "text": ["hello\n"]}]},
            ],
            "nbformat": 4,
        }))

    def test_an_image_is_shown_rather_than_called_binary(self):
        dom, console = self.loaded("/#/pic.png")
        self.assertNoPageErrors()
        self.assertIn('class="view-image"', dom)
        self.assertNotIn("looks like a binary file", dom)

    def test_a_pdf_gets_an_embed(self):
        dom, _ = self.loaded("/#/doc.pdf")
        self.assertIn('class="view-pdf"', dom)

    def test_a_csv_becomes_a_table(self):
        dom, console = self.loaded("/#/data.csv")
        self.assertNoPageErrors()
        self.assertIn('class="view-table"', dom)
        self.assertIn("<th>name</th>", dom)

    def test_quoted_csv_fields_are_parsed_not_split(self):
        """Every real export has a comma inside a quoted field, a doubled
        quote, and a newline inside a cell. Splitting on commas mangles all
        three and the damage looks like plausible data."""
        dom, _ = self.loaded("/#/data.csv")
        self.assertIn("has, a comma", dom)
        self.assertIn('says "hi"', dom)
        self.assertIn("two\nlines", dom)
        # Three data rows, not five: the embedded newline inside a quoted
        # field is not a row break. Counted on <td> so the header's own
        # rownum <th> is not included.
        self.assertEqual(dom.count('<td class="rownum">'), 3)

    def test_a_notebook_renders_cells(self):
        dom, console = self.loaded("/#/nb.ipynb")
        self.assertNoPageErrors()
        self.assertIn("Notebook title", dom)
        self.assertIn("<h1", dom, "markdown cells render as markdown")
        self.assertIn("print(&#x27;hello&#x27;)", dom.replace("'", "&#x27;"))
        self.assertIn('class="nb-output"', dom)
        self.assertIn("[4]", dom, "the execution count is shown")


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class ReviewTest(BrowserCase):
    def git(self, *args):
        subprocess.run(
            ["git"] + list(args), cwd=str(self.root), capture_output=True, check=True, timeout=30
        )

    def prepare(self):
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        (self.root / "kept.py").write_text("stays = 1\n")
        (self.root / "edited.py").write_text("value = 1\n" + "filler\n" * 40)
        self.git("add", "-A")
        self.git("commit", "-m", "base commit")
        (self.root / "edited.py").write_text("value = 2\n" + "filler\n" * 40)
        (self.root / "added.py").write_text("brand = 'new'\n")

    def test_the_review_panel_lists_changed_files(self):
        self.loaded("/#review")
        dom = self.waitFor(".change", count=2, what="the changed-file list")
        self.assertNoPageErrors()
        self.assertIn("edited.py", dom)
        self.assertIn("added.py", dom)

    def test_an_unchanged_file_is_not_listed_as_changed(self):
        self.loaded("/#review")
        dom = self.waitFor(".change", count=2, what="the changed-file list")
        panel = re.search(r'id="review".*?</div></div>', dom, re.S)
        self.assertNotIn("kept.py", panel.group(0) if panel else dom.split('id="review"')[1][:4000])

    def test_the_branch_and_base_are_shown(self):
        self.loaded("/#review")
        self.waitFor(".review-branch", what="the review header")
        self.assertEqual(self.browser.text(".review-branch"), "main")
        self.assertIn("vs ", self.browser.text(".review-base"))

    def test_the_commit_list_is_behind_its_own_tab(self):
        """The history lists work beyond the base, separately from working files."""
        self.git("checkout", "-b", "work")
        self.git("add", "-A")
        self.git("commit", "-m", "branch work")
        self.loaded("/#review")
        self.waitFor(".review-switch", what="the review switcher")
        self.assertNotIn("branch work", self.browser.text("#review"))
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.review-switch .seg'))"
            ".find(b => b.textContent === 'Commits').click()"
        )
        self.waitFor(".commit", what="the commit list")
        panel = self.browser.text("#review")
        self.assertIn("branch work", panel)
        self.assertNotIn("base commit", panel)

    def test_changed_files_are_grouped_by_folder(self):
        """A flat list is fine for three files and unreadable for eighty,
        where the same filename appears in four places."""
        (self.root / "src").mkdir()
        (self.root / "src" / "nested.py").write_text("x = 1\n")
        self.loaded("/#review")
        self.waitFor(".change-group", what="a folder heading")
        self.assertIn("src", self.browser.text("#review"))

    def test_a_folder_can_be_collapsed(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "nested.py").write_text("x = 1\n")
        self.loaded("/#review")
        self.waitFor(".change-group", what="a folder heading")
        before = self.browser.evaluate("document.querySelectorAll('.change').length")
        self.browser.evaluate("document.querySelector('.change-group').click()")
        self.browser.wait_for(
            "document.querySelectorAll('.change').length < %d" % before,
            what="the folder to collapse",
        )

    def test_the_header_counts_the_change(self):
        self.loaded("/#review")
        self.waitFor(".review-stat", what="the diff stat")
        stat = self.browser.text(".review-stat")
        self.assertIn("file", stat)
        self.assertRegex(stat, r"\+\d+")
        self.assertRegex(stat, r"−\d+")

    def test_an_untracked_file_is_marked_as_such(self):
        """git diff never reports these and they are usually the most
        interesting thing in a review."""
        self.loaded("/#review")
        dom = self.waitFor(".change-status.is-untracked", what="an untracked entry")
        self.assertRegex(dom, r'change-status is-untracked[^>]*>\?<')


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class DiffViewTest(BrowserCase):
    """The diff editor itself, reached by URL rather than by clicking."""

    def git(self, *args):
        subprocess.run(
            ["git"] + list(args), cwd=str(self.root), capture_output=True, check=True, timeout=30
        )

    def prepare(self):
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        # The change is deliberately NOT on line 1. With it there, a diff
        # editor rendering a single line satisfies every assertion below
        # while showing almost nothing — which is exactly what it was doing.
        (self.root / "code.py").write_text(
            "header = 0\nalpha = 1\nbeta = 2\n" + "same\n" * 60
        )
        self.git("add", "-A")
        self.git("commit", "-m", "base")
        (self.root / "code.py").write_text(
            "header = 0\nalpha = 99\nbeta = 2\n" + "same\n" * 60
        )

    def test_the_diff_endpoint_returns_both_sides(self):
        import json as _json
        import urllib.request

        url = "http://127.0.0.1:%d/api/git/diff?path=%%2Fcode.py" % self.port
        with urllib.request.urlopen(url, timeout=10) as res:
            body = _json.load(res)
        self.assertIn("alpha = 1", body["old"])
        self.assertIn("alpha = 99", body["new"])

    def test_the_diff_editor_renders_both_sides(self):
        self.loaded("/#diff/code.py")
        dom = self.waitFor(".monaco-diff-editor .view-line", count=2, what="diff lines")
        self.assertNoPageErrors()
        # Monaco's diff editor marks its two panes with these classes.
        self.assertIn("monaco-diff-editor", dom)
        self.assertIn("original", dom)
        self.assertIn("modified", dom)
        self.assertEqual(self.browser.evaluate("""(() => {
          const diff = monaco.editor.getDiffEditors()[0];
          return [diff.getOriginalEditor(), diff.getModifiedEditor()].map(editor =>
            editor.getOption(monaco.editor.EditorOption.readOnly));
        })()"""), [True, True])

    def test_the_changed_line_appears_on_both_sides(self):
        self.loaded("/#diff/code.py")
        self.waitFor(".monaco-diff-editor .view-line", count=4, what="diff lines")
        shown = self.diffText()
        self.assertIn("alpha = 1", shown, "the base side is missing")
        self.assertIn("alpha = 99", shown, "the working-tree side is missing")

    def test_the_panes_are_the_size_of_their_container(self):
        """A diff editor created while hidden measures zero and renders one
        line per side. Everything above still passes in that state if the
        change is on line 1, so the size is asserted directly."""
        self.loaded("/#diff/code.py")
        self.waitFor(".monaco-diff-editor .view-line", count=4, what="diff lines")
        sizes = self.browser.evaluate(
            "(() => { const e = monaco.editor.getDiffEditors()[0];"
            " const o = e.getOriginalEditor().getLayoutInfo();"
            " const host = document.getElementById('diffHost').getBoundingClientRect();"
            " return JSON.stringify([o.height, Math.round(host.height)]); })()"
        )
        import json as _json

        pane, host = _json.loads(sizes)
        self.assertGreater(pane, host * 0.5, "the pane is %dpx in a %dpx host" % (pane, host))

    def test_context_around_the_change_is_shown(self):
        """Three lines either side, per hideUnchangedRegions. One rendered
        line means the editor never got a size."""
        self.loaded("/#diff/code.py")
        self.waitFor(".monaco-diff-editor .view-line", count=4, what="diff lines")
        shown = self.diffText()
        self.assertIn("header = 0", shown, "the line above the change is missing")
        self.assertIn("beta = 2", shown, "the line below the change is missing")

    def test_unchanged_regions_are_collapsed(self):
        """60 identical lines should not all be rendered. Monaco's
        hideUnchangedRegions is what provides expand-context, and without it
        every diff is the whole file."""
        self.loaded("/#diff/code.py")
        # The collapsed band is inserted once the diff has settled, which is
        # after the first lines render — so wait for the band itself.
        dom = self.waitFor(".diff-hidden-lines", what="the collapsed-region band")
        rendered = self.browser.evaluate(
            'document.querySelectorAll(".monaco-diff-editor .view-line").length'
        )
        self.assertLess(
            rendered, 20, "62 lines rendered means nothing was collapsed (%d)" % rendered
        )

    def test_it_starts_side_by_side_and_the_toggle_switches_it(self):
        """Monaco marks the two-pane layout with a `side-by-side` class on
        the diff editor, and drops it for the unified one."""
        self.loaded("/#diff/code.py")
        self.waitFor(".monaco-diff-editor .view-line", count=4, what="diff lines")
        self.assertIn(
            "side-by-side",
            self.browser.evaluate("document.querySelector('.monaco-diff-editor').className"),
        )
        self.browser.click('#diffLayout .seg[data-layout="inline"]')
        self.browser.wait_for(
            "!document.querySelector('.monaco-diff-editor').className.includes('side-by-side')",
            what="the unified layout",
        )
        # And the change is still both readable and marked as a change.
        shown = self.diffText()
        self.assertIn("alpha = 1", shown)
        self.assertIn("alpha = 99", shown)

    def test_the_layout_choice_survives_a_reload(self):
        self.loaded("/#diff/code.py")
        self.waitFor(".monaco-diff-editor .view-line", count=4, what="diff lines")
        self.browser.click('#diffLayout .seg[data-layout="inline"]')
        self.browser.wait_for(
            "!document.querySelector('.monaco-diff-editor').className.includes('side-by-side')"
        )
        self.loaded("/#diff/code.py")
        self.waitFor(".monaco-diff-editor .view-line", count=4, what="diff lines after reload")
        self.assertNotIn(
            "side-by-side",
            self.browser.evaluate("document.querySelector('.monaco-diff-editor').className"),
        )

    def test_a_diff_tab_is_tinted_differently_from_a_file_tab(self):
        self.loaded("/#diff/code.py")
        dom = self.waitFor(".tab.is-diff", what="a diff tab")
        self.assertRegex(dom, r'class="tab[^"]*is-diff')

    def test_a_file_outside_the_repository_is_refused(self):
        import urllib.error
        import urllib.request

        url = "http://127.0.0.1:%d/api/git/diff?path=%%2Fnope%%2Fx.py" % self.port
        try:
            with urllib.request.urlopen(url, timeout=10) as res:
                body = res.read()
            # A path inside the root but absent is answered, not refused.
            self.assertIn(b'"old": null', body)
        except urllib.error.HTTPError as err:
            self.assertIn(err.code, (400, 404))


class SplitViewTest(BrowserCase):
    writable = True

    def prepare(self):
        body = ["# Title", "", "First paragraph.", ""]
        for n in range(12):
            body += ["## Section %d" % n, "", "Body of section %d." % n, ""]
        (self.root / "doc.md").write_text("\n".join(body))

    def enter_split(self):
        self.loaded("/#/doc.md")
        self.waitFor(".md-block", what="the preview")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('#viewToggle .seg'))"
            ".find(b => b.textContent === 'Split').click()"
        )
        self.browser.wait_for(
            "document.querySelector('.stage').classList.contains('md-split')",
            what="split layout",
        )
        self.browser.wait_for(
            'document.querySelectorAll(".view-line").length > 0', what="the editor to render"
        )

    def test_both_panes_are_visible_at_once(self):
        self.enter_split()
        self.assertNoPageErrors()
        visible = self.browser.evaluate(
            "(() => { const e = document.getElementById('editorHost'),"
            " p = document.getElementById('previewHost');"
            " return !e.hidden && !p.hidden; })()"
        )
        self.assertTrue(visible, "both panes should be shown in split")

    def test_the_panes_sit_side_by_side_not_overlapping(self):
        self.enter_split()
        overlap = self.browser.evaluate(
            "(() => { const e = document.getElementById('editorHost').getBoundingClientRect(),"
            " p = document.getElementById('previewHost').getBoundingClientRect();"
            " return e.right > p.left + 1; })()"
        )
        self.assertFalse(overlap, "the editor pane overlaps the preview")

    def test_scrolling_the_editor_moves_the_preview(self):
        self.enter_split()
        before = self.browser.evaluate("document.getElementById('preview').scrollTop")
        self.browser.evaluate(
            "(() => { const t = document.querySelector('.stage');"
            " window.__ed = monaco.editor.getEditors()[0];"
            " window.__ed.setScrollTop(window.__ed.getTopForLineNumber(40)); })()"
        )
        self.browser.wait_for(
            "document.getElementById('preview').scrollTop > %d" % (before + 20),
            what="the preview to follow the editor",
        )

    def test_scrolling_the_preview_moves_the_editor(self):
        self.enter_split()
        self.browser.evaluate("document.getElementById('preview').scrollTop = 800")
        self.browser.wait_for(
            "monaco.editor.getEditors()[0].getScrollTop() > 20",
            what="the editor to follow the preview",
        )

    def test_the_two_do_not_chase_each_other(self):
        """Each pane's scroll handler fires the other's. Without the
        re-entrancy guard they ping-pong and neither settles."""
        self.enter_split()
        self.browser.evaluate("document.getElementById('preview').scrollTop = 600")
        import time

        time.sleep(1.2)
        first = self.browser.evaluate("document.getElementById('preview').scrollTop")
        time.sleep(1.0)
        second = self.browser.evaluate("document.getElementById('preview').scrollTop")
        self.assertEqual(first, second, "the panes are still moving each other")

    def test_the_contents_pane_makes_way_for_the_second_column(self):
        self.enter_split()
        self.assertTrue(self.browser.evaluate("document.getElementById('tocPane').hidden"))

    def test_the_view_is_remembered_per_file(self):
        """Switching one document to split must not put every markdown tab
        into split on the next reload."""
        self.enter_split()
        (self.root / "other.md").write_text("# Other\n\nplain\n")
        self.loaded("/#/other.md")
        self.waitFor(".md-block", what="the other document")
        self.assertFalse(
            self.browser.evaluate(
                "document.querySelector('.stage').classList.contains('md-split')"
            ),
            "the second document opened in split",
        )


class LanguageServiceTest(BrowserCase):
    """Opens the file types whose language services are the bulk of Monaco.

    These exist to catch a trimmed bundle breaking: TypeScript, JSON, CSS
    and HTML each have a worker that a language service would try to spawn,
    and a missing one shows up as a console error rather than as anything
    visible."""

    writable = True

    def prepare(self):
        (self.root / "app.ts").write_text("const x: number = 1;\nexport default x;\n")
        (self.root / "data.json").write_text('{"a": 1, "b": [2, 3]}\n')
        (self.root / "style.css").write_text("body { color: red; }\n")
        (self.root / "page.htm").write_text("<div class='x'>hi</div>\n")

    def open_source(self, name):
        self.loaded("/#/" + name)
        self.browser.evaluate("document.querySelector('#viewToggle [data-view=\"edit\"]')?.click()")

    def open_and_check(self, name, expect):
        self.open_source(name)
        self.browser.wait_for(
            'document.querySelectorAll(".view-line").length > 0', what="rendered lines for " + name
        )
        shown = self.editorText()
        self.assertIn(expect, shown, name)
        self.assertNoPageErrors()

    def test_typescript_opens_cleanly(self):
        self.open_and_check("app.ts", "const x: number = 1;")

    def test_json_opens_cleanly(self):
        self.open_and_check("data.json", '"a": 1')

    def test_css_opens_cleanly(self):
        self.open_and_check("style.css", "color: red")

    def test_html_opens_cleanly(self):
        self.open_and_check("page.htm", "hi")

    def test_the_typescript_worker_actually_answers(self):
        """The one check that catches a trim going too far.

        Deleting the worker payloads leaves everything else working and
        produces no console error: the worker URL 404s, and the promise from
        getTypeScriptWorker() simply never settles. Measured — a probe with
        them removed hung until the socket timed out. So this races the call
        against a timer and requires it to win."""
        self.loaded("/#/app.ts")
        self.browser.wait_for('document.querySelectorAll(".view-line").length > 0')
        verdict = self.browser.evaluate(
            "Promise.race(["
            "  (async () => {"
            "     const get = await monaco.languages.typescript.getTypeScriptWorker();"
            "     const model = monaco.editor.getEditors()[0].getModel();"
            "     const w = await get(model.uri);"
            "     await w.getSemanticDiagnostics(model.uri.toString());"
            "     return 'ANSWERED';"
            "  })().catch(e => 'THREW ' + (e && e.message)),"
            "  new Promise(r => setTimeout(() => r('HUNG'), 12000)),"
            "])",
            timeout=40,
        )
        self.assertEqual(verdict, "ANSWERED")

    def test_typing_in_typescript_does_not_error(self):
        """Diagnostics run on change and are what spawn the TypeScript
        worker. Merely opening a file may never touch it, so a trimmed
        bundle can look fine until someone types."""
        self.loaded("/#/app.ts")
        self.browser.wait_for('document.querySelectorAll(".view-line").length > 0')
        self.assertEditorWritable(True)
        self.browser.evaluate(
            "(() => { const e = monaco.editor.getEditors()[0];"
            " e.getModel().setValue('const y: string = 2;\\nconst z = y.toUpperCase();\\n');"
            " })()"
        )
        import time

        time.sleep(2.5)
        self.assertNoPageErrors()

    def test_each_is_syntax_highlighted(self):
        """Highlighting comes from the Monarch grammars, which are separate
        from the language services — so this must survive a trim that drops
        the services."""
        for name in ("app.ts", "data.json", "style.css"):
            self.open_source(name)
            self.browser.wait_for(
                'document.querySelectorAll(".view-line span[class^=mtk]").length > 1',
                what="highlighted tokens in " + name,
            )


class TabBehaviourTest(BrowserCase):
    def prepare(self):
        import os
        import time

        (self.root / "fresh.txt").write_text("new\n")
        (self.root / "stale.txt").write_text("old\n")
        # Sixty days back, which is past the last age boundary.
        old = time.time() - 60 * 86400
        os.utime(self.root / "stale.txt", (old, old))

    def test_tabs_are_faded_by_how_recently_the_file_changed(self):
        self.loaded("/#/fresh.txt")
        self.waitFor(".tab", what="the first tab")
        self.browser.evaluate("window.location.hash = '#/stale.txt'")
        self.waitFor(".tab", count=2, what="both tabs")
        classes = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.tab')).map("
            "  t => t.dataset.path + ':' + Array.from(t.classList)"
            "    .filter(c => c.startsWith('age-')).join('')).join(' ')"
        )
        self.assertIn("/fresh.txt:age-0", classes)
        self.assertIn("/stale.txt:age-6", classes)


class ManageBrowserTest(BrowserCase):
    writable = True

    def prepare(self):
        (self.root / "a.txt").write_text("hello\n")

    def test_saving_sends_the_stamp_and_a_stale_one_is_reported(self):
        self.loaded("/#/a.txt")
        self.browser.wait_for('document.querySelectorAll(".view-line").length > 0')
        self.assertEditorWritable(True)
        # Someone else writes the file after it was opened.
        (self.root / "a.txt").write_text("changed underneath\n")
        self.browser.evaluate(
            "(() => { const e = monaco.editor.getEditors()[0];"
            " e.setValue('mine\\n'); })()"
        )
        self.browser.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            " {key:'s', metaKey:true, bubbles:true}))"
        )
        self.waitFor("dialog[open]", what="the overwrite confirmation")
        self.assertIn("changed on disk", self.browser.text("dialog[open]"))
        self.waitFor("#reloadFile.is-stale")
        self.browser.click('dialog[open] button[type="button"]')
        self.assertEqual(self.editorText(), "mine")
        self.assertEqual((self.root / "a.txt").read_text(), "changed underneath\n")
        self.browser.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            " {key:'s', metaKey:true, bubbles:true}))"
        )
        self.waitFor("dialog[open]")
        self.browser.click('dialog[open] button[type="submit"]')
        self.browser.wait_for("document.querySelector('#flash').textContent === 'Saved a.txt'")
        self.assertEqual((self.root / "a.txt").read_text(), "mine\n")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        errors = [message for message in self.browser.errors() if "status of 409 (Conflict)" not in message]
        self.assertEqual(errors, [])


class WriteOperationsTest(BrowserCase):
    """Create, rename, delete and save, driven through the page.

    The endpoints are covered in test_server; this is the wiring — that the
    buttons call them, and that the tree and tabs follow afterwards."""

    writable = True

    def prepare(self):
        (self.root / "existing.txt").write_text("original\n")

    def loaded(self, *args, **kwargs):
        super().loaded(*args, **kwargs)
        self.browser.evaluate("""for (const name of ['alert', 'confirm', 'prompt']) {
          window[name] = () => { throw new Error('Native ' + name + ' used'); };
        }""")

    def name_dialog(self, title, value="", accept="Create"):
        self.assertTrue(self.browser.evaluate("!!document.querySelector('dialog.app-dialog[open]')"))
        self.assertEqual(self.browser.evaluate("""(() => {
          const dialog = document.querySelector('dialog[open]'), input = dialog.querySelector('input');
          return [dialog.querySelector('h2').textContent, input.value,
            document.activeElement === input, input.selectionStart, input.selectionEnd,
            dialog.querySelector('button[type=submit]').textContent];
        })()"""), [title, value, True, 0, len(value), accept])

    def submit_name(self, value, enter=False):
        self.browser._call("Input.insertText", {"text": value})
        if enter:
            for kind in ("keyDown", "keyUp"):
                event = {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}
                if kind == "keyDown":
                    event["text"] = "\r"
                self.browser._call("Input.dispatchKeyEvent", event)
        else:
            self.browser.click('dialog[open] button[type="submit"]')
        self.browser.wait_for("!document.querySelector('dialog[open]')")

    def file_action(self, path, label):
        self.browser.evaluate("document.querySelector(" + json.dumps('#tree .row[data-path="' + path + '"]') +
                              ").dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,clientX:80,clientY:160}))")
        self.browser.evaluate("Array.from(document.querySelectorAll('.menu-item')).find(button => button.textContent === " + json.dumps(label) + ").click()")

    def test_the_new_file_button_creates_and_opens_it(self):
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.click("#newFile")
        self.name_dialog("New file")
        self.submit_name("made.txt")
        self.waitFor('.tab[data-path="/made.txt"]', what="the new file's tab")
        self.assertTrue((self.root / "made.txt").is_file())
        self.assertNoPageErrors()

    def test_the_new_folder_button_creates_one(self):
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.click("#newFolder")
        self.name_dialog("New folder")
        self.submit_name("subdir", enter=True)
        self.waitFor('[data-path="/subdir"]', what="the new folder in the tree")
        self.assertTrue((self.root / "subdir").is_dir())
        self.assertNoPageErrors()

    def test_context_menu_creates_files_and_folders_with_app_dialogs(self):
        (self.root / "parent").mkdir()
        self.loaded("/")
        self.waitFor('#tree .row[data-path="/parent"]')
        for kind, name in (("file", "child.txt"), ("folder", "nested")):
            with self.subTest(kind=kind):
                self.file_action("/parent", "New " + kind.title())
                self.name_dialog("New " + kind)
                self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"))
                self.submit_name(name)
                self.waitFor('#tree .row[data-path="/parent/' + name + '"]')
                target = self.root / "parent" / name
                self.assertTrue(target.is_file() if kind == "file" else target.is_dir())
        self.assertNoPageErrors()

    def test_rename_dialog_selects_the_name_and_updates_the_open_file(self):
        self.loaded("/#/existing.txt")
        self.assertEditorWritable(True)
        self.file_action("/existing.txt", "Rename")
        self.name_dialog("Rename file", "existing.txt", "Rename")
        self.submit_name("renamed.txt", enter=True)
        self.waitFor('.tab[data-path="/renamed.txt"][aria-selected="true"]')
        self.assertEqual((self.root / "renamed.txt").read_text(), "original\n")
        self.assertFalse((self.root / "existing.txt").exists())
        self.assertNoPageErrors()

    def test_dismissing_name_dialogs_and_empty_names_do_not_write(self):
        self.loaded("/")
        self.waitFor('#tree .row[data-path="/existing.txt"]')
        self.browser.evaluate("""window.fileWrites = [];
          const fetch = window.fetch;
          window.fetch = (url, options) => {
            if (['/api/create', '/api/rename'].includes(String(url))) fileWrites.push(String(url));
            return fetch(url, options);
          };""")
        for action in ("file", "folder", "rename"):
            for dismissal in ("cancel", "escape", "backdrop", "empty"):
                with self.subTest(action=action, dismissal=dismissal):
                    if action == "rename":
                        self.file_action("/existing.txt", "Rename")
                        self.name_dialog("Rename file", "existing.txt", "Rename")
                    else:
                        button = "#newFile" if action == "file" else "#newFolder"
                        self.browser.evaluate("document.querySelector(" + json.dumps(button) + ").focus()")
                        self.browser.click(button)
                        self.name_dialog("New " + action)
                    if dismissal == "cancel":
                        self.browser.click('dialog[open] button[type="button"]')
                    elif dismissal == "escape":
                        for kind in ("keyDown", "keyUp"):
                            self.browser._call("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
                    elif dismissal == "backdrop":
                        for kind in ("mousePressed", "mouseReleased"):
                            self.browser._call("Input.dispatchMouseEvent", {"type": kind, "x": 1, "y": 1, "button": "left", "clickCount": 1})
                    else:
                        self.browser.evaluate("document.querySelector('dialog input').value = ''")
                        self.browser.click('dialog[open] button[type="submit"]')
                    self.browser.wait_for("!document.querySelector('dialog')")
                    if action != "rename":
                        self.assertEqual(self.browser.evaluate("document.activeElement.id"), button[1:])
        self.assertEqual(self.browser.evaluate("fileWrites"), [])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["existing.txt"])
        self.assertEqual((self.root / "existing.txt").read_text(), "original\n")
        self.assertNoPageErrors()

    def test_saving_writes_to_disk(self):
        self.loaded("/#/existing.txt")
        self.browser.wait_for('document.querySelectorAll(".view-line").length > 0')
        self.assertEditorWritable(True)
        self.browser.evaluate(
            "monaco.editor.getEditors()[0].getModel().setValue('rewritten\\n')"
        )
        self.browser.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            " {key:'s', metaKey:true, bubbles:true}))"
        )
        self.browser.wait_for(
            "!document.getElementById('flash').hidden", what="the save to report"
        )
        self.assertEqual((self.root / "existing.txt").read_text(), "rewritten\n")

    def test_partially_loaded_source_cannot_overwrite_the_complete_file(self):
        from unittest.mock import patch

        from ficelo import files

        original = "line\n" * (files.SNIFF_BYTES // 5 + 64) + "last line must survive\n"
        (self.root / "partial.txt").write_text(original)
        with patch.object(files, "MAX_TEXT_BYTES", files.SNIFF_BYTES):
            self.loaded("/#/partial.txt")
            self.assertEditorWritable(False)
            self.assertNotIn("last line must survive", self.browser.evaluate("monaco.editor.getEditors()[0].getModel().getValue()"))
            # Even a programmatic model change must not bypass the save guard.
            self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue('partial replacement')")
            self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'s',ctrlKey:true,bubbles:true}))")
            self.browser.wait_for("document.querySelector('#flash').textContent.includes('cannot be saved')")
            self.assertEqual((self.root / "partial.txt").read_text(), original)
            self.assertNoPageErrors()

    def test_a_dirty_tab_is_marked_and_clears_on_save(self):
        self.loaded("/#/existing.txt")
        self.browser.wait_for('document.querySelectorAll(".view-line").length > 0')
        self.assertEditorWritable(True)
        self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue('edited\\n')")
        self.waitFor(".tab.is-dirty", what="the unsaved marker")
        self.browser.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            " {key:'s', metaKey:true, bubbles:true}))"
        )
        self.browser.wait_for(
            "!document.querySelector('.tab').classList.contains('is-dirty')",
            what="the marker to clear",
        )


class DefaultWritableLaunchTest(BrowserCase):
    # None omits the Config argument, exercising the actual launch default.
    writable = None
    expected_writable = True

    def prepare(self):
        (self.root / "a.txt").write_text("hello\n")
        (self.root / "note.md").write_text("# Note\n\nA paragraph.\n")
        (self.root / "data.json").write_text('{"value": 1}\n')

    def test_plain_file_permissions_and_shortcuts_follow_launch_mode(self):
        self.loaded("/#/a.txt")
        self.assertEqual(self.browser.text("#mode"), "Read/Write" if self.expected_writable else "read-only")
        self.assertFalse(self.browser.evaluate("!!document.getElementById('editToggle')"))
        self.assertEditorWritable(self.expected_writable)
        for modifier in ("ctrlKey", "metaKey"):
            self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', " +
                                  json.dumps({"key": "e", modifier: True, "bubbles": True, "cancelable": True}) + "))")
            self.assertEditorWritable(self.expected_writable)
        self.browser.evaluate("""(() => { const editor = monaco.editor.getEditors()[0];
          editor.setPosition({lineNumber: 1, column: 1}); editor.focus(); })()""")
        self.browser._call("Input.insertText", {"text": "typed "})
        expected = "typed hello\n" if self.expected_writable else "hello\n"
        self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getModel().getValue()"), expected)
        if self.expected_writable:
            self.waitFor(".tab.is-dirty")
        self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'s',ctrlKey:true,bubbles:true}))")
        if self.expected_writable:
            self.browser.wait_for("!document.querySelector('.tab.is-dirty') && document.querySelector('#flash').textContent.startsWith('Saved ')")
        else:
            self.assertFalse(self.browser.evaluate("!!document.querySelector('.tab.is-dirty')"))
        self.assertEqual((self.root / "a.txt").read_text(), expected)
        self.assertNoPageErrors()

    def test_source_views_and_saved_choices_follow_launch_mode(self):
        for name in ("note.md", "data.json"):
            with self.subTest(name=name):
                self.loaded("/#/" + name)
                self.assertEqual(self.browser.text('#viewToggle [data-view="edit"]'), "Source")
                self.browser.click('#viewToggle [data-view="edit"]')
                self.browser.wait_for("!document.getElementById('editorHost').hidden")
                self.assertEditorWritable(self.expected_writable)
                self.loaded("/#/" + name)
                self.browser.wait_for("!document.getElementById('editorHost').hidden")
                self.assertEditorWritable(self.expected_writable)
                self.browser.click('#viewToggle [data-view="preview"]')
                self.browser.click('#viewToggle [data-view="edit"]')
                self.assertEditorWritable(self.expected_writable)
                if name.endswith(".md"):
                    self.browser.click('#viewToggle [data-view="split"]')
                    self.assertEditorWritable(self.expected_writable)
        self.assertNoPageErrors()


class ReadOnlyLaunchTest(DefaultWritableLaunchTest):
    writable = False
    expected_writable = False


class KeyboardTest(BrowserCase):
    writable = True

    def prepare(self):
        for name in ("one.txt", "two.txt", "three.txt"):
            (self.root / name).write_text(name + "\n")

    def press(self, key, **mods):
        parts = {"key": key, "bubbles": True}
        parts.update(mods)
        import json as _json

        self.browser.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown', %s))" % _json.dumps(parts)
        )

    def open_three(self):
        self.loaded("/")
        for name in ("one.txt", "two.txt", "three.txt"):
            self.browser.evaluate("window.location.hash = '#/%s'" % name)
            self.waitFor('.tab[data-path="/%s"]' % name, what=name)
        self.waitFor(".tab", count=3, what="three tabs")

    def tabPaths(self):
        """The tab strip only. Asserting against the whole page catches the
        tree, where every file in the folder is listed whether it is open or
        not — which is how both of these first failed."""
        return self.browser.evaluate(
            "Array.from(document.querySelectorAll('#tabs .tab'))"
            ".map(t => t.dataset.path).join(',')"
        )

    def active(self):
        return self.browser.evaluate(
            "(document.querySelector('.tab[aria-selected=\"true\"]')||{}).dataset"
            " && document.querySelector('.tab[aria-selected=\"true\"]').dataset.path"
        )

    def test_alt_arrows_walk_the_tabs(self):
        self.open_three()
        self.assertEqual(self.active(), "/three.txt")
        self.press("ArrowLeft", altKey=True)
        self.assertEqual(self.active(), "/two.txt")
        self.press("ArrowRight", altKey=True)
        self.assertEqual(self.active(), "/three.txt")

    def test_alt_arrows_wrap(self):
        self.open_three()
        self.press("ArrowRight", altKey=True)
        self.assertEqual(self.active(), "/one.txt")

    def test_alt_number_jumps(self):
        self.open_three()
        self.press("1", altKey=True)
        self.assertEqual(self.active(), "/one.txt")
        self.press("9", altKey=True)
        self.assertEqual(self.active(), "/three.txt", "9 means the last tab")

    def test_alt_w_closes_and_alt_shift_w_brings_it_back(self):
        self.open_three()
        self.press("w", altKey=True)
        self.waitFor(".tab", count=2, what="one fewer tab")
        self.assertNotIn("/three.txt", self.tabPaths())
        self.press("W", altKey=True, shiftKey=True)
        self.waitFor('.tab[data-path="/three.txt"]', what="the reopened tab")

    def test_alt_shift_arrow_moves_a_tab(self):
        self.open_three()
        order = lambda: self.browser.evaluate(
            "Array.from(document.querySelectorAll('.tab')).map(t => t.dataset.path).join(',')"
        )
        self.assertEqual(order(), "/one.txt,/two.txt,/three.txt")
        self.press("ArrowLeft", altKey=True, shiftKey=True)
        self.assertEqual(order(), "/one.txt,/three.txt,/two.txt")

    def test_ctrl_b_hides_the_tree(self):
        self.loaded("/")
        self.assertFalse(self.browser.evaluate("document.getElementById('sidebar').hidden"))
        self.press("b", ctrlKey=True)
        self.assertTrue(self.browser.evaluate("document.getElementById('sidebar').hidden"))

    def test_search_shortcuts_reveal_files_and_select_the_query(self):
        self.loaded("/")
        for modifier in ("ctrlKey", "metaKey"):
            for key in ("p", "k"):
                with self.subTest(modifier=modifier, key=key):
                    self.browser.click('#modeSwitch [data-mode="review"]')
                    self.browser.click("#sidebarToggle")
                    self.assertTrue(self.browser.evaluate("document.getElementById('sidebar').hidden"))
                    self.browser.evaluate("document.getElementById('search').value = 'existing query'")
                    self.press(key, **{modifier: True})
                    self.assertEqual(self.browser.evaluate("""(() => {
                      const input = document.getElementById('search');
                      return [document.getElementById('sidebar').hidden,
                        document.querySelector('#modeSwitch [data-mode="files"]').getAttribute('aria-pressed'),
                        document.getElementById('review').hidden, document.activeElement.id,
                        input.value.slice(input.selectionStart, input.selectionEnd)];
                    })()"""), [False, "true", True, "search", "existing query"])
        self.assertNoPageErrors()

    def test_alt_r_and_reload_button_preserve_dirty_confirmation(self):
        self.loaded("/")
        self.assertTrue(self.browser.evaluate("document.getElementById('reloadFile').hidden"))
        self.browser.evaluate("location.hash = '#/one.txt'")
        self.browser.wait_for("!document.getElementById('editorHost').hidden && monaco.editor.getEditors()[0].getModel()?.getValue() === 'one.txt\\n'",
                              what="the opened file to render")
        self.assertFalse(self.browser.evaluate("document.getElementById('reloadFile').hidden"))
        (self.root / "one.txt").write_text("changed underneath\n")
        self.press("r", altKey=True)
        self.browser.wait_for(
            "monaco.editor.getEditors()[0].getModel().getValue().includes('underneath')",
            what="the reloaded text",
        )
        self.assertEditorWritable(True)
        self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue('unsaved text')")
        self.waitFor(".tab.is-dirty")
        self.browser.click("#reloadFile")
        self.waitFor(".app-dialog", what="reload confirmation")
        self.browser.click('.app-dialog button[type="button"]')
        self.browser.wait_for("!document.querySelector('.app-dialog')")
        self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getModel().getValue()"), "unsaved text")
        self.press("r", altKey=True)
        self.waitFor(".app-dialog")
        self.browser.click('.app-dialog button[type="submit"]')
        self.browser.wait_for("!document.querySelector('.tab.is-dirty')", what="the confirmed reload")
        self.assertEqual(self.browser.evaluate("monaco.editor.getEditors()[0].getModel().getValue()"), "changed underneath\n")
        self.assertNoPageErrors()


class TabSlideTest(BrowserCase):
    """Closing a tab moves the others; that movement has to be visible.

    Measured mid-flight: the tab is asked where it is a few milliseconds
    after the close and again once the slide is over. Asserting only the
    resting position cannot tell a slide from a jump, since both end in the
    same place."""

    def prepare(self):
        for name in ("one.txt", "two.txt", "three.txt", "four.txt"):
            (self.root / name).write_text(name + "\n")

    def open_all(self):
        self.loaded("/")
        for name in ("one.txt", "two.txt", "three.txt", "four.txt"):
            self.browser.evaluate("window.location.hash = '#/%s'" % name)
            self.waitFor('.tab[data-path="/%s"]' % name, what=name)
        self.waitFor(".tab", count=4, what="four tabs")

    def close_by_clicking(self, path):
        """Click the close button the way a person would, so the pointer
        position is recorded and the slot logic runs."""
        self.browser.evaluate(
            "(() => { const t = document.querySelector('.tab[data-path=\"%s\"]');"
            " const b = t.querySelector('.close');"
            " const box = b.getBoundingClientRect();"
            " b.dispatchEvent(new MouseEvent('click',"
            "   {bubbles:true, detail:1, clientX: box.left + box.width/2, clientY: box.top + 5}));"
            " })()" % path
        )

    def watch_transforms(self):
        """Record every transform applied to a tab, as it is applied.

        Reading the transform after the fact is a race: waitFor polls every
        100ms and the slide lasts 130, so a tab that did move can be found
        already at rest. An observer cannot miss it."""
        self.browser.evaluate(
            "(() => { window.__slides = [];"
            " const seen = new MutationObserver((records) => {"
            "   for (const r of records) {"
            "     const t = r.target;"
            "     if (t.classList && t.classList.contains('tab') && t.style.transform) {"
            "       window.__slides.push(t.dataset.path + ' ' + t.style.transform);"
            "     } } });"
            " seen.observe(document.getElementById('tabs'),"
            "   { subtree: true, attributes: true, attributeFilter: ['style'] }); })()"
        )

    def slides_seen(self):
        return self.browser.evaluate("(window.__slides || []).join(' | ')")

    def transform_of(self, path):
        return self.browser.evaluate(
            "(() => { const t = document.querySelector('.tab[data-path=\"%s\"]');"
            " return t ? getComputedStyle(t).transform : 'gone'; })()" % path
        )

    def test_closing_the_rightmost_tab_slides_its_neighbour(self):
        """The case that prompted this: with no tab to the right, the one on
        the left has to travel to the pointer, and it used to arrive
        instantly under the mouse."""
        self.open_all()
        self.watch_transforms()
        self.close_by_clicking("/four.txt")
        moving = self.transform_of("/three.txt")
        if moving in ("none", "gone"):
            # It may simply have finished; the observer knows either way.
            self.assertIn("/three.txt", self.slides_seen(), "the neighbour jumped")
        else:
            self.assertIn("matrix", moving)

    def test_and_finishes_where_it_should(self):
        self.open_all()
        self.close_by_clicking("/four.txt")
        self.browser.wait_for(
            "(() => { const t = document.querySelector('.tab[data-path=\"/three.txt\"]');"
            " return t && getComputedStyle(t).transform === 'none'; })()",
            timeout=10,
            what="the slide to finish",
        )

    def test_closing_a_middle_tab_slides_the_ones_after_it(self):
        """The other direction: the tabs to the right move left into the
        gap, which is a layout change and animates only because the
        movement is measured and replayed."""
        self.open_all()
        self.watch_transforms()
        self.close_by_clicking("/two.txt")
        moving = self.transform_of("/three.txt")
        if moving in ("none", "gone"):
            self.assertIn("/three.txt", self.slides_seen(), "the following tabs jumped")

    def test_the_slide_travels_the_right_way(self):
        """A neighbour meeting the pointer starts to its left and arrives;
        the inverse transform must therefore be negative, not positive."""
        self.open_all()
        self.close_by_clicking("/four.txt")
        matrix = self.transform_of("/three.txt")
        # matrix(a, b, c, d, tx, ty) — the fifth number is the x offset.
        offset = float(matrix.strip("matrix()").split(",")[4])
        self.assertLess(offset, 0, "the tab starts to the right of where it ends: %s" % matrix)

    def test_nothing_moves_for_someone_who_asked_for_stillness(self):
        """prefers-reduced-motion is a real setting and animation is a real
        problem for some people."""
        self.open_all()
        self.browser.evaluate(
            "window.matchMedia = (q) => ({ matches: q.includes('reduced-motion'),"
            " addEventListener() {}, addListener() {} });"
        )
        self.close_by_clicking("/four.txt")
        self.assertEqual(self.transform_of("/three.txt"), "none")

    def test_a_newly_opened_tab_does_not_fly_in(self):
        """It has no previous position, so there is nothing to travel from
        and it should simply appear.

        Opened alongside others on purpose: with an empty strip the slide
        returns early anyway, so the first tab proves nothing about the
        case where one appears among tabs that are already there."""
        self.loaded("/")
        self.browser.evaluate("window.location.hash = '#/one.txt'")
        self.waitFor('.tab[data-path="/one.txt"]', what="the first tab")
        self.watch_transforms()
        self.browser.evaluate("window.location.hash = '#/two.txt'")
        self.waitFor('.tab[data-path="/two.txt"]', what="the second tab")
        self.assertNotIn("/two.txt", self.slides_seen())
        self.assertEqual(self.transform_of("/two.txt"), "none")


class TabClosePointerTest(BrowserCase):
    writable = True
    names = ("prefix_with_a_long_filename.txt", "a.py", "a_much_longer_filename_for_the_next_tab.py",
             "b.py", "another_long_filename_for_the_following_tab.py", "c.py")

    def prepare(self):
        for name in self.names:
            (self.root / name).write_text(name + "\n")

    def exercise(self, *, overflow=False, reduced=False):
        if overflow:
            self.browser._call("Emulation.setDeviceMetricsOverride", {
                "width": 700, "height": 700, "deviceScaleFactor": 1, "mobile": False,
            })
        if reduced:
            self.browser._call("Emulation.setEmulatedMedia", {
                "features": [{"name": "prefers-reduced-motion", "value": "reduce"}],
            })
        self.loaded()
        for name in self.names:
            self.browser.evaluate("location.hash=" + json.dumps("#/" + name))
            self.waitFor('.tab[data-path="/' + name + '"]')
        settled = "[...document.querySelectorAll('#tabs .tab')].every(t=>getComputedStyle(t).transform==='none')"
        self.browser.wait_for(settled)
        first, last = (3, 5) if overflow else (1, 4)
        selector = '.tab[data-path="/' + self.names[first] + '"] .close'
        self.browser.evaluate("document.querySelector(" + json.dumps(selector) + ").scrollIntoView({block:'nearest',inline:'center'})")
        point = self.browser.evaluate("""(() => {
          const box=document.querySelector(""" + json.dumps(selector) + """).getBoundingClientRect();
          const strip=document.querySelector('#tabs');
          return {x:box.left+box.width/2,y:box.top+box.height/2,scroll:strip.scrollLeft};
        })()""")
        if overflow:
            self.assertGreater(point["scroll"], 0)
        self.browser._call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": point["x"], "y": point["y"]})
        for index in range(first, last):
            for kind in ("mousePressed", "mouseReleased"):
                self.browser._call("Input.dispatchMouseEvent", {"type": kind, "x": point["x"], "y": point["y"],
                                                                 "button": "left", "clickCount": 1})
            closed = '.tab[data-path="/' + self.names[index] + '"]'
            self.browser.wait_for("!document.querySelector(" + json.dumps(closed) + ")")
            self.browser.wait_for(settled)
            expected = "/" + self.names[index + 1]
            next_selector = json.dumps('.tab[data-path="' + expected + '"] .close')
            state = self.browser.evaluate("""(() => {
              const node=document.querySelector(""" + next_selector + """);
              const box=node.getBoundingClientRect(), tabs=[...document.querySelectorAll('#tabs .tab')];
              return {x:box.left+box.width/2,scroll:document.querySelector('#tabs').scrollLeft,
                hit:document.elementFromPoint(""" + str(point["x"]) + "," + str(point["y"]) + """)?.closest('.tab')?.dataset.path,
                overlap:tabs.some((t,i)=>i && t.getBoundingClientRect().left<tabs[i-1].getBoundingClientRect().right-0.1)};
            })()""")
            self.assertAlmostEqual(state["x"], point["x"], delta=0.75, msg=str(state))
            self.assertEqual(state["hit"], expected)
            self.assertFalse(state["overlap"])
            if overflow:
                self.assertAlmostEqual(state["scroll"], point["scroll"], delta=0.75)
        self.browser._call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": point["x"], "y": point["y"] + 80})
        self.browser.wait_for(settled)
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('#tabs .tab')].every(t=>!t.style.width && !t.style.flex && !t.style.marginLeft)"))
        self.assertNoPageErrors()

    def test_varied_names_close_at_one_stationary_pointer(self):
        self.exercise()

    def test_scrolled_tabs_keep_the_pointer_target_without_scroll_clamping(self):
        self.exercise(overflow=True)

    def test_reduced_motion_keeps_the_same_close_target(self):
        self.exercise(reduced=True)

    def test_cancelled_and_keyboard_closes_do_not_hold_pointer_layout(self):
        def paths():
            return self.browser.evaluate("[...document.querySelectorAll('#tabs .tab')].map(t=>t.dataset.path)")
        self.loaded("/#/a.py")
        self.browser.evaluate("location.hash='#/b.py'")
        self.waitFor('.tab[data-path="/b.py"]')
        self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue('unsaved text')")
        self.waitFor('.tab.is-dirty[data-path="/b.py"]')
        point = self.browser.evaluate("""(() => {
          const box=document.querySelector('.tab[data-path="/b.py"] .close').getBoundingClientRect();
          return {x:box.left+box.width/2,y:box.top+box.height/2};
        })()""")
        self.browser._call("Input.dispatchMouseEvent", {"type": "mouseMoved", **point})
        for kind in ("mousePressed", "mouseReleased"):
            self.browser._call("Input.dispatchMouseEvent", {"type": kind, **point, "button": "left", "clickCount": 1})
        self.waitFor(".app-dialog[open]")
        for kind in ("keyDown", "keyUp"):
            self.browser._call("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        self.browser.wait_for("!document.querySelector('.app-dialog')")
        self.assertEqual(len(paths()), 2)
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('#tabs .tab')].every(t=>!t.style.width && !t.style.marginLeft)"))
        self.browser.evaluate("document.querySelector('.tab[data-path=\"/a.py\"] .close').focus()")
        for kind in ("keyDown", "keyUp"):
            event = {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}
            if kind == "keyDown":
                event.update(text="\r", unmodifiedText="\r")
            self.browser._call("Input.dispatchKeyEvent", event)
        self.browser.wait_for("!document.querySelector('.tab[data-path=\"/a.py\"]')")
        self.assertEqual(paths(), ["/b.py"])
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('#tabs .tab')].every(t=>!t.style.width && !t.style.marginLeft)"))
        self.assertNoPageErrors()


class SettingsOnServerTest(BrowserCase):
    """Settings are saved on the server, under the state folder."""

    def prepare(self):
        (self.root / "a.txt").write_text("a\n")
        (self.root / "b.txt").write_text("b\n")

    def settings_file(self):
        return self.server.config.state_dir / "settings.json"

    def wait_for_file(self, needle, timeout=10):
        import time

        end = time.time() + timeout
        while time.time() < end:
            if self.settings_file().exists() and needle in self.settings_file().read_text():
                return self.settings_file().read_text()
            time.sleep(0.1)
        self.fail("settings.json never contained %r" % needle)

    def test_opening_a_file_is_saved_to_the_state_folder(self):
        self.loaded("/#/a.txt")
        self.waitFor('.tab[data-path="/a.txt"]', what="the tab")
        self.wait_for_file("/a.txt")

    def test_settings_survive_a_change_of_hostname(self):
        """The reason this moved off localStorage. Browser storage is per
        origin, so the same server opened under a second name used to start
        from nothing — which happened, on a real host."""
        self.loaded("/#/a.txt")
        self.waitFor('.tab[data-path="/a.txt"]', what="the tab")
        self.browser.evaluate("window.location.hash = '#/b.txt'")
        self.waitFor('.tab[data-path="/b.txt"]', what="the second tab")
        self.wait_for_file("/b.txt")

        route = self.browser.evaluate("location.pathname + location.search + location.hash")
        # Same shared workspace link, with a different hostname.
        # The blank page has to finish loading first: a navigation started
        # while the previous one is still in flight hangs, which is the same
        # reason loaded() goes through about:blank and waits.
        self.browser.navigate("about:blank")
        self.browser.wait_for(
            "location.href === 'about:blank' && document.readyState === 'complete'",
            what="the page to be cleared",
        )
        self.browser.navigate(f"http://localhost:{self.port}{route}")
        self.browser.wait_for('document.body.dataset.ficeloReady === "1"', timeout=60)
        self.waitFor('.tab[data-path="/b.txt"]', what="the tabs, restored under the new name")
        self.assertIn('data-path="/a.txt"', self.browser.html())

    def test_an_appearance_change_is_saved(self):
        self.settings_file().write_text(json.dumps({"ficelo.appearance": {"theme": "auto"}}))
        self.browser._call("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": "dark"}],
        })
        self.loaded("/#/a.txt")
        self.assertEqual(self.browser.evaluate("document.documentElement.dataset.theme"), "light")
        self.browser.click("#settingsToggle")
        self.assertEqual(self.browser.evaluate(
            "[...document.querySelectorAll('#settings .set-choice button')].map(n=>n.textContent)"
        ), ["Light", "Dark"])
        self.browser.click("#settings .set-choice button:last-child")
        self.wait_for_file('"theme": "dark"')
        self.browser._call("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": "light"}],
        })
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertEqual(self.browser.evaluate("document.documentElement.dataset.theme"), "dark")
        self.loaded("/#/a.txt")
        self.assertEqual(self.browser.evaluate("document.documentElement.dataset.theme"), "dark")
        self.toggle_theme()
        self.assertEqual(self.browser.evaluate("document.documentElement.dataset.theme"), "light")
        self.toggle_theme()
        self.assertEqual(self.browser.evaluate("document.documentElement.dataset.theme"), "dark")
        self.browser.click("#settingsToggle")
        self.browser.click("#settings .set-reset")
        self.assertEqual(self.browser.evaluate("document.documentElement.dataset.theme"), "light")
        self.wait_for_file('"theme": "light"')
        self.assertNoPageErrors()

    def count_state_posts(self):
        """Wrap fetch so the page counts its own settings writes."""
        self.browser.evaluate(
            "(() => { window.__posts = []; const real = window.fetch;"
            " window.fetch = (url, opts) => {"
            "   if (String(url).includes('/api/state') && opts && opts.method === 'POST')"
            "     window.__posts.push([performance.now(), JSON.parse(opts.body).key, !!opts.keepalive]);"
            "   return real(url, opts); }; })()"
        )

    def test_a_single_change_is_sent_at_once(self):
        """Delaying a one-off change lets a reload straight after it read the
        old settings — and then save the old value back over the new one.
        Seen as a starred session losing its star under a loaded machine."""
        self.loaded("/")
        self.count_state_posts()
        sent_at = self.browser.evaluate(
            "(async () => { const m = await import('/js/util.js');"
            " const before = window.__posts.length;"
            " m.store.set('ficelo.probe', 1);"
            " return window.__posts.length - before; })()"
        )
        self.assertEqual(sent_at, 1, "the write was held back instead of sent")
        # And sent so that it survives the page navigating away: an ordinary
        # fetch is cancelled on unload, which lost settings changed just
        # before a reload.
        self.assertTrue(
            self.browser.evaluate("window.__posts[window.__posts.length - 1][2]"),
            "the write was not sent with keepalive",
        )

    def test_a_burst_of_changes_is_merged(self):
        """Scrolling or dragging a slider writes many times a second; only
        the last value matters, and fifty requests for it would be waste."""
        self.loaded("/")
        self.count_state_posts()
        self.browser.evaluate(
            "(async () => { const m = await import('/js/util.js');"
            " for (let i = 0; i < 50; i += 1) m.store.set('ficelo.burst', i); })()"
        )
        import time

        time.sleep(0.6)
        sent = self.browser.evaluate("window.__posts.filter(p => p[1] === 'ficelo.burst').length")
        self.assertLessEqual(sent, 3, "%d writes for one burst" % sent)
        self.wait_for_file('"ficelo.burst": 49')

    def test_the_last_change_before_closing_is_not_lost(self):
        """Writes are batched briefly; closing the page flushes them."""
        self.loaded("/#/a.txt")
        self.waitFor('.tab[data-path="/a.txt"]', what="the tab")
        self.browser.evaluate("window.location.hash = '#/b.txt'")
        self.waitFor('.tab[data-path="/b.txt"]', what="the second tab")
        # Navigate away at once, inside the write delay.
        self.browser.navigate("about:blank")
        self.wait_for_file("/b.txt")


class HomeStartTest(BrowserCase):
    """Served from a root that contains the home folder — the default, where
    the root is "/" — the page opens at home and calls it ~."""

    def prepare(self):
        from unittest import mock

        self.home = self.root / "users" / "someone"
        (self.home / "notes").mkdir(parents=True)
        (self.home / "notes" / "todo.md").write_text("# Todo\n")
        (self.root / "etc").mkdir()
        (self.root / "etc" / "hosts").write_text("127.0.0.1 localhost\n")
        patch = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_tree_starts_in_the_home_folder(self):
        self.loaded("/")
        self.waitFor('[data-path="/users/someone/notes"]', what="the home folder's contents")
        paths = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row')).map(r => r.dataset.path).join(',')"
        )
        self.assertNotIn("/etc", paths, "the tree started at the top of the disk")

    def crumbs(self):
        """The breadcrumb as it reads on screen, separators included."""
        return self.browser.evaluate(
            "Array.from(document.getElementById('crumbs').children).map(c => c.textContent).join(' ')"
        )

    def bold(self):
        return self.browser.evaluate(
            "Array.from(document.querySelectorAll('.crumb.is-root')).map(c => c.textContent).join(',')"
        )
    def test_with_nothing_open_the_breadcrumb_is_the_full_path_of_home(self):
        """Show the complete folder path instead of abbreviating home to ~."""
        self.loaded("/")
        self.waitFor(".crumb", count=3, what="breadcrumb")
        # Served from a temporary folder here, whose name leads the path in
        # the place "/" takes when the whole filesystem is served.
        self.assertEqual(self.crumbs(), "%s / users / someone" % self.root.name)
        self.assertEqual(self.bold(), "someone", "the folder the tree starts at is bold")
    def test_clicking_a_segment_starts_the_tree_there(self):
        self.loaded("/")
        self.waitFor(".crumb", count=3, what="breadcrumb")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.crumb')).find(c => c.textContent === 'users').click()"
        )
        self.browser.wait_for(
            "document.querySelector('.crumb.is-root') &&"
            " document.querySelector('.crumb.is-root').textContent === 'users'",
            what="the tree to start at users",
        )
        self.assertEqual(self.crumbs(), "%s / users" % self.root.name)
        self.waitFor('[data-path="/users/someone"]', what="users' children in the tree")
        paths = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row')).map(r => r.dataset.path).join(',')"
        )
        self.assertNotIn("/etc", paths, "a folder outside the new start is still listed")

    def test_the_root_segment_goes_back_to_the_top(self):
        self.loaded("/")
        self.waitFor(".crumb", count=3, what="breadcrumb")
        self.browser.evaluate("document.querySelector('.crumb').click()")
        self.waitFor('[data-path="/etc"]', what="the top of the disk in the tree")
        self.assertEqual(self.crumbs(), self.root.name)
    def test_an_open_file_extends_it_to_the_file_s_folder(self):
        """The tree's start stays bold; the folders below it recede."""
        self.loaded("/#/users/someone/notes/todo.md")
        self.waitFor(".crumb", count=4, what="breadcrumb")
        self.assertEqual(self.crumbs(), "%s / users / someone / notes" % self.root.name)
        self.assertEqual(self.bold(), "someone")
        self.assertEqual(
            self.browser.evaluate(
                "Array.from(document.querySelectorAll('.crumb.is-below')).map(c => c.textContent).join(',')"
            ),
            "notes",
        )

    def test_the_file_name_itself_is_not_in_the_breadcrumb(self):
        self.loaded("/#/users/someone/notes/todo.md")
        self.waitFor(".crumb", count=4, what="breadcrumb")
        self.assertNotIn("todo.md", self.crumbs())

    def test_there_is_no_tilde_button_in_the_explorer(self):
        dom, _ = self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.assertNotIn('id="explorerRoot"', dom)
    def test_tilde_in_the_sidebar_search_means_home(self):
        self.loaded("/")
        self.browser.evaluate(
            "(() => { const i = document.getElementById('search');"
            " i.value = '~/notes/todo.md';"
            " i.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()"
        )
        self.waitFor('.tab[data-path="/users/someone/notes/todo.md"]', what="the opened file")

    def test_the_rest_of_the_disk_is_still_reachable(self):
        """Starting at home is where the tree opens, not a boundary."""
        self.loaded("/#/etc/hosts")
        self.assertIn("localhost", self.editorText())


class NavigationTest(BrowserCase):
    def prepare(self):
        (self.root / "deep" / "deeper").mkdir(parents=True)
        (self.root / "deep" / "deeper" / "file.txt").write_text("hi\n")
        (self.root / "top.txt").write_text("top\n")

    def test_the_breadcrumb_shows_the_folder_of_the_open_file(self):
        self.loaded("/#/deep/deeper/file.txt")
        self.waitFor(".crumb", count=3, what="breadcrumb segments")
        crumbs = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.crumb')).map(c => c.textContent).join('|')"
        )
        self.assertTrue(crumbs.endswith("|deep|deeper"), crumbs)
    def test_clicking_a_breadcrumb_starts_the_tree_there(self):
        self.loaded("/#/deep/deeper/file.txt")
        self.waitFor(".crumb", count=3, what="breadcrumb segments")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.crumb'))"
            ".find(c => c.textContent === 'deep').click()"
        )
        self.browser.wait_for(
            "!!document.querySelector('[data-path=\"/deep/deeper\"]') &&"
            " !document.querySelector('[data-path=\"/top.txt\"]')",
            what="the tree redrawn from deep",
        )
        paths = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row')).map(r => r.dataset.path).join(',')"
        )
        self.assertNotIn("/top.txt", paths, "a sibling of the new start is still listed")
    def test_the_sidebar_search_opens_a_served_relative_file(self):
        self.loaded("/")
        self.browser.evaluate(
            "(() => { const i = document.getElementById('search');"
            " i.value = 'deep/deeper/file.txt';"
            " i.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()"
        )
        self.waitFor('.tab[data-path="/deep/deeper/file.txt"]', what="the opened file")

    def test_the_sidebar_search_accepts_an_absolute_path_inside_the_root(self):
        self.loaded("/")
        self.browser.evaluate(
            "(() => { const i = document.getElementById('search');"
            " i.value = %s + '/top.txt';"
            " i.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()"
            % __import__("json").dumps(str(self.root))
        )
        self.waitFor('.tab[data-path="/top.txt"]', what="the opened file")

    def test_the_tree_menu_offers_the_expected_actions(self):
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.evaluate(
            "(() => { const r = document.querySelector('[data-path=\"/top.txt\"]');"
            " const box = r.getBoundingClientRect();"
            " r.dispatchEvent(new MouseEvent('contextmenu',"
            "   {bubbles:true, clientX: box.left + 5, clientY: box.top + 5})); })()"
        )
        self.waitFor(".menu", what="the context menu")
        labels = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.menu-item')).map(i => i.textContent).join('|')"
        )
        self.assertEqual(labels, "Copy Full Path|Copy File Name|Info")

    def test_info_reports_the_file_facts(self):
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.evaluate(
            "(() => { const r = document.querySelector('[data-path=\"/top.txt\"]');"
            " const box = r.getBoundingClientRect();"
            " r.dispatchEvent(new MouseEvent('contextmenu',"
            "   {bubbles:true, clientX: box.left + 5, clientY: box.top + 5})); })()"
        )
        self.waitFor(".menu-item", what="the context menu")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.menu-item'))"
            ".find(i => i.textContent === 'Info').click()"
        )
        card = self.waitFor(".menu-info", what="the info card")
        text = self.browser.text(".menu-info")
        self.assertIn("top.txt", text)
        self.assertIn("B", text, "a size")
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2}", "a modification date")
        self.assertIn("rw", text, "a mode")

    def test_the_tree_can_be_rooted_at_a_folder(self):
        """Serving a whole filesystem is a real configuration; the tree has
        to be able to start somewhere useful."""
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.evaluate(
            "(() => { const r = document.querySelector('[data-path=\"/deep\"]');"
            " const box = r.getBoundingClientRect();"
            " r.dispatchEvent(new MouseEvent('contextmenu',"
            "   {bubbles:true, clientX: box.left + 5, clientY: box.top + 5})); })()"
        )
        self.waitFor(".menu-item", what="the context menu")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.menu-item'))"
            ".find(i => i.textContent === 'Set as Explorer Root').click()"
        )
        self.browser.wait_for(
            "!!document.querySelector('[data-path=\"/deep/deeper\"]') &&"
            " !document.querySelector('[data-path=\"/top.txt\"]')",
            what="the tree redrawn from deep",
        )
        paths = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row')).map(r => r.dataset.path).join(',')"
        )
        self.assertNotIn("/top.txt", paths, "a sibling of the new root is still listed")
        self.assertIn("/deep/deeper", paths)

    def test_the_tree_comes_back_where_it_was_left(self):
        """Resetting to Home on every reload means re-navigating every
        morning, which is the thing a remembered session is meant to spare
        you."""
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.evaluate(
            "(() => { const r = document.querySelector('[data-path=\"/deep\"]');"
            " const box = r.getBoundingClientRect();"
            " r.dispatchEvent(new MouseEvent('contextmenu',"
            "   {bubbles:true, clientX: box.left + 5, clientY: box.top + 5})); })()"
        )
        self.waitFor(".menu-item", what="the context menu")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.menu-item'))"
            ".find(i => i.textContent === 'Set as Explorer Root').click()"
        )
        self.browser.wait_for(
            "!!document.querySelector('[data-path=\"/deep/deeper\"]') &&"
            " !document.querySelector('[data-path=\"/top.txt\"]')",
            what="the tree redrawn from deep",
        )
        self.loaded("/")
        self.browser.wait_for(
            "!!document.querySelector('[data-path=\"/deep/deeper\"]') &&"
            " !document.querySelector('[data-path=\"/top.txt\"]')",
            what="the tree to still start where it was left",
        )
        paths = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row')).map(r => r.dataset.path).join(',')"
        )
        self.assertNotIn("/top.txt", paths)

    def test_the_root_segment_puts_it_back(self):
        """With the ~/ button gone, the "/" at the start of the breadcrumb is
        the way back to the top."""
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.evaluate("window.location.hash = '#/deep/deeper/file.txt'")
        self.waitFor('.tab[data-path="/deep/deeper/file.txt"]', what="the file")
        self.browser.click("#settingsToggle")
        self.browser.evaluate("document.getElementById('homePath').value = '/deep/deeper'")
        self.browser.click("#settingsToggle")
        self.browser.wait_for("(async () => (await import('/js/util.js')).store.get('ficelo.home') === '/deep/deeper')()")
        self.browser.click("#homeButton")
        self.browser.wait_for(
            "document.querySelector('.crumb.is-root') &&"
            " document.querySelector('.crumb.is-root').textContent === 'deeper'",
            what="deeper marked as the start",
        )
        self.browser.evaluate("document.querySelector('.crumb').click()")
        # A prior tree draw may still be visible while the new root is
        # validated. Wait for both the root marker and its listing.
        self.browser.wait_for(
            "document.querySelector('.crumb')?.classList.contains('is-root') &&"
            " !!document.querySelector('#tree [data-path=\"/top.txt\"]')",
            what="the root marker and tree back at the top",
        )
        self.assertTrue(
            self.browser.evaluate("document.querySelector('.crumb').classList.contains('is-root')"),
            "the leading segment should now be the bold one",
        )
        paths = self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row')).map(r => r.dataset.path).join(',')"
        )
        self.assertIn("/top.txt", paths)

    def test_no_panel_makes_the_page_scroll_sideways(self):
        """Search results must fit the sidebar and viewport without adding
        a horizontal scrollbar to the page or the result list."""
        self.loaded("/")
        self.waitFor(".row", what="the tree")
        self.browser.evaluate(
            "(() => { const s = document.getElementById('search');"
            " s.value = 'txt'; s.dispatchEvent(new Event('input')); })()"
        )
        self.waitFor(".result", what="search results")
        overflow = self.browser.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        self.assertLessEqual(overflow, 0, "the page scrolls sideways by %dpx" % overflow)
        box = self.browser.evaluate(
            "JSON.stringify(document.getElementById('results').getBoundingClientRect())"
        )
        import json as _json

        right = _json.loads(box)["right"]
        width = self.browser.evaluate("window.innerWidth")
        self.assertLessEqual(right, width + 1, "the results panel runs past the window edge")
        inner = self.browser.evaluate(
            "(() => { const r = document.getElementById('results');"
            " return r.scrollWidth - r.clientWidth; })()"
        )
        self.assertLessEqual(inner, 0, "the results panel scrolls sideways by %dpx" % inner)

    def test_the_explorer_buttons_are_present(self):
        dom, _ = self.loaded("/")
        for ident in ("newFile", "newFolder"):
            self.assertIn('id="%s"' % ident, dom)
        self.assertNotIn('id="refreshTree"', dom)
        self.assertNotIn('class="explorer-title"', dom)


class SessionTest(BrowserCase):
    writable = True

    def prepare(self):
        for name in ("alpha.txt", "beta.txt", "gamma.txt"):
            (self.root / name).write_text(name + "\n")

    def flush_preferences(self):
        self.browser.evaluate("(async () => (await import('/js/util.js')).flushStore())()")

    def test_session_activity_labels_and_opening_or_changing_a_session(self):
        now = int(time.time() * 1000)
        for ident, age in (("here", 600000), ("away", 120000)):
            self.server.settings.put("ficelo.workspace." + ident, {
                "id": ident, "name": ident, "tabs": {}, "order": [], "root": "/", "at": now - age,
            })
        self.loaded("/?session=here")
        self.flush_preferences()
        self.assertGreaterEqual(self.server.settings.read()["ficelo.workspace.here"]["at"], now)
        self.browser.click("#sessionsButton")
        self.browser.wait_for("!document.querySelector('#sessionList').hidden")
        self.assertEqual(self.browser.text(".session-activity"), "2 min ago")
        self.assertTrue(self.browser.evaluate("document.querySelector('.session-activity').previousElementSibling.classList.contains('session-star')"))
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.session.is-current .session-activity')"))

        # The visible label ages without rebuilding the menu or losing focus.
        self.browser.evaluate("window.actualNow = Date.now; Date.now = () => actualNow() + 60000")
        self.browser.wait_for("document.querySelector('.session-activity').textContent === '3 min ago'")
        self.browser.evaluate("Date.now = actualNow")
        self.browser.click('[aria-label="Star away"]')
        self.assertEqual(self.browser.text(".session-activity"), "less than 1 min ago")
        self.flush_preferences()
        before = self.server.settings.read()["ficelo.workspace.away"]["at"]
        self.browser.click('.session:not(.is-current) .session-open')
        self.browser.wait_for("new URL(location).searchParams.get('session') === 'away'")
        self.flush_preferences()
        self.assertGreater(self.server.settings.read()["ficelo.workspace.away"]["at"], before)
        self.assertNoPageErrors()

    def test_returning_to_the_browser_tab_records_activity_without_changing_contents(self):
        self.loaded("/")
        self.flush_preferences()
        key = "ficelo.workspace." + self.browser.evaluate("new URL(location).searchParams.get('session')")
        before = self.server.settings.read()[key]
        other = self.browser._call("Target.createTarget", {"url": "about:blank"})["targetId"]
        try:
            self.browser._call("Target.activateTarget", {"targetId": other})
            self.browser.wait_for("document.hidden", what="the session browser tab to be hidden")
            self.flush_preferences()
            self.assertEqual(self.server.settings.read()[key], before, "hiding a browser tab counted as viewing it")
            self.browser._call("Page.bringToFront")
            self.browser.wait_for("!document.hidden", what="the session browser tab to be visible")
            self.flush_preferences()
            after = self.server.settings.read()[key]
            self.assertGreater(after["at"], before["at"])
            self.assertEqual({**after, "at": before["at"]}, before)
        finally:
            self.browser._call("Target.closeTarget", {"targetId": other})
        self.assertNoPageErrors()

    def test_session_ages_leave_names_and_actions_visible_in_narrow_windows(self):
        for ident in ("here", "away"):
            self.server.settings.put("ficelo.workspace." + ident, {
                "id": ident, "name": "A session with a descriptive name", "tabs": {}, "order": [], "root": "/",
            })
        for width in (480, 360):
            self.browser._call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": 800, "deviceScaleFactor": 1, "mobile": False,
            })
            self.loaded("/?session=here")
            self.browser.click("#sessionsButton")
            self.browser.wait_for("!document.querySelector('#sessionList').hidden")
            geometry = self.browser.evaluate("""(() => {
              const popup = document.querySelector('#sessionList').getBoundingClientRect();
              const row = document.querySelector('.session:not(.is-current)');
              return {left:popup.left, right:popup.right, viewport:document.documentElement.clientWidth,
                name:row.querySelector('.session-name').getBoundingClientRect().width,
                buttons:[...row.querySelectorAll('.session-icon')].every(button => {
                  const box = button.getBoundingClientRect();
                  return box.width >= 28 && box.left >= popup.left && box.right <= popup.right;
                })};
            })()""")
            self.assertGreaterEqual(geometry["left"], 8, geometry)
            self.assertLessEqual(geometry["right"], geometry["viewport"] - 8, geometry)
            self.assertGreaterEqual(geometry["name"], 80, geometry)
            self.assertTrue(geometry["buttons"], geometry)
        self.assertNoPageErrors()

    def test_editing_a_file_updates_activity_without_saving_its_contents(self):
        self.loaded("/#/alpha.txt")
        self.flush_preferences()
        key = "ficelo.workspace." + self.browser.evaluate("new URL(location).searchParams.get('session')")
        before = self.server.settings.read()[key]
        self.browser.evaluate("monaco.editor.getEditors()[0].getModel().setValue('Unsaved text')")
        self.browser.wait_for("(async () => (await import('/js/sessions.js')).readAll()[0].at > " + str(before["at"]) + ")()")
        self.flush_preferences()
        after = self.server.settings.read()[key]
        self.assertGreater(after["at"], before["at"])
        self.assertEqual({**after, "at": before["at"]}, before)
        self.assertEqual((self.root / "alpha.txt").read_text(), "alpha.txt\n")
        self.assertNoPageErrors()

    def test_recently_deleted_shows_age_name_tab_count_and_restores_the_session(self):
        now = int(time.time() * 1000)
        deleted = [
            {"id": "named", "name": "Notes", "paths": ["/alpha.txt"], "deletedAt": now - 120000},
            {"id": "empty", "paths": [], "deletedAt": now - 7200000},
            {"id": "files", "paths": ["/beta.txt", "/gamma.txt"], "deletedAt": now - 172800000},
        ]
        for record in deleted:
            self.server.settings.put("ficelo.workspace." + record["id"], {**record, "at": 1, "root": "/"})
        self.loaded("/")
        self.browser.click("#sessionsButton")
        self.browser.wait_for("!document.querySelector('#sessionList').hidden")
        self.browser.click(".session-trash summary")
        rows = self.browser.evaluate("""[...document.querySelectorAll('.session-deleted')].map(row => [
          row.querySelector('.session-deleted-age').textContent,
          row.querySelector('.session-name').textContent,
          row.querySelector('.session-count').textContent,
          row.querySelector('button').textContent])""")
        self.assertEqual(rows, [
            ["2 min ago", "Notes", "1 tab", "Restore session"],
            ["2 h ago", "Empty session", "0 tabs", "Restore session"],
            ["2 day ago", "beta.txt, gamma.txt", "2 tabs", "Restore session"],
        ])
        self.browser.evaluate("window.actualNow = Date.now; Date.now = () => actualNow() + 60000")
        self.browser.wait_for("document.querySelector('.session-deleted-age').textContent === '3 min ago'")
        self.browser.evaluate("Date.now = actualNow")
        self.browser._call("Emulation.setDeviceMetricsOverride", {
            "width": 360, "height": 800, "deviceScaleFactor": 1, "mobile": False,
        })
        self.browser.wait_for("innerWidth === 360")
        self.assertTrue(self.browser.evaluate("""[...document.querySelectorAll('.session-deleted')].every(row => {
          const bounds = row.getBoundingClientRect();
          return row.querySelector('.session-name').getBoundingClientRect().width >= 80 &&
            [...row.children].every(child => {
              const box = child.getBoundingClientRect();
              return box.left >= bounds.left && box.right <= bounds.right;
            });
        })"""))
        self.browser.click('[aria-label="Restore session Notes"]')
        self.flush_preferences()
        self.assertFalse(self.browser.evaluate("document.querySelector('#sessionList').hidden"))
        self.assertTrue(self.browser.evaluate("document.querySelector('.session-trash').open"))
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.session-deleted').length"), 2)
        self.waitFor('[aria-label="Delete Notes"]')
        restored = self.server.settings.read()["ficelo.workspace.named"]
        self.assertNotIn("deletedAt", restored)
        self.assertEqual(restored["name"], "Notes")
        self.assertEqual(restored["paths"], ["/alpha.txt"])
        self.assertNoPageErrors()

    def test_session_row_actions_keep_the_menu_open_until_selection_or_outside_click(self):
        for ident, name in (("keep", "Current"), ("other", "Other")):
            self.server.settings.put("ficelo.workspace." + ident, {
                "id": ident, "name": name, "paths": [], "root": "/",
            })
        self.loaded("/?session=keep")
        self.browser.click("#sessionsButton")
        self.browser.wait_for("!document.querySelector('#sessionList').hidden")

        self.browser.click('#sessionList [aria-label="Delete Other"]')
        self.assertFalse(self.browser.evaluate("document.querySelector('#sessionList').hidden"))
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#sessionList [aria-label=\"Delete Other\"]')"))

        self.browser.click('#sessionList [aria-label="Star Current"]')
        self.assertFalse(self.browser.evaluate("document.querySelector('#sessionList').hidden"))
        self.waitFor('#sessionList [aria-label="Unstar Current"]')

        self.browser.click("#sessionList .session-trash summary")
        self.browser.click("#sessionList .session-trash button")
        self.assertFalse(self.browser.evaluate("document.querySelector('#sessionList').hidden"))
        self.waitFor('#sessionList [aria-label="Delete Other"]')

        self.browser.click("#sidebarToggle")
        self.assertTrue(self.browser.evaluate("document.querySelector('#sessionList').hidden"))
        self.browser.click("#sessionsButton")
        self.browser.wait_for("!document.querySelector('#sessionList').hidden")
        self.browser.evaluate("""[...document.querySelectorAll('#sessionList .session')]
          .find(row => row.querySelector('.session-name').textContent === 'Other')
          .querySelector('.session-open').click()""")
        self.browser.wait_for("new URL(location.href).searchParams.get('session') === 'other'")
        self.assertTrue(self.browser.evaluate("document.querySelector('#sessionList').hidden"))
        self.assertNoPageErrors()

    def test_unstarring_at_the_session_limit_does_not_refresh_unrelated_sessions(self):
        for index in range(50):
            ident = "u" + str(index)
            self.server.settings.put("ficelo.workspace." + ident, {
                "id": ident, "name": ident, "tabs": {}, "order": [], "root": "/", "at": index + 1,
            })
        self.server.settings.put("ficelo.workspace.star", {
            "id": "star", "name": "Saved", "starred": True, "tabs": {}, "order": [], "root": "/", "at": 1,
        })
        self.loaded("/?session=u49")
        self.browser.click("#sessionsButton")
        self.browser.wait_for("!document.querySelector('#sessionList').hidden")
        self.browser.click('[aria-label="Unstar Saved"]')
        self.flush_preferences()
        records = self.server.settings.read()
        self.assertIn("deletedAt", records["ficelo.workspace.u0"])
        self.assertNotIn("deletedAt", records["ficelo.workspace.star"])
        self.assertFalse(records["ficelo.workspace.star"]["starred"])
        for index in range(1, 49):
            record = records["ficelo.workspace.u" + str(index)]
            self.assertEqual(record["at"], index + 1)
            self.assertNotIn("deletedAt", record)
        self.assertNoPageErrors()

    def test_long_session_links_still_open_and_copies_use_short_ids(self):
        ident = "12345678-1234-1234-1234-123456789abc"
        self.server.settings.put("ficelo.workspace." + ident, {
            "id": ident, "name": "Saved session", "paths": ["/alpha.txt"], "root": "/",
        })
        self.loaded("/?session=" + ident)
        self.waitFor('.tab[data-path="/alpha.txt"]')
        current = "new URL(location.href).searchParams.get('session')"
        self.assertEqual(self.browser.evaluate(current), ident)
        self.loaded("/?copy=" + ident)
        self.waitFor('.tab[data-path="/alpha.txt"]')
        self.assertRegex(self.browser.evaluate(current), r"^[a-z0-9]{4}$")
        self.flush_preferences()
        self.assertIn("ficelo.workspace." + ident, self.server.settings.read())
        self.assertNoPageErrors()

    def test_shared_links_merge_independent_changes_and_open_only_updates_activity(self):
        self.loaded("/#/alpha.txt")
        self.flush_preferences()
        initial = self.server.settings.read()
        route = self.browser.evaluate("location.pathname + location.search + location.hash")
        first = self.browser
        second = Browser()
        self.addCleanup(second.close)
        try:
            self.browser = second
            self.loaded(route)
            self.flush_preferences()
            opened = self.server.settings.read()
            key = next(key for key in initial if key.startswith("ficelo.workspace."))
            self.assertGreater(opened[key]["at"], initial[key]["at"])
            opened[key]["at"] = initial[key]["at"]
            self.assertEqual(opened, initial, "opening rewrote more than session activity")
            self.browser = first
            self.browser.evaluate("location.hash = '#/beta.txt'")
            self.waitFor('.tab[data-path="/beta.txt"]')
            self.flush_preferences()
            self.browser = second
            self.browser.evaluate("location.hash = '#/gamma.txt'")
            self.waitFor('.tab[data-path="/gamma.txt"]')
            self.flush_preferences()
            records = [value for key, value in self.server.settings.read().items() if key.startswith("ficelo.workspace.")]
            self.assertEqual(len(records), 1)
            self.assertEqual(set(records[0]["tabs"]), {"/alpha.txt", "/beta.txt", "/gamma.txt"})
        finally:
            self.browser = first

    def test_stale_scroll_cannot_resurrect_a_closed_shared_tab(self):
        (self.root / "alpha.txt").write_text("line\n" * 400)
        self.loaded("/#/alpha.txt")
        self.flush_preferences()
        # Reload the shared session without explicitly reopening its former file.
        route = self.browser.evaluate("(() => { const url = new URL(location); url.searchParams.delete('path'); return url.pathname + url.search; })()")
        first = self.browser
        second = Browser()
        self.addCleanup(second.close)
        try:
            self.browser = second
            self.loaded(route)
            self.flush_preferences()
            first.click("#tabs .tab .close")
            self.browser = first
            self.flush_preferences()
            second.evaluate("monaco.editor.getEditors()[0].setScrollTop(300); window.dispatchEvent(new Event('pagehide'))")
            self.browser = second
            self.flush_preferences()
            self.browser = first
            self.loaded(route)
            self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 0)
        finally:
            self.browser = first

    def test_legacy_copy_merge_and_picker_refresh_keep_old_state(self):
        legacy = [{"id": "old", "name": "Old", "paths": ["/alpha.txt"], "active": "/alpha.txt"},
                  {"id": "other", "name": "Other", "paths": ["/beta.txt"], "active": "/beta.txt"}]
        self.server.settings.put("ficelo.sessions", legacy)
        self.server.settings.put("ficelo.scroll", {"/alpha.txt": {"top": 123}})
        self.loaded("/?copy=old")
        self.assertEqual(self.browser.evaluate("(async () => (await import('/js/settings.js')).readScroll('/alpha.txt'))()"), {"top": 123})
        self.flush_preferences()
        self.server.settings.put("ficelo.workspace.remote", {"id": "remote", "name": "Remote", "tabs": {}, "order": []})
        self.browser.click("#sessionsButton")
        self.browser.wait_for("document.querySelector('#sessionList').textContent.includes('Remote')")
        self.browser.evaluate("Array.from(document.querySelectorAll('.session')).filter(row => ['Old','Other'].includes(row.querySelector('.session-name').textContent)).forEach(row => row.querySelector('input').click())")
        self.browser.click(".session-tools button")
        self.waitFor(".app-dialog")
        self.browser.click(".app-dialog button[type=submit]")
        self.browser.wait_for("!document.querySelector('.app-dialog')")
        self.flush_preferences()
        self.browser.wait_for("(async () => Object.values((await (await fetch('/api/state')).json()).settings).some(s => s && s.redirect))()")
        records = self.server.settings.read()
        merged = next(record for key, record in records.items() if key.startswith("ficelo.workspace.") and set(record.get("tabs", {})) == {"/alpha.txt", "/beta.txt"})
        self.assertEqual(merged["state"]["ficelo.scroll"]["/alpha.txt"], {"top": 123})

    def test_many_offline_updates_drain_after_reconnecting(self):
        self.loaded("/")
        result = self.browser.evaluate("""(async () => {
          const u = await import('/js/util.js'); await u.flushStore();
          const original = window.fetch;
          window.fetch = (input, init) => String(input) === '/api/state' && init?.method === 'POST'
            ? Promise.reject(new Error('offline test')) : original(input, init);
          for (let i = 0; i < 2002; i++) u.store.set('ficelo.offline', {counter:i});
          await u.flushStore(); window.fetch = original; await u.flushStore();
          return JSON.parse(sessionStorage.getItem('ficelo.pending'))['ficelo.offline'] || [];
        })()""")
        self.assertEqual(result, [])
        self.assertEqual(self.server.settings.read()["ficelo.offline"], {"counter": 2001})

    def test_restore_keeps_missing_review_tabs_and_saved_order(self):
        record = {"id": "saved", "name": "Mixed", "tabs": {
            "/alpha.txt": {"id": "/alpha.txt", "path": "/alpha.txt"},
            "/missing.txt": {"id": "/missing.txt", "path": "/missing.txt"},
            "diff:work:/alpha.txt": {"id": "diff:work:/alpha.txt", "kind": "diff", "path": "/alpha.txt", "rev": None},
        }, "order": ["/missing.txt", "diff:work:/alpha.txt", "/alpha.txt"], "active": "/missing.txt"}
        self.server.settings.put("ficelo.workspace.saved", record)
        self.loaded("/?session=saved")
        self.assertEqual(self.browser.evaluate("Array.from(document.querySelectorAll('#tabs .tab')).map(t => t.dataset.id)"), record["order"])
        self.assertIn("no such file", self.browser.text("#notice"))
        self.flush_preferences()
        opened = self.server.settings.read()["ficelo.workspace.saved"]
        self.assertGreater(opened.pop("at"), 0)
        self.assertEqual(opened, record)

    def test_option_shortcuts_use_physical_keys_and_preserve_text_navigation(self):
        self.loaded("/#/alpha.txt")
        self.browser.evaluate("location.hash = '#/beta.txt'")
        self.waitFor('.tab[data-path="/beta.txt"]')
        self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {code:'Digit1',key:'¡',altKey:true,bubbles:true}))")
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs [aria-selected]').dataset.path"), "/alpha.txt")
        self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {code:'KeyW',key:'∑',altKey:true,bubbles:true}))")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 1)
        self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {code:'KeyW',key:'∑',altKey:true,shiftKey:true,bubbles:true}))")
        self.waitFor('#tabs .tab', 2)
        self.browser.evaluate("document.getElementById('search').dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowLeft',altKey:true,bubbles:true}))")
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs [aria-selected]').dataset.path"), "/alpha.txt")

    def test_a_new_session_clears_the_tabs_and_keeps_the_old_one(self):
        self.loaded("/")
        self.assertRegex(self.browser.evaluate("new URL(location.href).searchParams.get('session')"), r"^[a-z0-9]{4}$")
        self.browser.evaluate("window.location.hash = '#/alpha.txt'")
        self.waitFor('.tab[data-path="/alpha.txt"]', what="the first tab")
        self.browser.evaluate(
            "(() => { const n = document.getElementById('sessionName');"
            " n.value = 'First piece'; n.dispatchEvent(new Event('change')); })()"
        )
        self.browser.click("#newSession")
        self.browser.click(".app-dialog button[type=submit]")
        self.browser.wait_for(
            "document.querySelectorAll('.tab').length === 0", what="an empty tab strip"
        )
        self.assertRegex(self.browser.evaluate("new URL(location.href).searchParams.get('session')"), r"^[a-z0-9]{4}$")
        self.browser.click("#sessionsButton")
        dom = self.waitFor(".session", what="the session list")
        self.assertIn("First piece", dom)

    def test_switching_back_restores_the_files(self):
        self.loaded("/")
        self.browser.evaluate("window.location.hash = '#/alpha.txt'")
        self.waitFor('.tab[data-path="/alpha.txt"]', what="the first tab")
        self.browser.evaluate(
            "(() => { const n = document.getElementById('sessionName');"
            " n.value = 'Has alpha'; n.dispatchEvent(new Event('change')); })()"
        )
        self.browser.click("#newSession")
        self.browser.click(".app-dialog button[type=submit]")
        self.browser.wait_for("document.querySelectorAll('.tab').length === 0")
        self.browser.evaluate("window.location.hash = '#/beta.txt'")
        self.waitFor('.tab[data-path="/beta.txt"]', what="the second session's tab")

        self.browser.click("#sessionsButton")
        self.waitFor(".session", what="the session list")
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.session'))"
            ".find(s => s.textContent.includes('Has alpha'))"
            ".querySelector('.session-open').click()"
        )
        self.waitFor('.tab[data-path="/alpha.txt"]', what="the restored tab")
        open_now = self.browser.evaluate(
            "Array.from(document.querySelectorAll('#tabs .tab'))"
            ".map(t => t.dataset.path).join(',')"
        )
        self.assertNotIn("/beta.txt", open_now, "the other session's file came too")

    def test_starring_survives_and_sorts_to_the_top(self):
        self.loaded("/")
        self.browser.evaluate("window.location.hash = '#/alpha.txt'")
        self.waitFor('.tab[data-path="/alpha.txt"]', what="a tab")
        self.browser.click("#starSession")
        self.browser.wait_for(
            "document.getElementById('starSession').textContent === '★'", what="the star to light"
        )
        self.loaded("/")
        self.browser.wait_for(
            "document.getElementById('starSession').textContent === '★'",
            what="the star to survive a reload",
        )

    def test_the_session_name_reaches_the_page_title(self):
        self.loaded("/")
        self.browser.evaluate(
            "(() => { const n = document.getElementById('sessionName');"
            " n.value = 'Reading week'; n.dispatchEvent(new Event('change')); })()"
        )
        self.browser.wait_for(
            "document.title === 'Reading week'", what="the title to contain only the name"
        )

    def test_session_name_width_fits_text_and_caps_at_twenty_characters(self):
        self.loaded("/")
        width = "document.querySelector('#sessionName').getBoundingClientRect().width"
        default = self.browser.evaluate(width)

        def type_name(value):
            self.browser.evaluate("""(() => {
              const input = document.querySelector('#sessionName');
              input.value = """ + json.dumps(value) + """;
              input.dispatchEvent(new Event('input'));
            })()""")
            return self.browser.evaluate(width)

        short = type_name("a")
        self.assertLess(short, default)
        self.assertGreater(type_name("Reading week"), short)
        self.assertTrue(self.browser.evaluate("""(() => {
          const input = document.querySelector('#sessionName');
          return input.scrollWidth <= input.clientWidth;
        })()"""))
        capped = type_name("W" * 20)
        long_name = "W" * 20 + " and a longer name"
        self.assertEqual(type_name(long_name), capped)
        self.assertEqual(self.browser.evaluate("document.querySelector('#sessionName').value"), long_name)
        self.browser.evaluate("document.querySelector('#sessionName').dispatchEvent(new Event('change'))")
        self.flush_preferences()
        self.loaded("/")
        self.assertEqual(self.browser.evaluate(width), capped)
        self.assertEqual(type_name(""), default)
        self.assertNoPageErrors()


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class WalkthroughTest(BrowserCase):
    """One session, driven by clicking, through everything a person does.

    The other tests reach a state by URL, which is precise and skips the
    wiring. This one clicks the tree, the tabs, the search box and the mode
    switch, so a handler that was never attached shows up here.
    """

    writable = True

    def prepare(self):
        subprocess.run(["git", "init", "--initial-branch=main"], cwd=str(self.root),
                       capture_output=True, check=True)
        for key, value in (("user.email", "t@e.i"), ("user.name", "T"),
                           ("commit.gpgsign", "false")):
            subprocess.run(["git", "config", key, value], cwd=str(self.root),
                           capture_output=True, check=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "engine.py").write_text(
            "def start():\n    return 'running'\n" + "# filler\n" * 30
        )
        (self.root / "notes.md").write_text("# Notes\n\nSome prose.\n\n## Detail\n\nMore.\n")
        subprocess.run(["git", "add", "-A"], cwd=str(self.root), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "start"], cwd=str(self.root),
                       capture_output=True, check=True)
        (self.root / "src" / "engine.py").write_text(
            "def start():\n    return 'CHANGED'\n" + "# filler\n" * 30
        )

    def test_the_whole_thing_hangs_together(self):
        self.loaded("/")

        # 1. The tree lists the root and nothing deeper.
        self.waitFor(".row", count=2, what="the root listing")
        self.assertNotIn("engine.py", self.browser.html())

        # 2. Expanding a folder by clicking reveals its children.
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('.row'))"
            ".find(r => r.textContent.includes('src')).click()"
        )
        self.waitFor('[data-path="/src/engine.py"]', what="the expanded folder")

        # 3. Clicking a file opens it, and Monaco renders its text.
        self.browser.click('[data-path="/src/engine.py"]')
        self.browser.wait_for(
            'document.querySelectorAll(".view-line").length > 0', what="the editor"
        )
        self.assertIn("def start():", self.editorText())
        self.waitFor(".tab", what="a tab for it")

        # 4. A markdown file renders rather than opening in the editor.
        self.browser.click('[data-path="/notes.md"]')
        self.waitFor(".md-block", count=2, what="the rendered document")
        self.assertIn("Notes", self.browser.text("#preview"))

        # 5. Split shows both panes.
        self.browser.evaluate(
            "Array.from(document.querySelectorAll('#viewToggle .seg'))"
            ".find(b => b.textContent === 'Split').click()"
        )
        self.browser.wait_for(
            "document.querySelector('.stage').classList.contains('md-split')",
            what="split layout",
        )

        # 6. Typing in the search box finds a nested file.
        self.browser.evaluate(
            "(() => { const s = document.getElementById('search');"
            " s.value = 'engine'; s.dispatchEvent(new Event('input')); })()"
        )
        self.waitFor(".result", what="search results")
        self.assertIn("engine.py", self.browser.text("#results"))

        # 7. Review mode lists the change and opens its diff.
        self.browser.click('#modeSwitch .seg[data-mode="review"]')
        self.waitFor(".change", what="the changed-file list")
        self.browser.click(".change")
        self.waitFor(".diff-hidden-lines", what="the diff, with its collapsed regions")
        # The collapsed band appears before the changed lines are painted,
        # so waiting on it alone reads the diff mid-flight.
        self.browser.wait_for(
            "document.querySelector('.monaco-diff-editor').innerText.includes('CHANGED')",
            what="the changed line to be painted",
        )
        shown = self.diffText()
        self.assertIn("running", shown, "the base side")
        self.assertIn("CHANGED", shown, "the working-tree side")

        # 8. Two tabs are open and the diff one is tinted differently.
        self.assertRegex(self.browser.html(), r'class="tab[^"]*is-diff')

        # Nothing complained along the way.
        self.assertNoPageErrors()


if __name__ == "__main__":
    unittest.main()
