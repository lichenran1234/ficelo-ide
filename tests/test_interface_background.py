"""Interface paper stays independent of readers and usable at every gray."""

import shutil

from test_browser import BrowserCase
from test_modules import WEB, ModuleTest
from test_reading_contrast import CONTRAST_JS


class InterfaceContrastTest(ModuleTest):
    def setUp(self):
        super().setUp()
        for name in ("style.css", "file-icons.css", "review.css"):
            shutil.copy(WEB / name, self.web / name)

    checks = CONTRAST_JS + r'''
import { applyInterfaceBackground } from "/js/interface-background.js";
check("interface text is readable across all gray levels without recoloring readers", async () => {
  for(const name of ['style.css','file-icons.css','review.css'])await new Promise((resolve,reject)=>{
    const link=document.createElement('link');link.rel='stylesheet';link.href='/'+name;
    link.onload=resolve;link.onerror=reject;document.head.append(link);
  });
  const host=document.createElement('div');
  host.innerHTML='<header class="bar"><span>Header</span><button class="icon">Icon</button><button class="ghost">Home</button>'+
    '<input class="path" value="Session"><div class="segmented"><button class="seg" aria-pressed="true">Selected</button></div></header>'+
    '<nav id="sidebar" class="sidebar"><div class="row"><span class="name">File</span></div>'+
    '<div class="row" aria-selected="true"><span class="name">Selected file</span></div><div class="row is-broken"><span class="name">Broken link</span></div></nav>'+
    '<div id="tabs" class="tabs"><div class="tab"><span class="label">Tab</span><button class="close">x</button></div>'+
    '<div class="tab" aria-selected="true"><span class="label">Active tab</span><button class="close">x</button></div></div>'+
    '<aside id="tocPane" class="toc-pane"><span class="toc-title">Contents</span><a class="toc-item">Heading</a><a class="toc-item" aria-current="location">Current</a></aside>'+
    '<footer class="statusbar"><span class="badge" data-writable="true">Read/Write</span><span class="sb-dir">Folder</span></footer>'+
    '<div id="settings" class="settings"><span class="set-title">General</span><span class="set-hint">Hint</span><input class="set-color" value="#F2F2F2"><div class="set-home"><label>Configure Home folder</label><input class="path" value="/documents"></div></div>'+
    '<div class="menu"><button class="menu-item">Copy</button><button class="menu-item is-danger">Delete</button></div>'+
    '<div class="app-dialog"><h2>Confirm</h2><div class="dialog-actions"><button type="button">Cancel</button><button type="submit">Save</button></div></div>'+
    '<div id="viewerHost" class="viewer-host"><article class="json-document"><div class="json-entry"><span class="json-value is-string">Reader text</span></div></article></div>';
  document.body.append(host);
  for(const tone of ['muted','blue','cyan','gold','green','violet','red','orange']) {
    const icon=document.createElement('span');icon.className='file-icon';icon.dataset.tone=tone;icon.textContent='Icon';
    host.querySelector('#sidebar').append(icon);
  }
  for(const name of ['tree-status is-m','tree-status is-a','tree-status is-d','change-status is-modified',
    'change-status is-added','change-status is-deleted','stat-add','stat-del','session-star is-on','icon is-on']) {
    const node=document.createElement('span');node.className=name;node.textContent='Status';
    host.querySelector(name.startsWith('session-')||name.startsWith('icon ')?'.bar':'#sidebar').append(node);
  }
  for(const kind of ['is-diff','is-commit'])for(const selected of [false,true])for(const age of ['0','6']) {
    const tab=document.createElement('div');tab.className='tab '+kind+' age-'+age;
    if(selected)tab.setAttribute('aria-selected','true');tab.innerHTML='<span class="label">Review tab</span><button class="close">x</button>';
    host.querySelector('#tabs').append(tab);
  }
  const root=document.documentElement;root.dataset.theme='light';
  const reader=host.querySelector('.json-value');
  const snapshot=()=>[getComputedStyle(reader).color,getComputedStyle(reader).getPropertyValue('--bg'),getComputedStyle(reader).getPropertyValue('--ink')];
  const original=snapshot();
  const background=node=>{for(let n=node;n;n=n.parentElement){const c=getComputedStyle(n).backgroundColor;if(c!=='rgba(0, 0, 0, 0)')return c;}return '#ffffff';};
  const targets=[...host.querySelectorAll('span,button,input,a,h2,label')].filter(n=>!n.closest('#viewerHost'));
  try {
    for(let level=0;level<=255;level++) {
      applyInterfaceBackground(level);
      for(const node of targets) {
        const ink=getComputedStyle(node).color,paper=background(node);
        if(ratio(ink,paper)<4.5)return level+' '+node.className+' '+node.textContent+': '+ink+' on '+paper;
      }
      if(eq(snapshot(),original))return 'reader palette changed at '+level;
    }
    root.dataset.theme='dark';
    const dark=targets.map(n=>[getComputedStyle(n).color,background(n)]);
    applyInterfaceBackground(0);
    return eq(targets.map(n=>[getComputedStyle(n).color,background(n)]),dark);
  } finally {host.remove();root.dataset.theme='light';applyInterfaceBackground(242);}
});
'''

    def test_full_grayscale_interface_contrast(self):
        self.assertAllPass()


