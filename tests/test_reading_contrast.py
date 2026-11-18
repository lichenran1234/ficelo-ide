"""Readable preview and source colors across the full grayscale range."""

import shutil

from test_browser import BrowserCase
from test_modules import WEB, ModuleTest

CONTRAST_JS = r'''
function luminance(color) {
  const rgb = color.startsWith('#') ? [1,3,5].map(at=>parseInt(color.slice(at,at+2),16))
    : color.match(/[\d.]+/g).slice(0,3).map(Number);
  return rgb.map(value=>value/255).map(value=>value<=0.04045?value/12.92:((value+0.055)/1.055)**2.4)
    .reduce((sum,value,index)=>sum+value*[0.2126,0.7152,0.0722][index],0);
}
function ratio(a,b) { const x=luminance(a),y=luminance(b);return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05); }
'''


class ReadingContrastModuleTest(ModuleTest):
    def setUp(self):
        super().setUp()
        for name in ("style.css", "markdown.css", "reading-background.css"):
            shutil.copy(WEB / name, self.web / name)

    checks = CONTRAST_JS + r'''
import { grayColor, backgroundDark, readableColor, paletteFor, applyReadingBackground } from "/js/reading-background.js";
check("gray levels clamp and round without accepting CSS text", () =>
  eq([grayColor(),grayColor(-20),grayColor(300),grayColor(127.6),grayColor(NaN),grayColor('red')],
    ['#f2f2f2','#000000','#ffffff','#808080','#f2f2f2','#f2f2f2']));
check("every gray has readable body, link, stripe and header colors", () => {
  for(let level=0;level<=255;level++) {
    const p=paletteFor(grayColor(level));
    const pairs=[[p.ink,p.paper],[p.muted,p.paper],[p.link,p.paper],[p.warning,p.paper],
      [p.stripeInk,p.stripe],[p.stripeLink,p.stripe],[p.headerInk,p.header],[p.headerLink,p.header]];
    for(const [ink,paper] of pairs)if(ratio(ink,paper)<4.5)return level+': '+ink+' on '+paper;
    const chosen=backgroundDark(p.paper)?'#ffffff':'#000000';
    if(ratio(chosen,p.paper)<4.5)return 'wrong foreground family at '+level;
  }
  return eq([backgroundDark('#757575'),backgroundDark('#808080'),readableColor('#2d3d50','#f5f5f5')],
    [true,false,'#2d3d50']);
});
check("rendered content remains readable at every gray including nested table text", async () => {
  for(const name of ['style.css','markdown.css','reading-background.css'])await new Promise((resolve,reject)=>{
    const link=document.createElement('link');link.rel='stylesheet';link.href='/'+name;
    link.onload=resolve;link.onerror=reject;document.head.append(link);
  });
  const host=document.createElement('div');host.className='preview-scroll';
  host.innerHTML='<article class="markdown"><p>Body <a href="#">link</a> <code>inline</code> <mark><a href="#">marked link</a></mark></p>'+
    '<figure><figcaption>Caption</figcaption></figure><table><thead><tr><th><strong>Header</strong> <a href="#">link</a></th></tr></thead>'+
    '<tbody><tr><td><strong>Ordinary</strong> <a href="#">link</a></td></tr><tr><td><strong>Stripe</strong> <a href="#">link</a></td></tr></tbody></table>'+
    '<table class="front-matter"><tbody><tr><th>Key</th><td>Value</td></tr></tbody></table>'+
    '<blockquote class="warning"><p>Warning <a href="#">link</a></p></blockquote><blockquote class="tip"><p>Tip <a href="#">link</a></p></blockquote>'+
    '<div class="diagram-note">Diagram message</div><pre><code class="hljs"><span class="hljs-keyword">const</span> value = 1;</code></pre></article>';
  document.body.append(host);const root=document.documentElement,previous=root.dataset.theme;root.dataset.theme='light';
  const targets=[...host.querySelectorAll('p,p a,p code,mark,mark a,figcaption,th,th strong,th a,td,td strong,td a,.diagram-note')];
  const background=node=>{for(let n=node;n;n=n.parentElement){const color=getComputedStyle(n).backgroundColor;if(color!=='rgba(0, 0, 0, 0)')return color;}return '#ffffff';};
  const code=()=>[getComputedStyle(host.querySelector('pre code')).backgroundColor,getComputedStyle(host.querySelector('.hljs-keyword')).color];
  const originalCode=code();
  try {
    for(let level=0;level<=255;level++) {
      applyReadingBackground(host,grayColor(level));
      for(const node of targets) {
        const ink=getComputedStyle(node).color,paper=background(node);
        if(ratio(ink,paper)<4.5)return level+' '+node.tagName+' '+node.textContent+': '+ink+' on '+paper;
      }
      for(const node of host.querySelectorAll('blockquote')) {
        const badge=getComputedStyle(node,'::before');
        if(ratio(badge.color,badge.backgroundColor)<4.5)return level+' unreadable callout badge';
      }
      if(eq(code(),originalCode))return 'fenced code palette changed at '+level;
    }
    root.dataset.theme='dark';const markedDark=getComputedStyle(host.querySelector('p')).color;
    applyReadingBackground(host,null);
    return eq(getComputedStyle(host.querySelector('p')).color,markedDark);
  } finally {host.remove();if(previous===undefined)delete root.dataset.theme;else root.dataset.theme=previous;}
});
'''

    def test_full_grayscale_preview_contrast(self):
        self.assertAllPass()


