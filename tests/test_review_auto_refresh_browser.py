"""Automatic review refresh retries changes that leave working files untouched."""

from urllib.parse import quote

import test_review_browser as review_browser
from test_browser import BrowserCase


class ReviewAutoRefreshBrowserTest(BrowserCase):
    writable = True
    git = review_browser.RepositoryReviewBrowserTest.git
    prepare = review_browser.RepositoryReviewBrowserTest.prepare

    def test_older_base_comparison_cannot_reverse_change_directions(self):
        code = "".join(f"value_{n:03d} = {n}\n" for n in range(300))
        (self.root / "z.py").write_text(code)
        self.git("add", "z.py")
        self.git("commit", "-m", "Add long review fixture")
        self.git("update-ref", "refs/heads/main", "HEAD")
        (self.root / "z.py").write_text(code.replace("value_000 = 0", "value_000 = -1"))
        repo = quote(str(self.root), safe="/")
        path = quote(str(self.root / "z.py"), safe="/")
        self.loaded("/?review=" + repo + "&base=main&path=" + path)
        self.waitFor("#diffHost .view-line")
        self.browser.evaluate("""(() => {
          window.diff = monaco.editor.getDiffEditors()[0];
          diff.updateOptions({hideUnchangedRegions:{enabled:false}});
          for (const editor of [diff.getOriginalEditor(),diff.getModifiedEditor()])
            editor.setScrollTop(editor.getTopForLineNumber(130));
        })()""")
        self.browser.wait_for("diff.getOriginalEditor().getScrollTop() > 1000")
        self.browser.evaluate("""(async () => {
          const {api}=await import('/js/api.js'), status=api.gitStatus, read=api.gitDiff;
          window.diffReads=0; window.oldReleased=false; window.statusRevision=0;
          const gate=new Promise(resolve => { window.releaseOld=resolve; });
          api.gitStatus=async (...args)=>({...await status(...args),version:'review-'+(++statusRevision)});
          api.gitDiff=async (...args)=>{
            const body=await read(...args), count=++diffReads;
            // Model two observed base revisions without changing the working
            // file's stamp. Deliver the earlier comparison last.
            if (count===1) {
              body.old=body.old.replace('value_005 = 5','value_005 = 55');
              await gate; oldReleased=true;
            } else body.old=body.old.replace('value_290 = 290','value_290 = 999');
            return body;
          };
        })()""")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("diffReads === 1")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        lower = "document.querySelector('#scrollFileBottom').classList.contains('is-stale') && !document.querySelector('#scrollFileTop').classList.contains('is-stale')"
        self.browser.wait_for("diffReads >= 2 && " + lower)
        self.browser.evaluate("releaseOld()")
        self.browser.wait_for("oldReleased")
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertTrue(self.browser.evaluate(lower))
        self.assertTrue(self.browser.evaluate("diff.getOriginalEditor().getValue().includes('value_005 = 5\\n')"))
        self.assertNoPageErrors()

    def test_base_only_change_retries_a_failed_automatic_diff_read(self):
        repo = quote(str(self.root), safe="/")
        path = quote(str(self.root / "z.py"), safe="/")
        self.loaded("/?review=" + repo + "&base=main&path=" + path)
        original = "monaco.editor.getDiffEditors()[0].getOriginalEditor().getModel()"
        self.browser.wait_for(original + ".getValue().startsWith('value = 1\\n')")
        self.browser.click("#settingsToggle")
        self.browser.evaluate("""(() => {
          const row = [...document.querySelectorAll('#settings label')].find(
            item => item.querySelector('.set-label')?.textContent === 'Manual refresh');
          row.querySelector('input[type=checkbox]').click();
        })()""")
        self.browser.click("#settingsToggle")
        self.browser.evaluate("""(async () => {
          const {api} = await import('/js/api.js');
          const diff = api.gitDiff, watch = api.watch;
          window.baseChangeReads = 0; window.failedDiffRead = false;
          window.watchAfterFailure = 0; window.greenEvents = 0;
          api.gitDiff = async (...args) => {
            const body = await diff(...args);
            if (args[0] === '/z.py' && body.old?.startsWith('value = 2\\n')) {
              baseChangeReads++;
              // Detection succeeds; fail the automatic read that follows it.
              if (baseChangeReads === 2) {
                failedDiffRead = true;
                throw new Error('A temporary read failure');
              }
            }
            return body;
          };
          api.watch = async (...args) => {
            const body = await watch(...args);
            if (failedDiffRead) watchAfterFailure++;
            return body;
          };
          new MutationObserver(records => {
            greenEvents += records.filter(record => record.oldValue?.includes('is-stale') ||
              record.target.classList.contains('is-stale')).length;
          }).observe(document.querySelector('#fileActions'), {
            subtree:true, attributes:true, attributeFilter:['class'], attributeOldValue:true,
          });
        })()""")
        before = (self.root / "z.py").stat()
        self.git("update-ref", "refs/heads/main", self.sha)
        self.assertEqual((self.root / "z.py").stat(), before)
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("failedDiffRead", what="the automatic diff read to fail once")
        self.browser.wait_for(
            "watchAfterFailure > 0 && baseChangeReads >= 3 && "
            + original + ".getValue().startsWith('value = 2\\n')",
            timeout=10,
            what="the unchanged-file watcher to retry and refresh the diff's base",
        )
        # Reveal unchanged context so both panes have a nontrivial scroll range.
        self.browser.evaluate("""monaco.editor.getDiffEditors()[0].updateOptions({
          hideUnchangedRegions: {enabled:false},
        })""")
        editors = "[diff.getOriginalEditor(), diff.getModifiedEditor()]"
        self.browser.wait_for(
            "(() => { const diff = monaco.editor.getDiffEditors()[0]; return "
            + editors + ".every(editor => editor.getScrollHeight() > editor.getLayoutInfo().height + 20); })()"
        )
        for button, condition in (
            ("scrollFileBottom", "editor.getScrollHeight() - editor.getLayoutInfo().height - editor.getScrollTop() <= 2"),
            ("scrollFileTop", "editor.getScrollTop() <= 1"),
        ):
            self.browser.click("#" + button)
            self.browser.wait_for(
                "(() => { const diff = monaco.editor.getDiffEditors()[0]; return "
                + editors + ".every(editor => " + condition + "); })()",
                what="both diff panes to reach the requested edge",
            )
        self.assertEqual(self.browser.evaluate("greenEvents"), 0)
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#fileActions .is-stale')"))
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#tabs .tab.is-dirty')"))
        self.assertNoPageErrors()
