"""Reading backgrounds, independent appearance controls, and file overrides."""

import json
import shutil

from test_modules import WEB, ModuleTest
from test_browser import BrowserCase


class ReadingBackgroundTest(ModuleTest):
    def setUp(self):
        super().setUp()
        for name in ("style.css", "markdown.css", "reading-background.css"):
            shutil.copy(WEB / name, self.web / name)

    checks = r'''
import { readFileBackground, paletteFor, applyReadingBackground, parseGrayColor } from "/js/reading-background.js";

check("manual colors accept only neutral gray hex", () => {
  const valid = [["#000000",0],["#fff",255],["ABCABC",null],["aaa",170],[" #F5F5F5 ",245],["111111",17]];
  for (const [text,expected] of valid) {
    const error=eq(parseGrayColor(text),expected);if(error)return text+": "+error;
  }
  for(const text of [null,245,"", "#", "#12", "#1234", "#ff0000", "#123456", "gray", "#fff; color:red"])
    if(parseGrayColor(text)!==null)return "accepted invalid gray: "+text;
});

check("absolute file preferences are global and normalize hex colors", () => {
  store.set("ficelo.filebackgrounds", {"/documents/notes.md":"#E8E8E8"});
  store.scope("background-first"); const first = readFileBackground("/documents/notes.md");
  store.scope("background-second"); const second = readFileBackground("/documents/notes.md");
  store.scope(null);
  return eq([first,second,readFileBackground("/documents/notes.md",true),
    readFileBackground("/notes.md"),readFileBackground("documents/notes.md")],
    ["#e8e8e8","#e8e8e8",null,null,null]);
});
check("malformed maps and values never become CSS", () => {
  const values = [null,1,{},[],"#eee","#12345678","red","#12345g","#eeeeee; color:red"];
  for (const value of values) {
    store.set("ficelo.filebackgrounds", {"/notes.md":value});
    if (readFileBackground("/notes.md") !== null || paletteFor(value) !== null) return "accepted invalid color";
  }
  for (const map of [null,[],"#fafafa",42]) {
    store.set("ficelo.filebackgrounds",map);
    if (readFileBackground("/notes.md") !== null) return "accepted invalid map";
  }
});
check("configured paper and tables reset cleanly and leave Dark/code colors intact", async () => {
  for (const name of ["style.css","markdown.css","reading-background.css"]) {
    await new Promise((resolve,reject) => {
      const sheet = document.createElement("link"); sheet.rel="stylesheet"; sheet.href="/"+name;
      sheet.onload=resolve; sheet.onerror=reject; document.head.append(sheet);
    });
  }
  const root = document.documentElement, previousTheme = root.dataset.theme;
  const make = () => {
    const host = document.createElement("div"); host.className="preview-scroll";
    host.innerHTML='<article class="markdown"><p>Reading paper.</p><table><thead><tr><th>Heading</th></tr></thead>'+
      '<tbody><tr><td>First</td></tr><tr><td>Second</td></tr></tbody></table>'+
      '<table class="front-matter"><tbody><tr><th>Title</th><td>Notes</td></tr></tbody></table>'+
      '<pre><code class="hljs"><span class="hljs-keyword">const</span> value = 1;</code></pre></article>';
    document.body.append(host); return host;
  };
  const chosen=make(), ordinary=make();
  const read = host => {
    const css = selector => getComputedStyle(selector ? host.querySelector(selector) : host);
    return {paper:css().backgroundColor,article:css('.markdown').backgroundColor,
      row:css('tbody tr').backgroundColor,stripe:css('tbody tr:nth-child(2)').backgroundColor,
      header:css('thead th').backgroundColor,border:css('td').borderTopColor,
      metadata:css('.front-matter th').backgroundColor,code:css('pre code').backgroundColor,
      token:css('.hljs-keyword').color};
  };
  const rgb = hex => 'rgb('+[1,3,5].map(at=>parseInt(hex.slice(at,at+2),16)).join(', ')+')';
  try {
    root.dataset.theme="light"; const before=read(ordinary);
    for (const color of ["#FAFAFA","#F5F5F5","#EFEFEF","#E8E8E8","#E0E0E0","#D8D8D8"]) {
      const palette=applyReadingBackground(chosen,color), got=read(chosen);
      const expected={paper:rgb(palette.paper),article:rgb(palette.paper),row:rgb(palette.row),
        stripe:rgb(palette.stripe),header:rgb(palette.header),border:rgb(palette.border),
        metadata:rgb(palette.header),code:before.code,token:before.token};
      const error=eq(got,expected); if(error)return color+": "+error;
      if(eq(read(ordinary),before))return "ordinary document changed";
      if(got.stripe===got.row || got.header===got.row || got.border===got.row)return "table contrast lost";
    }
    root.dataset.theme="dark";
    if(eq(read(chosen),read(ordinary)))return "configured colors leaked into Dark";
    root.dataset.theme="light"; applyReadingBackground(chosen,null);
    if(chosen.hasAttribute('data-file-background') || chosen.style.length)return "stale marker or variables";
    return eq(read(chosen),before);
  } finally {
    chosen.remove(); ordinary.remove();
    if(previousTheme===undefined)delete root.dataset.theme; else root.dataset.theme=previousTheme;
  }
});
'''

    def test_reading_backgrounds(self):
        self.assertAllPass()