class ReadingContrastBrowserTest(BrowserCase):
    def prepare(self):
        (self.root / "sample.py").write_text(
            "# A readable comment\ndef greeting(name):\n    if name == 1:\n        return 'Hello'\n"
        )

    def test_source_tokens_and_selections_remain_readable_without_changing_base_themes(self):
        self.loaded("/#/sample.py")
        self.editorText()
        self.browser.wait_for("[...document.querySelectorAll('#editorHost .view-line:first-child span')].some(node=>getComputedStyle(node).fontStyle==='italic')")
        report = self.browser.evaluate("(async()=>{" + CONTRAST_JS + r'''
          const {applyTheme}=await import('/js/monaco.js');const {grayColor}=await import('/js/reading-background.js');
          const editor=monaco.editor.getEditors()[0],model=editor.getModel();
          const frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
          const settled=async hex=>{const rgb='rgb('+[1,3,5].map(at=>parseInt(hex.slice(at,at+2),16)).join(', ')+')';
            for(let turn=0;turn<120;turn++){await frame();if([...document.querySelectorAll('#editorHost .view-line:first-child span')].some(node=>
              !node.querySelector('span')&&getComputedStyle(node).fontStyle==='italic'&&getComputedStyle(node).color===rgb))return;}
            throw new Error('Source tokens did not settle for '+hex);};
          const sample=()=>({paper:getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor,
            tokens:[...document.querySelectorAll('#editorHost .view-line span')].filter(n=>!n.querySelector('span')).flatMap(n=>
              [...n.textContent].filter(char=>char.trim()).map(char=>[char,getComputedStyle(n).color]))});
          const define=monaco.editor.defineTheme,names=new Set();let latest=null;const failures=[];
          const host=document.createElement('div');host.className='monaco-editor';host.style.position='fixed';host.style.left='-10000px';document.body.append(host);
          const examples={javascript:'// comment\nconst value = `hello ${name}`; console.log(true, /pattern/g);',
            json:'{"name":"value","count":123,"enabled":true,"empty":null}',markdown:'# Title\n**bold** _italic_ `code` [link](target)\n- list',
            shell:'# comment\necho "$NAME" | sort --reverse',html:'<!DOCTYPE html><div class="card">&amp; {{ value }}</div>',
            css:'/* note */ .name {color: red; width: 12px !important;}',python:'@decorator\nclass Name: # note\n    def method(self): return True',
            yaml:'name: value\nitems: [true, 12, null]'};
          monaco.editor.defineTheme=(name,data)=>{names.add(name);latest=data;return define(name,data);};
          try {
            editor.focus();editor.setSelection(new monaco.Selection(1,1,1,1));
            applyTheme('light');await settled('#6e7781');const light=sample();applyTheme('dark');await settled('#8b949e');const dark=sample();
            for(const level of [0,32,64,100,110,117,118,120,128,180,242,255]) {
              const paper=grayColor(level);applyTheme('light',paper);await settled('#'+latest.rules.find(rule=>rule.token==='comment').foreground);
              for(const rule of latest.rules)if(ratio('#'+rule.foreground,paper)<4.5)failures.push('token '+rule.token+' at '+level);
              const actual=sample();for(const [,ink] of actual.tokens)if(ratio(ink,actual.paper)<4.5)failures.push('painted token at '+level);
              for(const key of ['editorLineNumber.foreground','editorLineNumber.activeForeground','editor.foreground']) {
                if(ratio(latest.colors[key],paper)<4.5)failures.push(key+' at '+level);
              }
              editor.focus();editor.setSelection(new monaco.Selection(2,1,2,20));await frame();
              const selection=document.querySelector('#editorHost .cslr');
              if(!selection)failures.push('no selected text at '+level);
              const selected=[...document.querySelectorAll('#editorHost .view-line:nth-child(2) span')].filter(node=>node.textContent.trim());
              for(const node of selected)if(ratio(getComputedStyle(node).color,latest.colors['editor.selectionBackground'])<4.5)failures.push('selected text at '+level);
              editor.setSelection(new monaco.Selection(1,1,1,1));await frame();
              if(level===117||level===128)for(const [language,source]of Object.entries(examples)) {
                host.innerHTML=await monaco.editor.colorize(source,language,{});
                for(const node of host.querySelectorAll('span'))if(!node.querySelector('span')&&node.textContent.trim()&&ratio(getComputedStyle(node).color,paper)<4.5)
                  failures.push(language+' token '+node.textContent+' at '+level);
              }
            }
            applyTheme('dark','#808080');await settled('#8b949e');const afterDark=sample(),sameDark=JSON.stringify(afterDark)===JSON.stringify(dark);
            applyTheme('light',null);await settled('#6e7781');const afterLight=sample(),sameLight=JSON.stringify(afterLight)===JSON.stringify(light);
            const difference=(before,after)=>{const at=before.tokens.findIndex((token,index)=>JSON.stringify(token)!==JSON.stringify(after.tokens[index]));
              return {paper:[before.paper,after.paper],count:[before.tokens.length,after.tokens.length],before:before.tokens.slice(at,at+4),after:after.tokens.slice(at,at+4)};};
            return {failures,sameDark,sameLight,names:[...names],sameModel:model===editor.getModel(),
              darkDifference:!sameDark&&difference(dark,afterDark),lightDifference:!sameLight&&difference(light,afterLight)};
          } finally {host.remove();monaco.editor.defineTheme=define;applyTheme('light');}
        })()''')
        self.assertEqual(report["failures"], [], repr(report))
        self.assertEqual(report["names"], ["github-light-reading"])
        self.assertTrue(report["sameDark"] and report["sameLight"] and report["sameModel"], repr(report))
        self.assertNoPageErrors()