class InterfaceBackgroundBrowserTest(BrowserCase):
    def prepare(self):
        (self.root / "sample.md").write_text("# Sample\n\nReading paper.\n\n## Second\n\nMore text.\n")
        (self.root / "sample.json").write_text('{"message":"Hello"}\n')

    def test_interface_controls_persist_reset_and_leave_readers_independent(self):
        self.loaded("/#/sample.md")
        self.browser.click('#viewToggle [data-view="split"]')

        def panel():
            if self.browser.evaluate("document.querySelector('#settings').hidden"):
                self.browser.click("#settingsToggle")

        def papers():
            return self.browser.evaluate("""['.bar','#sidebar','#tabs','#tocPane','.statusbar','#settings'].map(s=>
              getComputedStyle(document.querySelector(s)).backgroundColor)""")

        def reader_papers():
            return self.browser.evaluate("""['#preview','#editorHost .monaco-editor-background'].map(s=>
              getComputedStyle(document.querySelector(s)).backgroundColor)""")

        def slide(value):
            self.browser.evaluate("""(()=>{const s=document.querySelector('#settings input[name=interfaceBackground]');
              s.value=""" + str(value) + ";s.dispatchEvent(new Event('input',{bubbles:true}));})()")

        def enter(value):
            self.browser.evaluate("""(()=>{const field=document.querySelector('#settings input[name=interfaceBackgroundColor]');
              field.focus();field.select();})()""")
            self.browser._call("Input.insertText", {"text": value})

        panel()
        self.assertEqual(papers(), ["rgb(242, 242, 242)"] * 6)
        self.assertEqual(reader_papers(), ["rgb(242, 242, 242)"] * 2)
        self.assertEqual(self.browser.evaluate("document.querySelector('#settings input[name=interfaceBackgroundColor]').value"), "#F2F2F2")
        self.browser.evaluate("document.querySelector('#settings input[name=interfaceBackground]').focus()")
        for kind in ("keyDown", "keyUp"):
            self.browser._call("Input.dispatchKeyEvent", {"type": kind, "key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37})
        self.assertEqual(papers(), ["rgb(241, 241, 241)"] * 6)
        slide(242)
        self.toggle_theme()
        panel()
        dark_papers, dark_readers = papers(), reader_papers()
        self.toggle_theme()
        panel()
        self.browser.evaluate("void(window.interfaceModel=monaco.editor.getModels().find(m=>m.getValue().startsWith('# Sample')))")
        for level in (0, 117, 118, 255):
            slide(level)
            self.assertEqual(papers(), ["rgb(%d, %d, %d)" % (level, level, level)] * 6)
            self.assertEqual(reader_papers(), ["rgb(242, 242, 242)"] * 2)
        enter(" #a0a0a0 ")
        self.assertEqual(papers(), ["rgb(160, 160, 160)"] * 6)
        enter("#123456")
        self.assertFalse(self.browser.evaluate("document.querySelector('#settings input[name=interfaceBackgroundColor]').checkValidity()"))
        self.assertEqual(papers(), ["rgb(160, 160, 160)"] * 6)
        self.assertTrue(self.browser.evaluate("monaco.editor.getModels().includes(interfaceModel)"))
        self.browser.wait_for("""(async()=>{const a=(await(await fetch('/api/state')).json()).settings['ficelo.appearance'];
          return a?.interfaceBackground===160 && a.mdBackground===242 && a.codeBackground===242;})()""")

        self.loaded("/#/sample.md")
        panel()
        self.assertEqual(papers(), ["rgb(160, 160, 160)"] * 6)
        self.assertEqual(self.browser.evaluate("""['interfaceBackground','interfaceBackgroundColor'].map(name=>
          document.querySelector('#settings input[name='+name+']').value)"""), ["160", "#A0A0A0"])
        self.toggle_theme()
        panel()
        self.assertEqual((papers(), reader_papers()), (dark_papers, dark_readers))
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('#settings .is-background input')].every(n=>n.disabled)"))
        self.toggle_theme()
        panel()
        self.browser.click("#settings .set-reset")
        self.assertEqual(papers(), ["rgb(242, 242, 242)"] * 6)
        self.assertEqual(reader_papers(), ["rgb(242, 242, 242)"] * 2)
        self.browser.wait_for("(async()=>{const a=(await(await fetch('/api/state')).json()).settings['ficelo.appearance'];return a?.interfaceBackground===242;})()")

        self.browser.evaluate("location.hash='#/sample.json'")
        self.waitFor("#viewerHost .json-document")
        snapshot = "[getComputedStyle(document.querySelector('#viewerHost')).backgroundColor,getComputedStyle(document.querySelector('#viewerHost .json-value')).color]"
        ordinary_json = self.browser.evaluate(snapshot)
        panel()
        slide(0)
        self.assertEqual(self.browser.evaluate(snapshot), ordinary_json)
        self.assertNoPageErrors()