class ReadingBackgroundBrowserTest(BrowserCase):
    writable = True
    colors = ("fafafa", "f5f5f5", "efefef", "e8e8e8", "e0e0e0", "d8d8d8")

    def prepare(self):
        document = "# First\n\n| Name | Value |\n| --- | --- |\n| One | 1 |\n| Two | 2 |\n\n## Second\n"
        for color in self.colors:
            (self.root / (color + ".md")).write_text(document)
        (self.root / "ordinary.md").write_text(document)
        (self.root / "sample.py").write_text("# Sample code\nvalue = 1\nprint(value)\n")
        (self.root / "plain.py").write_text("".join("value_%d = %d\n" % (i, i) for i in range(160)))
        (self.root / "diagram.md").write_text("# Diagram\n\n```mermaid\ngraph TD\n A --> B\n```\n")

    def test_file_colors_follow_tabs_views_and_theme_without_leaking(self):
        profiles = {str(self.root / (color + ".md")): "#" + color for color in self.colors}
        profiles[str(self.root / "sample.py")] = "#e8e8e8"
        (self.server.config.state_dir / "settings.json").write_text(json.dumps({"ficelo.filebackgrounds": profiles}))
        self.loaded("/#/fafafa.md")
        for color in self.colors:
            self.browser.evaluate("location.hash=" + json.dumps("#/" + color + ".md"))
            self.browser.wait_for("document.querySelector('#preview').dataset.fileBackground === " + json.dumps("#" + color))
            expected = "rgb(%d, %d, %d)" % tuple(int(color[at:at + 2], 16) for at in (0, 2, 4))
            values = self.browser.evaluate('''(() => {
              const color = selector => getComputedStyle(document.querySelector(selector)).backgroundColor;
              return [color('#preview'),color('#markdownContent'),color('#preview tbody tr:first-child'),
                color('#preview tbody tr:nth-child(2)'),color('#preview thead th'),
                getComputedStyle(document.querySelector('#preview td')).borderTopColor];
            })()''')
            self.assertEqual(values[:3], [expected] * 3)
            self.assertEqual(len(set(values[2:])), 4, "table rows, headers, and borders must remain distinct")
            self.browser.click('#viewToggle [data-view="split"]')
            self.assertEqual(self.browser.evaluate('''[
              getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor,
              getComputedStyle(document.querySelector('#editorHost .margin')).backgroundColor]'''), [expected] * 2)
            self.browser.click('#viewToggle [data-view="preview"]')

        self.toggle_theme()
        self.assertFalse(self.browser.evaluate("document.querySelector('#preview').hasAttribute('data-file-background')"))
        self.toggle_theme()
        self.assertEqual(self.browser.evaluate("document.querySelector('#preview').dataset.fileBackground"), "#d8d8d8")
        self.browser.evaluate("location.hash='#/ordinary.md'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path === '/ordinary.md'")
        self.assertEqual(self.browser.evaluate("document.querySelector('#preview').dataset.fileBackground"), "#f2f2f2")
        self.browser.click('#viewToggle [data-view="split"]')
        self.assertEqual(self.browser.evaluate("getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor"), "rgb(242, 242, 242)")

        self.browser.evaluate("location.hash='#/sample.py'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path === '/sample.py'")
        self.assertEqual(self.browser.evaluate("getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor"), "rgb(232, 232, 232)")
        self.toggle_theme()
        self.assertEqual(self.browser.evaluate("getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor"), "rgb(13, 17, 23)")
        self.assertNoPageErrors()

    def test_manual_gray_fields_reject_color_and_stay_synchronized(self):
        self.loaded("/#/ordinary.md")
        self.browser.click('#viewToggle [data-view="split"]')
        self.browser.click("#settingsToggle")

        def enter(key, value):
            selector = json.dumps('#settings input[name="' + key + 'Color"]')
            self.browser.evaluate("(()=>{const field=document.querySelector(" + selector + ");field.focus();field.select();})()")
            self.browser._call("Input.insertText", {"text": value})

        def field_state(key):
            range_selector = json.dumps('#settings input[name="' + key + '"]')
            field_selector = json.dumps('#settings input[name="' + key + 'Color"]')
            return self.browser.evaluate("""(() => {
              const range=document.querySelector(""" + range_selector + """);
              const field=document.querySelector(""" + field_selector + """);
              return [range.value,field.value,field.checkValidity()];
            })()""")

        enter("mdBackground", " #A3A3A3 ")
        enter("codeBackground", "000")
        self.assertEqual(field_state("mdBackground"), ["163", "#A3A3A3", True])
        self.assertEqual(field_state("codeBackground"), ["0", "000", True])
        for invalid in ("#ff0000", "#123456", "#12", "garbage", "#00000000", "#FFFFFFoops"):
            enter("mdBackground", invalid)
            self.assertEqual(field_state("mdBackground"), ["163", invalid, False])
            self.assertEqual(self.browser.evaluate("getComputedStyle(document.querySelector('#preview')).backgroundColor"), "rgb(163, 163, 163)")
            self.assertEqual(self.browser.evaluate("getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor"), "rgb(0, 0, 0)")
        self.browser.evaluate("(()=>{const range=document.querySelector('#settings input[name=mdBackground]');range.value=17;range.dispatchEvent(new Event('input',{bubbles:true}));})()")
        self.assertEqual(field_state("mdBackground"), ["17", "#111111", True])
        enter("mdBackground", "fff")
        for kind in ("keyDown", "keyUp"):
            event = {"type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}
            if kind == "keyDown":
                event.update(text="\r", unmodifiedText="\r")
            self.browser._call("Input.dispatchKeyEvent", event)
        self.assertEqual(field_state("mdBackground"), ["255", "#FFFFFF", True])
        self.browser.wait_for("(async()=>{const a=(await(await fetch('/api/state')).json()).settings['ficelo.appearance'];return a?.mdBackground===255 && a?.codeBackground===0;})()")
        self.loaded("/#/ordinary.md")
        self.browser.click("#settingsToggle")
        self.assertEqual(field_state("mdBackground"), ["255", "#FFFFFF", True])
        self.assertEqual(field_state("codeBackground"), ["0", "#000000", True])
        self.assertNoPageErrors()

    def test_independent_gray_sliders_persist_reset_and_preserve_editing(self):
        def panel():
            if self.browser.evaluate("document.querySelector('#settings').hidden"):
                self.browser.click("#settingsToggle")

        def slide(key, value):
            selector = json.dumps('#settings input[name="' + key + '"]')
            self.browser.evaluate("""(() => {
              const slider=document.querySelector(""" + selector + """);
              slider.value=""" + str(value) + """;slider.dispatchEvent(new Event('input',{bubbles:true}));
            })()""")

        def colors():
            return self.browser.evaluate("""[
              getComputedStyle(document.querySelector('#preview')).backgroundColor,
              getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor]""")

        def saved(markdown, code):
            self.browser.wait_for("""(async()=>{
              const a=(await (await fetch('/api/state')).json()).settings['ficelo.appearance'];
              return a?.mdBackground===""" + str(markdown) + " && a?.codeBackground===" + str(code) + ";})()")

        self.loaded("/#/ordinary.md")
        self.browser.click('#viewToggle [data-view="split"]')
        panel()
        self.assertEqual(colors(), ["rgb(242, 242, 242)"] * 2)
        self.assertEqual(self.browser.evaluate("""[...document.querySelectorAll('#settings .is-background input[type=range]')].map(s=>
          [s.name,s.min,s.max,s.step,s.value,s.getAttribute('aria-valuetext'),s.disabled])"""), [
            ["interfaceBackground", "0", "255", "1", "242", "#F2F2F2", False],
            ["mdBackground", "0", "255", "1", "242", "#F2F2F2", False],
            ["codeBackground", "0", "255", "1", "242", "#F2F2F2", False],
        ])
        self.browser.evaluate("document.querySelector('#settings input[name=mdBackground]').focus()")
        for kind in ("keyDown", "keyUp"):
            self.browser._call("Input.dispatchKeyEvent", {"type": kind, "key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37})
        self.assertEqual(colors(), ["rgb(241, 241, 241)", "rgb(242, 242, 242)"])
        slide("mdBackground", 0)
        self.assertEqual(colors(), ["rgb(0, 0, 0)", "rgb(242, 242, 242)"])
        slide("mdBackground", 255)
        slide("codeBackground", 0)
        self.assertEqual(colors(), ["rgb(255, 255, 255)", "rgb(0, 0, 0)"])
        saved(255, 0)

        self.browser.evaluate("location.hash='#/plain.py'")
        self.waitFor('#tabs [aria-selected][data-path="/plain.py"]')
        self.browser.evaluate("""(() => {
          window.grayEditor=monaco.editor.getEditors().find(e=>e.getDomNode()?.closest('#editorHost'));
          window.grayModel=grayEditor.getModel();window.originalGrayText=grayModel.getValue();
          grayEditor.pushUndoStop();
          grayEditor.executeEdits('gray-test',[{range:new monaco.Range(1,1,1,1),text:'# Unsaved edit\\n'}]);
          grayEditor.pushUndoStop();grayEditor.setPosition({lineNumber:40,column:3});
          grayEditor.setScrollTop(500,monaco.editor.ScrollType.Immediate);
          window.grayPosition=grayEditor.getPosition();window.grayTop=grayEditor.getScrollTop();
        })()""")
        panel()
        slide("codeBackground", 173)
        self.assertEqual(colors()[1], "rgb(173, 173, 173)")
        self.assertEqual(self.browser.evaluate("""({sameModel:grayEditor.getModel()===grayModel,
          unsaved:grayModel.getValue().startsWith('# Unsaved edit'),
          position:grayEditor.getPosition(),savedPosition:grayPosition,top:grayEditor.getScrollTop(),savedTop:grayTop})"""),
          self.browser.evaluate("({sameModel:true,unsaved:true,position:grayPosition,savedPosition:grayPosition,top:grayTop,savedTop:grayTop})"))
        self.browser.evaluate("grayModel.undo()")
        self.assertTrue(self.browser.evaluate("grayModel.getValue()===originalGrayText"))
        saved(255, 173)
        self.loaded("/#/plain.py")
        self.assertEqual(colors()[1], "rgb(173, 173, 173)")
        panel()
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('#settings .is-background input[type=range]')].map(s=>s.value)"), ["242", "255", "173"])

        override = json.dumps({str(self.root / "ordinary.md"): "#e8e8e8"})
        self.browser.evaluate("(async()=>{const {store}=await import('/js/util.js');store.set('ficelo.filebackgrounds'," + override + ");})()")
        self.browser.evaluate("location.hash='#/ordinary.md'")
        self.waitFor('#tabs [aria-selected][data-path="/ordinary.md"]')
        self.browser.click('#viewToggle [data-view="split"]')
        panel()
        slide("mdBackground", 111)
        slide("codeBackground", 64)
        self.assertEqual(colors(), ["rgb(232, 232, 232)"] * 2)
        self.toggle_theme()
        panel()
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('#settings .is-background input[type=range]')].every(s=>s.disabled)"))
        self.assertFalse(self.browser.evaluate("document.querySelector('#preview').hasAttribute('data-file-background')"))
        self.assertEqual(colors()[1], "rgb(13, 17, 23)")
        self.toggle_theme()
        panel()
        self.browser.click("#settings .set-reset")
        saved(242, 242)
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('#settings .is-background input[type=range]')].map(s=>s.value)"), ["242", "242", "242"])
        self.assertEqual(colors(), ["rgb(232, 232, 232)"] * 2)

        self.browser.evaluate("location.hash='#/diagram.md'")
        self.waitFor("#preview .mermaid-figure svg")
        self.browser.evaluate("""(() => {
          window.grayDiagramCalls=0;const render=mermaid.render.bind(mermaid);
          window.grayDiagramId=document.querySelector('#preview .mermaid-figure svg').id;
          mermaid.render=(...args)=>{grayDiagramCalls++;return render(...args);};
        })()""")
        panel()
        for level in (240, 237, 230, 220):
            slide("mdBackground", level)
        slide("codeBackground", 0)
        self.assertEqual(self.browser.evaluate("grayDiagramCalls"), 0)
        slide("mdBackground", 0)
        self.browser.wait_for("grayDiagramCalls===1 && document.querySelector('#preview .mermaid-figure svg')?.id!==grayDiagramId")
        self.assertEqual(self.browser.evaluate("grayDiagramCalls"), 1)
        self.assertNoPageErrors()
