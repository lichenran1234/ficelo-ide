"""Commit edge controls survive lazy diff layout and yield to manual scrolling."""

from test_browser import BrowserCase


class CommitScrollBrowserTest(BrowserCase):
    def test_edges_override_saved_jumps_and_follow_lazy_section_sizes(self):
        self.loaded()
        self.browser.evaluate("""(async () => {
          const { CommitView } = await import('/js/commit-view.js');
          const { loadMonaco } = await import('/js/monaco.js');
          const engine = await loadMonaco();
          window.commitHost = document.createElement('div');
          commitHost.className = 'view-scroll';
          commitHost.style.cssText = 'position:fixed;top:80px;left:280px;width:800px;height:360px;z-index:40;overflow:auto';
          document.body.append(commitHost);
          const files = Array.from({ length: 36 }, (_, index) => ({
            path: 'file-' + String(index).padStart(2, '0') + '.txt', status: 'M',
            old: 'before\\n'.repeat(index === 35 ? 1800 : 60),
            new: 'after\\n'.repeat(index === 35 ? 1800 : 60),
          }));
          window.commitView = new CommitView(engine, commitHost);
          commitView.show({ id:'scroll-fixture', rev:'test', repo:'/',
            commitData: { commit:{ subject:'Many changed files', short:'test', author:'Test' }, files },
            commitViewState: { active:'file-18.txt', offset:45, folded:['file-03.txt'] },
          });
          // The saved section jump has both a queued animation frame and a
          // pending diff result when explicit navigation supersedes it.
          commitView.scrollToEdge('bottom');
        })()""")
        self.browser.wait_for("""(() => {
          const item = commitView.sections.at(-1), editor = item.editor?.getModifiedEditor();
          return editor && editor.getScrollHeight() > 24000 &&
            editor.getScrollHeight() - editor.getLayoutInfo().height - editor.getScrollTop() <= 2 &&
            commitHost.scrollHeight - commitHost.clientHeight - commitHost.scrollTop <= 2;
        })()""", what="the lazy last section and its inner editor to reach the bottom")
        self.assertTrue(self.browser.evaluate("""commitView.sections.filter(item => item.editor).length < 12 &&
          commitView.sections[3].content.hidden && !commitView.sections[3].editor"""))

        # A late diff result, a font/layout change, and a resized scrollport
        # must all retain the requested edge rather than the saved section.
        self.browser.evaluate("""window.lastSection = commitView.sections.at(-1);
          lastSection.models.modified.setValue('later\\n'.repeat(2000));
          commitView.setOptions({ fontSize:15, layout:'inline' });
          commitHost.style.height = '420px';""")
        self.browser.wait_for("""(() => {
          const editor = lastSection.editor.getModifiedEditor();
          return commitHost.clientHeight === 420 && editor.getOption(monaco.editor.EditorOption.fontSize) === 15 &&
            editor.getScrollHeight() - editor.getLayoutInfo().height - editor.getScrollTop() <= 2 &&
            commitHost.scrollHeight - commitHost.clientHeight - commitHost.scrollTop <= 2;
        })()""", what="the bottom to remain anchored through asynchronous layout changes")

        # Wheel input releases the anchor before another delayed layout.
        self.browser.evaluate("""commitHost.dispatchEvent(new WheelEvent('wheel', { deltaY:-100, bubbles:true }));
          commitHost.scrollTop -= 200;
          window.manualTop = commitHost.scrollTop;
          commitHost.style.height = '400px';""")
        self.browser.wait_for("commitHost.clientHeight === 400")
        self.assertEqual(self.browser.evaluate("commitHost.scrollTop"), self.browser.evaluate("manualTop"))
        self.assertGreater(self.browser.evaluate("commitHost.scrollHeight - commitHost.clientHeight - commitHost.scrollTop"), 100)

        self.browser.evaluate("commitView.jump('file-18.txt'); commitView.scrollToEdge('top')")
        self.browser.wait_for("""commitView.sections[0].editor && commitHost.scrollTop === 0 &&
          commitView.sections[0].editor.getModifiedEditor().getScrollTop() === 0""",
                              what="Top to supersede another queued section jump")
        self.browser.evaluate("commitView.setOptions({ fontSize:17 }); commitHost.style.height = '360px'")
        self.browser.wait_for("commitHost.clientHeight === 360")
        self.assertEqual(self.browser.evaluate("commitHost.scrollTop"), 0)

        # A folded boundary remains folded, and an ordinary later scroll
        # also cancels edge holding without requiring a wheel event.
        self.browser.evaluate("commitView.toggle(lastSection); commitView.scrollToEdge('bottom')")
        self.browser.wait_for("lastSection.content.hidden && commitHost.scrollHeight - commitHost.clientHeight - commitHost.scrollTop <= 2")
        self.browser.evaluate("commitHost.scrollTop -= 200")
        self.browser.wait_for("!commitView.edge")
        self.assertTrue(self.browser.evaluate("lastSection.content.hidden && commitView.sections[3].content.hidden"))
        self.browser.evaluate("commitView.dispose(); commitHost.remove()")
        self.assertNoPageErrors()
