"""Reader behavior checks grouped to keep browser startup costs bounded."""

from test_browser import BrowserCase


class MarkdownInteractionTest(BrowserCase):
    writable = True

    def prepare(self):
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "start.md").write_text(
            "# Start\n\n[Other](other.md#destination) [Missing](missing.md)\n\n"
            "![Crop](pic.svg?size=small#crop)\n\n[Outside](https://example.com/)\n\n## End\n"
        )
        (docs / "other.md").write_text("# Other\n\n" + "A paragraph.\n\n" * 50 + "## Destination\n\n" + "After.\n\n" * 30)
        (docs / "pic.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><view id="crop" viewBox="0 0 1 1"/></svg>')
        (self.root / "single.md").write_text("## Single\n\n##### Not in contents\n\n[Chart](chart.md#after-diagram)\n")
        (self.root / "collisions.md").write_text("# Title\n\n" + "Before.\n\n" * 35 + "# Title\n\n" + "Between.\n\n" * 35 + "# Title-2\n\n" + "After.\n\n" * 35)
        (self.root / "long.md").write_text("\n\n".join(
            f"## Section {number}\n\n{'Words. ' * 60}\n\n### Detail {number}\n\nParagraph."
            for number in range(30)
        ))
        (self.root / "wrapped.md").write_text("# Wrapped\n\n" + "word " * 700 + "\n\n## After\n\n" + "A paragraph.\n\n" * 80)
        (self.root / "diagrams.md").write_text(
            "# Diagrams\n\n```mermaid\ngraph TD\nA[Start] --> B[Finish]\n```\n\n"
            "```mermaid\nnot a valid diagram {{{\n```\n\n## After\n"
        )
        (self.root / "chart.md").write_text("# Before\n\n```mermaid\ngraph TD\nA --> B\n```\n\n## After diagram\n\n" + "A paragraph.\n\n" * 100)

    def test_local_links_preserve_the_reader_and_image_suffixes(self):
        self.loaded("/#/docs/start.md")
        self.assertEqual(self.browser.evaluate("document.querySelector('#preview img').getAttribute('src')"), "/preview/docs/pic.svg?size=small#crop")
        self.assertEqual(self.browser.evaluate("(()=>{const a=document.querySelector('#preview a[href^=https]');return [a.target,a.rel];})()"), ["_blank", "noopener noreferrer"])
        self.browser.click('#preview a[href="missing.md"]')
        self.browser.wait_for("document.querySelector('#flash').textContent.startsWith('This link could not be opened:')")
        self.assertEqual(self.browser.evaluate("[document.querySelector('#tabs [aria-selected]').dataset.path,document.querySelectorAll('#tabs .tab').length]"), ["/docs/start.md", 1])
        self.assertEqual(self.browser.errors(), ["Failed to load resource: the server responded with a status of 404 (Not Found)"])
        self.browser.console.clear()
        self.browser.evaluate("""(()=>{const preview=document.querySelector('#preview'),scroll=preview.scrollTo.bind(preview);
          window.scrollBehaviors=[];preview.scrollTo=options=>{scrollBehaviors.push({behavior:options.behavior||'auto',heading:preview.querySelector('h1')?.textContent});scroll(options);};})()""")

        for modifier in ("ctrlKey", "metaKey", "shiftKey", "altKey"):
            self.browser.evaluate("scrollBehaviors=[]")
            prevented = self.browser.evaluate("(()=>{const e=new MouseEvent('click',{bubbles:true,cancelable:true," + modifier + ":true});document.querySelector('#preview a[href=\"other.md#destination\"]').dispatchEvent(e);return e.defaultPrevented;})()")
            self.assertTrue(prevented)
            self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path === '/docs/other.md'")
            self.browser.wait_for("Math.abs(document.querySelector('#destination').getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")
            # A cached tab resumes instantly before following the requested
            # heading. Only that explicit navigation should animate.
            self.assertEqual(self.browser.evaluate("scrollBehaviors.filter(row=>row.heading==='Other' && row.behavior!=='instant').map(row=>row.behavior)"), ["smooth"])
            self.browser.click('#tabs [data-path="/docs/start.md"]')

        # A delayed preflight may not steal focus after another navigation.
        self.browser.evaluate("""(()=>{const original=window.fetch;window.fetch=(url,options)=>{
          if(String(url).includes('/api/info?') && String(url).includes('other.md')) {
            return new Promise(resolve=>{window.finishLink=()=>original(url,options).then(resolve);});
          } return original(url,options);
        };document.querySelector('#preview a[href="other.md#destination"]').click();})()""")
        self.browser.wait_for("typeof finishLink === 'function'")
        self.browser.evaluate("location.hash='#/single.md'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path === '/single.md'")
        self.browser.evaluate("finishLink()")
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs [aria-selected]').dataset.path"), "/single.md")
        self.assertNoPageErrors()

    def test_theme_changes_keep_document_selection_and_current_reading_position(self):
        self.loaded("/#/long.md")
        self.browser.evaluate("document.querySelector('#preview').scrollTop=700")
        self.browser.wait_for("(async()=>{const {readScroll}=await import('/js/settings.js');return readScroll('/long.md')?.top===700;})()")
        self.browser.evaluate("""(()=>{const preview=document.querySelector('#preview');preview.scrollTop=1700;
          window.keptParagraph=preview.querySelector('p');const range=document.createRange();range.selectNodeContents(keptParagraph);
          const selection=getSelection();selection.removeAllRanges();selection.addRange(range);window.keptSelection=selection.toString();})()""")
        self.toggle_theme()
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertEqual(self.browser.evaluate("[document.querySelector('#preview').scrollTop,document.querySelector('#preview p')===keptParagraph,getSelection().toString()===keptSelection]"), [1700, True, True])
        self.browser.wait_for("(async()=>{const {readScroll}=await import('/js/settings.js');return readScroll('/long.md')?.top===1700;})()")
        self.assertNoPageErrors()

    def test_reselecting_the_active_file_keeps_its_rendered_preview(self):
        self.loaded("/#/chart.md")
        self.waitFor("#preview .mermaid-figure svg")
        for selector in ('#tree [data-path="/chart.md"]', '#tabs [data-path="/chart.md"]'):
            with self.subTest(selector=selector):
                self.browser.evaluate("""(() => {
                  const preview = document.querySelector('#preview');
                  preview.scrollTop = 240;
                  window.keptHeading = preview.querySelector('h1');
                  window.keptDiagram = preview.querySelector('.mermaid-figure svg');
                })()""")
                self.browser.click(selector)
                self.browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                self.assertEqual(self.browser.evaluate("""[
                  document.querySelector('#preview h1') === keptHeading,
                  document.querySelector('#preview .mermaid-figure svg') === keptDiagram,
                  document.querySelector('#preview').scrollTop
                ]"""), [True, True, 240])
        path = self.root / "chart.md"
        path.write_text(path.read_text().replace("# Before", "# Updated"))
        self.browser.click("#reloadFile")
        self.browser.wait_for("document.querySelector('#preview h1')?.textContent === 'Updated'")
        self.waitFor("#preview .mermaid-figure svg")
        self.assertFalse(self.browser.evaluate("document.querySelector('#preview .mermaid-figure svg') === keptDiagram"))
        self.assertNoPageErrors()

    def test_diagram_rendering_does_not_move_the_status_bar(self):
        self.loaded("/#/diagrams.md")
        self.waitFor("#preview .mermaid-figure svg")
        self.waitFor("#preview .is-diagram-error")
        result = self.browser.evaluate("""(async () => {
          const footer = document.querySelector('.statusbar');
          const bottom = footer.getBoundingClientRect().bottom;
          const height = document.documentElement.scrollHeight;
          const children = new Set(document.body.children);
          let movement = 0, overflow = 0, samples = 0;
          const sample = () => {
            movement = Math.max(movement, Math.abs(footer.getBoundingClientRect().bottom - bottom));
            overflow = Math.max(overflow, document.documentElement.scrollHeight - height);
            samples++;
          };
          const observer = new MutationObserver(sample);
          observer.observe(document.body, {childList: true, subtree: true});
          try {
            const {renderDiagrams} = await import('/js/mermaid.js');
            const rendered = await renderDiagrams(document.querySelector('#preview'), true);
            sample();
            return {movement, overflow, samples, rendered,
              leaked: [...document.body.children].filter(node => !children.has(node)).length};
          } finally { observer.disconnect(); }
        })()""")
        self.assertEqual(result["rendered"], 1)
        self.assertGreater(result["samples"], 1)
        self.assertEqual([result["movement"], result["overflow"]], [0, 0])
        self.assertEqual(result["leaked"], 0)
        self.assertNoPageErrors()

    def test_reselecting_the_active_file_cancels_pending_navigation(self):
        self.loaded("/#/chart.md")
        self.waitFor("#preview .mermaid-figure svg")
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js');
          const read = api.read;
          api.read = (path) => path === '/single.md'
            ? new Promise(resolve => { window.finishOpen = () => read(path).then(resolve); }) : read(path);
          window.keptDiagram = document.querySelector('#preview .mermaid-figure svg');
        })()""")
        self.browser.click('#tree [data-path="/single.md"]')
        self.browser.wait_for("typeof finishOpen === 'function'")
        self.browser.click('#tree [data-path="/chart.md"]')
        self.browser.evaluate("finishOpen()")
        self.browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        self.assertEqual(self.browser.evaluate("document.querySelector('#tabs [aria-selected=true]').dataset.path"), "/chart.md")
        self.assertTrue(self.browser.evaluate("document.querySelector('#preview .mermaid-figure svg') === keptDiagram"))
        self.assertNoPageErrors()

    def test_contents_use_relative_levels_and_follow_the_last_section(self):
        self.loaded("/#/single.md")
        self.assertTrue(self.browser.evaluate("document.querySelector('#tocPane').hidden && document.querySelector('#tocToggle').hidden"))
        self.browser.evaluate("location.hash='#/long.md'")
        self.browser.wait_for("document.querySelectorAll('#toc .toc-item').length===60")
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('#toc .toc-item')].slice(0,2).map(n=>n.className)"), ["toc-item depth-1", "toc-item depth-2"])
        self.browser.evaluate("document.querySelector('#preview').scrollTop=document.querySelector('#preview').scrollHeight")
        self.browser.wait_for("document.querySelector('#toc [aria-current]')?.textContent==='Detail 29'")
        self.assertTrue(self.browser.evaluate("(()=>{const pane=document.querySelector('#tocPane'),row=document.querySelector('#toc [aria-current]');const a=pane.getBoundingClientRect(),b=row.getBoundingClientRect();return pane.scrollTop>0 && b.top>=a.top && b.bottom<=a.bottom+1;})()"))
        self.browser.click('#toc [data-heading="section-10"]')
        self.browser.wait_for("Math.abs(document.querySelector('#section-10').getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")
        self.browser.wait_for("(async()=>{const {readScroll}=await import('/js/settings.js');return readScroll('/long.md')?.anchor==='section-10';})()")
        self.loaded("/#/long.md")
        self.browser.wait_for("Math.abs(document.querySelector('#section-10').getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")
        self.browser.evaluate("location.hash='#/collisions.md'")
        self.browser.wait_for("document.querySelectorAll('#toc .toc-item').length===3")
        self.browser.click('#toc [data-heading-index="2"]')
        self.browser.wait_for("Math.abs(document.querySelectorAll('#preview h1')[2].getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('#toc [aria-current]')].map(n=>n.dataset.headingIndex)"), ["2"])
        self.assertNoPageErrors()

    def test_split_scroll_moves_continuously_inside_wrapped_source_lines(self):
        self.loaded("/#/wrapped.md")
        self.browser.click('#viewToggle [data-view="split"]')
        self.browser.evaluate("""(()=>{window.editor=monaco.editor.getEditors()[0];editor.updateOptions({wordWrap:'on'});
          editor.layout();window.keptModel=editor.getModel();editor.setPosition({lineNumber:3,column:30});})()""")
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        positions: list[float] = []
        for top in (80, 140, 200):
            self.browser.evaluate(f"editor.setScrollTop({top})")
            minimum = positions[-1] + 1 if positions else 0
            self.browser.wait_for(f"document.querySelector('#preview').scrollTop > {minimum}")
            positions.append(self.browser.evaluate("document.querySelector('#preview').scrollTop"))
            self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertTrue(positions[0] < positions[1] < positions[2], positions)
        self.browser.evaluate("document.querySelector('#preview').scrollTop += 30")
        self.browser.wait_for("editor.getScrollTop()>200")
        self.assertEqual(self.browser.evaluate("editor.getVisibleRanges()[0].startLineNumber"), 3)
        self.browser.evaluate("editor.executeEdits('test',[{range:keptModel.getFullModelRange(),text:keptModel.getValue()+'\\nTail'}])")
        self.browser.wait_for("document.querySelector('#preview').textContent.includes('Tail')")
        self.assertTrue(self.browser.evaluate("editor.getModel()===keptModel && document.querySelector('#preview').scrollTop>0"))
        self.assertNoPageErrors()

    def test_diagram_loader_retries_and_uses_the_latest_theme(self):
        self.loaded("/#/single.md")
        result = self.browser.evaluate("""(async()=>{
          const helper=await import('/js/mermaid.js?retry-test');const append=document.head.append,define=window.define;
          const previous=window.mermaid,host=document.createElement('div');document.body.append(host);
          host.innerHTML='<pre><code class="language-mermaid">first</code></pre><pre><code class="language-mermaid">other</code></pre>';
          let attempts=0,release;const themes=[],ids=[],sources=[];
          document.head.append=function(node){if(!node.src?.includes('mermaid.min.js'))return append.call(this,node);
            attempts++;if(attempts===1)queueMicrotask(()=>node.onerror());else release=()=>{
              window.mermaid={initialize:options=>themes.push([options.theme,options.securityLevel]),render:async(id,source)=>{
                ids.push(id);sources.push(source);return {svg:'<svg id="'+id+'"><text>'+source+'</text></svg>'};}};node.onload();};};
          try {
            const failed=await helper.renderDiagrams(host,false),source=host.querySelector('pre').textContent;
            const retry=helper.renderDiagrams(host,false);helper.setMermaidTheme(true);release();const rendered=await retry;
            await helper.renderDiagrams(host,false);
            return {attempts,failed,source,rendered,themes,ids,sources,errors:host.querySelectorAll('.is-diagram-error').length,restored:window.define===define};
          } finally {document.head.append=append;window.mermaid=previous;window.define=define;host.remove();}
        })()""")
        self.assertEqual([result["attempts"], result["failed"], result["source"], result["rendered"], result["errors"], result["restored"]], [2, 0, "first", 2, 0, True])
        self.assertEqual(result["themes"], [["dark", "strict"], ["dark", "strict"], ["default", "strict"], ["default", "strict"]])
        self.assertEqual(len(set(result["ids"])), 4)
        self.assertEqual(result["sources"], ["first", "other", "first", "other"])
        self.assertNoPageErrors()

    def test_invalid_diagrams_stay_local_and_theme_redraws_only_diagrams(self):
        self.loaded("/#/diagrams.md")
        self.browser.wait_for("document.querySelector('#preview .mermaid-figure svg') && document.querySelector('#preview .is-diagram-error')")
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('body svg')].filter(n=>n.id.startsWith('ficelo-diagram-')&&!n.closest('#preview')).length"), 0)
        self.assertEqual(self.browser.evaluate("document.querySelector('#preview .is-diagram-error pre').textContent"), "not a valid diagram {{{\n")
        self.browser.evaluate("window.heading=document.querySelector('#preview h1');window.oldDiagram=document.querySelector('#preview .mermaid-figure svg').id")
        self.toggle_theme()
        self.browser.wait_for("document.querySelector('#preview .mermaid-figure svg').id!==oldDiagram")
        self.assertTrue(self.browser.evaluate("document.querySelector('#preview h1')===heading"))
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('body svg')].filter(n=>n.id.startsWith('ficelo-diagram-')&&!n.closest('#preview')).length"), 0)

    def test_late_diagrams_settle_the_latest_layout_without_overriding_reader_movement(self):
        self.loaded("/#/chart.md")
        self.waitFor("#preview .mermaid-figure svg")
        self.browser.click('#toc [data-heading="after-diagram"]')
        self.browser.wait_for("(async()=>{const {readScroll}=await import('/js/settings.js');return readScroll('/chart.md')?.anchor==='after-diagram';})()")
        self.browser.evaluate("location.hash='#/single.md'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path==='/single.md'")
        self.browser.evaluate("""window.originalRender=mermaid.render;window.held=[];
          mermaid.render=(id)=>new Promise(resolve=>held.push(()=>resolve({svg:'<svg id="'+id+'" height="3000"></svg>'})));""")
        self.browser.click('#tabs [data-path="/chart.md"]')
        # Tab switching retains the rendered diagram. Explicit reload is what
        # starts a new render and exercises late layout restoration.
        self.browser.click('#reloadFile')
        self.browser.wait_for("held.length===1")
        self.toggle_theme()
        self.browser.evaluate("held[0]()")
        self.browser.wait_for("held.length===2")
        self.browser.evaluate("held[1]()")
        self.browser.wait_for("document.querySelector('#preview .mermaid-figure svg') && Math.abs(document.querySelector('#after-diagram').getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")

        self.browser.click('#tabs [data-path="/single.md"]')
        self.browser.evaluate("""window.releaseDiagram=null;mermaid.render=(...args)=>new Promise(resolve=>{
          window.releaseDiagram=()=>{mermaid.render=originalRender;return originalRender(...args).then(resolve);};});""")
        self.browser.click('#tabs [data-path="/chart.md"]')
        self.browser.click('#reloadFile')
        self.browser.wait_for("typeof releaseDiagram==='function' && Math.abs(document.querySelector('#after-diagram').getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")
        self.browser.evaluate("document.querySelector('#preview').scrollTop=1700")
        self.browser.wait_for("(async()=>{const {readScroll}=await import('/js/settings.js');return readScroll('/chart.md')?.top===1700;})()")
        self.browser.evaluate("""(()=>{const top=document.querySelector('#preview').getBoundingClientRect().top;
          window.readingNode=[...document.querySelectorAll('#preview p')].find(node=>node.getBoundingClientRect().top>=top);
          window.readingOffset=readingNode.getBoundingClientRect().top-top;})()""")
        self.browser.evaluate("releaseDiagram()")
        self.waitFor("#preview .mermaid-figure svg")
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertGreater(self.browser.evaluate("document.querySelector('#preview').scrollTop"), 1600)
        self.assertAlmostEqual(self.browser.evaluate("readingNode.getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-readingOffset"), 0, delta=2)
        # Explicit fragments wait for the current theme's final layout and
        # perform one smooth jump, rather than an automatic restore first.
        self.browser.click('#tabs [data-path="/single.md"]')
        self.browser.click('#tabs [data-path="/chart.md"] .close')
        self.browser.evaluate("""(()=>{const preview=document.querySelector('#preview'),scroll=preview.scrollTo.bind(preview);
          window.jumps=[];preview.scrollTo=options=>{if(preview.querySelector('h1')?.textContent==='Before')jumps.push(options.behavior);scroll(options);};
          window.held=[];mermaid.render=(id)=>new Promise(resolve=>held.push(()=>resolve({svg:'<svg id="'+id+'" height="3000"></svg>'})));})()""")
        self.browser.click('#preview a[href="chart.md#after-diagram"]')
        self.browser.wait_for("held.length===1")
        self.toggle_theme()
        self.browser.evaluate("held[0]()")
        self.browser.wait_for("held.length===2")
        self.assertEqual(self.browser.evaluate("jumps"), [])
        self.browser.evaluate("held[1]()")
        self.browser.wait_for("Math.abs(document.querySelector('#after-diagram').getBoundingClientRect().top-document.querySelector('#preview').getBoundingClientRect().top-8)<2")
        self.assertEqual(self.browser.evaluate("jumps"), ["smooth"])
        self.assertNoPageErrors()
