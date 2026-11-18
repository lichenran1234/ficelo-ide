"""Repository review as a person uses it: one commit tab and live working files."""

import json
import os
import subprocess
from urllib.request import urlopen
from urllib.parse import quote

from test_browser import BrowserCase


class RepositoryReviewBrowserTest(BrowserCase):
    writable = True

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                              env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
                              check=True, timeout=30).stdout.strip()

    def prepare(self):
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        (self.root / "nested/deep").mkdir(parents=True)
        for name in ("nested/deep/a.py", "nested/b.py", "z.py"):
            (self.root / name).write_text("value = 1\n" + "# context\n" * 60)
        self.git("add", "."); self.git("commit", "-m", "base")
        self.git("checkout", "-b", "work")
        for name in ("nested/deep/a.py", "nested/b.py", "z.py"):
            (self.root / name).write_text("value = 2\n" + "# context\n" * 60)
        self.git("add", "."); self.git("commit", "-m", "Update all three files")
        self.sha = self.git("rev-parse", "HEAD")

    def context(self, expression):
        self.browser.evaluate("(" + expression + ").dispatchEvent(new MouseEvent('contextmenu', {bubbles:true,cancelable:true,button:2,clientX:100,clientY:140}))")

    def test_query_links_open_working_diffs_and_whole_commits(self):
        repo = quote(str(self.root), safe="/")
        path = quote(str(self.root / "z.py"), safe="/")
        self.loaded("/?review=" + repo + "&base=main&path=" + path)
        self.waitFor("#diffHost .view-line", count=4)
        self.assertEqual(self.browser.evaluate("[new URL(location).searchParams.get('review'),new URL(location).searchParams.get('base'),new URL(location).searchParams.get('path'),location.hash]"), [str(self.root), "main", str(self.root / "z.py"), ""])
        self.loaded("/?review=" + repo + "&base=main&sha=" + self.sha)
        self.waitFor(".commit-file", count=3)
        self.assertFalse(self.browser.evaluate("new URL(location).searchParams.has('path')"))
        self.assertEqual(self.browser.evaluate("new URL(location).searchParams.get('sha')"), self.sha)
        self.loaded("/#commit/" + self.sha + "/z.py")
        self.waitFor("#diffHost .view-line", count=4)
        self.assertEqual(self.browser.evaluate("[new URL(location).searchParams.get('path'),new URL(location).searchParams.get('sha'),location.hash]"), [str(self.root / "z.py"), self.sha, ""])
        self.assertNoPageErrors()

    def test_review_tabs_keep_their_position_on_every_frame(self):
        for name in ("nested/deep/a.py", "nested/b.py", "z.py"):
            (self.root / name).write_text("changed = 3\n" * 300)
        self.git("add", ".")
        self.git("commit", "-m", "Long changed files")
        self.sha = self.git("rev-parse", "HEAD")
        for whole in (False, True):
            with self.subTest(whole_commit=whole):
                route = "/?review=" + quote(str(self.root), safe="/") + "&sha=" + self.sha
                if not whole:
                    route += "&path=" + quote(str(self.root / "z.py"), safe="/")
                self.loaded(route)
                self.waitFor(".commit-document .view-line" if whole else "#diffHost .view-line", count=4)
                node = "document.querySelector('#viewerHost .commit-document')" if whole else "monaco.editor.getDiffEditors()[0].getModifiedEditor()"
                position = node + (".scrollTop" if whole else ".getScrollTop()")
                self.browser.evaluate(node + (".scrollTop = 240" if whole else ".setScrollTop(240)"))
                self.browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                wanted = self.browser.evaluate(position)
                self.assertGreater(wanted, 0)
                self.browser.evaluate("location.hash = '#/z.py'")
                self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.id === '/z.py'")
                samples = self.browser.evaluate("""(async () => {
                  document.querySelector('#tabs .is-commit').click();
                  const positions = [""" + position + """];
                  for (let i = 0; i < 8; i++) {
                    await new Promise(requestAnimationFrame);
                    positions.push(""" + position + """);
                  }
                  return positions;
                })()""")
                self.assertEqual(samples, [wanted] * 9)
        self.assertNoPageErrors()

    def test_repository_query_overrides_a_saved_diff_and_clears_its_selection(self):
        other = self.root / "other"
        other.mkdir()
        self.git("-C", str(other), "init", "--initial-branch=main")
        self.git("-C", str(other), "config", "user.email", "test@example.invalid")
        self.git("-C", str(other), "config", "user.name", "Test")
        self.git("-C", str(other), "config", "commit.gpgsign", "false")
        (other / "another.py").write_text("value = 3\n")
        self.git("-C", str(other), "add", ".")
        self.git("-C", str(other), "commit", "-m", "base")
        self.loaded("/#diff/z.py")
        self.waitFor("#diffHost .view-line", count=4)
        self.browser.evaluate("(async () => (await import('/js/util.js')).flushStore())()")
        original = self.browser.evaluate("location.pathname + location.search")
        session = self.browser.evaluate("new URL(location).searchParams.get('session')")
        target = "/?session=" + quote(session) + "&review=" + quote(str(other), safe="/")
        self.loaded(target + "&base=main")
        self.browser.wait_for("document.querySelector('.review-repo-button')?.textContent === 'other ▾'")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#tabs [aria-selected=true]')"))
        self.assertEqual(self.browser.evaluate("new URL(location).searchParams.get('review')"), str(other))

        self.loaded(original)
        self.waitFor("#diffHost .view-line", count=4)
        self.browser.evaluate("history.pushState(null, '', " + json.dumps(target + "&base=HEAD") + "); window.dispatchEvent(new PopStateEvent('popstate'))")
        self.browser.wait_for("document.querySelector('.review-repo-button')?.textContent === 'other ▾'")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('#tabs [aria-selected=true]')"))
        self.assertEqual(self.browser.evaluate("[new URL(location).searchParams.get('review'),new URL(location).searchParams.get('base')]"), [str(other), "HEAD"])
        self.assertNoPageErrors()

    def context_labels(self):
        return self.browser.evaluate("[...document.querySelector('.menu').children].map(item => item.classList.contains('menu-divider') ? '|' : item.textContent)")

    def dismiss_context(self):
        self.browser._call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        self.browser._call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})

    def test_review_row_menus_and_deleted_whole_file_follow_files_navigation(self):
        self.loaded("/#review")
        self.waitFor(".review-file", count=3)
        self.context("document.querySelector('.review-file')")
        self.assertEqual(self.context_labels(), ["Copy Path in Repo", "Copy Full Path", "Copy File Name", "|", "Open Whole File in Files"])
        self.context("document.querySelector('.change-group')")
        self.assertEqual(self.context_labels(), ["Copy Path in Repo", "Copy Full Path", "Copy Folder Name"])
        self.dismiss_context()
        self.context("document.querySelector('.review-repo-button')")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"))
        (self.root / "z.py").unlink()
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("document.querySelector('.review-file[data-review-path=\"/z.py\"] .is-deleted')")
        self.context("document.querySelector('.review-file[data-review-path=\"/z.py\"]')")
        self.assertEqual(self.context_labels(), ["Copy Path in Repo", "Copy Full Path", "Copy File Name", "|", "Open Whole File in Files"])
        self.browser.evaluate("[...document.querySelectorAll('.menu-item')].find(item => item.textContent === 'Open Whole File in Files').click()")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected=true]')?.dataset.path === '/z.py' && document.querySelector('#notice').textContent.includes('no such file')")
        self.assertEqual(self.browser.evaluate("document.querySelector('#modeSwitch [data-mode=files]').getAttribute('aria-pressed')"), "true")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"))

    def test_unselected_working_and_historical_diff_surfaces_offer_document_actions(self):
        self.loaded("/#diff/nested/deep/a.py")
        self.waitFor("#diffHost .view-line", count=4)
        copies = ["Copy Path in Repo", "Copy Full Path", "Copy File Name"]
        for historical in (False, True):
            if historical:
                self.loaded("/#commit/" + self.sha + "/nested/deep/a.py")
                self.browser.wait_for("monaco.editor.getDiffEditors()[0].getModifiedEditor().getOption(monaco.editor.EditorOption.readOnly)")
            expected = [*copies, "|", "Close Tab", "Close to the Right", "Close All " + ("2" if historical else "1"), "|", "Open Whole File in Files"]
            for side in ("getOriginalEditor", "getModifiedEditor"):
                self.browser.evaluate("window.clickedEditor = monaco.editor.getDiffEditors()[0]." + side + "(); clickedEditor.setPosition({lineNumber:1,column:1})")
                self.context("clickedEditor.getDomNode().querySelector('.view-line')")
                self.assertEqual(self.context_labels(), expected)
                self.dismiss_context()
                self.browser.evaluate("clickedEditor.setSelection(new monaco.Selection(1,1,1,6))")
                self.context("clickedEditor.getDomNode().querySelector('.view-line')")
                self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"), "selected diff text keeps Monaco's menu")
                self.dismiss_context()
                self.browser.evaluate("clickedEditor.setPosition({lineNumber:1,column:1})")
            self.context("document.querySelector('.review-diff-header')")
            self.assertEqual(self.context_labels(), expected)
            self.dismiss_context()
        self.context("document.querySelector('#tabs .tab[aria-selected=true]')")
        self.assertEqual(self.context_labels(), [*copies, "|", "Move Left", "Move Right", "|", "Close Tab", "Close to the Right", "Close All 2", "|", "Open Whole File in Files"])
        self.browser.evaluate("[...document.querySelectorAll('.menu-item')].find(item => item.textContent === 'Open Whole File in Files').click()")
        self.browser.wait_for("document.querySelector('#tabs .tab[aria-selected=true]')?.dataset.id === '/nested/deep/a.py'")
        self.assertEqual(self.browser.evaluate("document.querySelector('#modeSwitch [data-mode=files]').getAttribute('aria-pressed')"), "true")
        self.browser.wait_for("document.querySelector('#tree [aria-selected=true]')?.dataset.path === '/nested/deep/a.py'", what="the working file to be revealed in Files")
        self.assertNoPageErrors()

    def test_commit_document_menus_use_commit_identity_and_preserve_selected_text(self):
        self.open_history()
        self.browser.click(".review-commit-row .commit")
        self.waitFor(".commit-document .view-line", count=4)
        expected = ["Copy Full Path", "Copy File Name", "|", "Close Tab", "Close to the Right", "Close All 1"]
        for selector in (".commit-banner", ".commit-file-heading-row", ".commit-file-content", "#viewerHost"):
            self.context("document.querySelector(" + json.dumps(selector) + ")")
            self.assertEqual(self.context_labels(), expected)
        self.dismiss_context()
        self.browser.evaluate("window.clickedEditor = monaco.editor.getDiffEditors()[0].getModifiedEditor(); clickedEditor.setPosition({lineNumber:1,column:1})")
        self.context("clickedEditor.getDomNode().querySelector('.view-line')")
        self.assertEqual(self.context_labels(), expected)
        self.dismiss_context()
        self.browser.evaluate("clickedEditor.setSelection(new monaco.Selection(1,1,1,6))")
        self.context("clickedEditor.getDomNode().querySelector('.view-line')")
        self.assertFalse(self.browser.evaluate("!!document.querySelector('.menu')"))
        self.dismiss_context()
        self.context("document.querySelector('.tab.is-commit')")
        self.assertEqual(self.context_labels(), ["Copy Full Path", "Copy File Name", "|", "Move Left", "Move Right", "|", "Close Tab", "Close to the Right", "Close All 1"])
        self.assertNoPageErrors()

    def open_history(self):
        self.loaded("/#review")
        self.waitFor(".review-switch")
        self.browser.evaluate("[...document.querySelectorAll('.review-switch button')].find(button => button.textContent === 'Commits').click()")
        self.waitFor(".review-commit-row .commit")

    def test_whole_commit_matches_tree_order_and_reuses_one_tab(self):
        self.open_history()
        self.browser.click(".review-commit-row .commit")
        self.waitFor(".commit-file", count=3)
        self.waitFor(".commit-document .view-line", count=4)
        self.assertTrue(self.browser.evaluate("monaco.editor.getDiffEditors().every(diff => [diff.getOriginalEditor(), diff.getModifiedEditor()].every(editor => editor.getOption(monaco.editor.EditorOption.readOnly)))"))
        self.assertEqual(self.browser.evaluate("[...document.querySelectorAll('.commit-file-name')].map(node => node.textContent)"), ["nested/deep/a.py", "nested/b.py", "z.py"])
        self.browser.evaluate("window.originalSection = document.querySelector('.commit-file')")
        self.browser.click('.review-file[data-review-path="/z.py"]')
        self.browser.wait_for("document.querySelector('.review-file[data-review-path=\"/z.py\"]').classList.contains('is-active')")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.tab.is-commit').length"), 1)
        self.assertTrue(self.browser.evaluate("window.originalSection === document.querySelector('.commit-file')"))
        self.browser.click('.review-file[data-review-path="/nested/deep/a.py"]')
        self.browser.wait_for("document.querySelector('.commit-document').scrollTop === 0")
        self.loaded("/#commit/" + self.sha + "/z.py")
        self.waitFor("#diffHost .view-line", count=4)
        self.assertTrue(self.browser.evaluate("monaco.editor.getDiffEditors().every(diff => [diff.getOriginalEditor(), diff.getModifiedEditor()].every(editor => editor.getOption(monaco.editor.EditorOption.readOnly)))"))
        self.assertNoPageErrors()

    def test_hidden_commit_does_not_create_editors_or_reset_the_current_file_theme(self):
        self.open_history()
        colors = json.dumps({str(self.root / "z.py"): "#e8e8e8"})
        self.browser.evaluate("""(async()=>{
          const {store}=await import('/js/util.js');store.set('ficelo.filebackgrounds',""" + colors + """);
          window.heldIntersections=[];window.deferIntersections=true;
          const Original=window.IntersectionObserver;
          window.IntersectionObserver=class extends Original {
            constructor(callback,options) {
              super((entries,observer)=>{
                if(deferIntersections)heldIntersections.push(()=>callback(entries,observer));
                else callback(entries,observer);
              },options);
            }
          };
        })()""")
        self.browser.click(".review-commit-row .commit")
        self.browser.wait_for("heldIntersections.length>0")
        self.browser.evaluate("location.hash='#/z.py'")
        self.browser.wait_for("document.querySelector('#tabs [aria-selected]')?.dataset.path==='/z.py'")
        background = "getComputedStyle(document.querySelector('#editorHost .monaco-editor-background')).backgroundColor"
        self.assertEqual(self.browser.evaluate(background), "rgb(232, 232, 232)")
        self.browser.evaluate("heldIntersections.splice(0).forEach(deliver=>deliver())")
        self.assertEqual(self.browser.evaluate("monaco.editor.getDiffEditors().length"), 0)
        self.assertEqual(self.browser.evaluate(background), "rgb(232, 232, 232)")

        # Becoming visible again must deliver a fresh intersection and
        # create the diff editors, rather than leaving blank sections.
        self.browser.evaluate("window.deferIntersections=false")
        self.browser.click("#tabs .is-commit")
        self.waitFor(".commit-document .view-line", count=4)
        self.assertEqual(self.browser.evaluate(background), "rgb(255, 255, 255)")
        self.browser.click('#tabs [data-path="/z.py"]')
        self.assertEqual(self.browser.evaluate(background), "rgb(232, 232, 232)")
        self.assertNoPageErrors()

    def test_commit_folding_layout_and_workspace_restore(self):
        self.open_history(); self.browser.click(".review-commit-row .commit")
        self.waitFor(".commit-file", count=3)
        self.browser.click(".commit-file-heading")
        self.assertTrue(self.browser.evaluate("document.querySelector('.commit-file-content').hidden"))
        self.browser.click('#diffLayout [data-layout="inline"]')
        self.browser.evaluate("window.dispatchEvent(new Event('pagehide'))")
        route = self.browser.evaluate("location.pathname + location.search")
        self.loaded(route)
        self.waitFor(".commit-file", count=3)
        self.assertTrue(self.browser.evaluate("document.querySelector('.commit-file-content').hidden"))
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.tab.is-commit').length"), 1)
        self.assertNoPageErrors()

    def test_working_diff_edits_and_saves_without_a_permission_toggle(self):
        self.loaded("/#diff/z.py")
        self.waitFor("#diffHost .view-line", count=4)
        self.assertEqual(self.browser.evaluate("""(() => {
          const diff = monaco.editor.getDiffEditors()[0];
          return [diff.getOriginalEditor(), diff.getModifiedEditor()].map(editor =>
            editor.getOption(monaco.editor.EditorOption.readOnly));
        })()"""), [True, False])
        self.browser.evaluate("""(() => { const editor = monaco.editor.getDiffEditors()[0].getModifiedEditor();
          editor.setPosition({lineNumber: 1, column: 1}); editor.focus(); })()""")
        self.browser._call("Input.insertText", {"text": "# typed change\n"})
        self.waitFor(".tab.is-dirty")
        self.browser.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'s',ctrlKey:true,bubbles:true}))")
        self.browser.wait_for("!document.querySelector('.tab.is-dirty') && document.querySelector('#flash').textContent.startsWith('Saved ')")
        self.assertTrue((self.root / "z.py").read_text().startswith("# typed change\nvalue = 2\n"))
        self.assertNoPageErrors()

    def test_working_diff_waits_for_manual_refresh_and_keeps_dirty_buffer(self):
        self.loaded("/#diff/z.py")
        self.waitFor("#diffHost .view-line", count=4)
        (self.root / "z.py").write_text("value = 300\n" + "# context\n" * 60)
        self.waitFor("#reloadFile.is-stale")
        (self.root / "first-refresh.txt").write_text("status changed\n")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.waitFor('.review-file[data-review-path="/first-refresh.txt"]')
        self.assertFalse(self.browser.evaluate("document.querySelector('#diffHost').textContent.includes('300')"))
        self.browser.click("#reloadFile")
        self.browser.wait_for("document.querySelector('#diffHost').textContent.includes('300')")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.browser.evaluate("window.modified = monaco.editor.getModels().find(model => model.uri.scheme === 'ficelo-new'); window.modified.setValue('local unsaved\\n')")
        (self.root / "z.py").write_text("external write\n")
        (self.root / "second-refresh.txt").write_text("status changed again\n")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.waitFor('.review-file[data-review-path="/second-refresh.txt"]')
        self.assertEqual(self.browser.evaluate("window.modified.getValue()"), "local unsaved\n")
        self.waitFor("#reloadFile.is-stale")
        self.browser.click("#reloadFile")
        self.waitFor("dialog[open]")
        self.browser.click('dialog[open] button[type="button"]')
        self.assertEqual(self.browser.evaluate("window.modified.getValue()"), "local unsaved\n")
        self.assertTrue(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.browser.click("#reloadFile")
        self.waitFor("dialog[open]")
        self.browser.click('dialog[open] button[type="submit"]')
        self.browser.wait_for("window.modified.getValue()==='external write\\n'")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.assertNoPageErrors()

    def test_refresh_checks_track_each_buffer_and_ignore_superseded_replies(self):
        self.loaded("/#/z.py")
        self.waitFor("#editorHost .view-line")
        self.browser.evaluate("""(() => {
          const original=window.fetch;window.watchReplies=0;window.holdWatch=true;
          const gate=new Promise(resolve=>window.releaseWatch=()=>{holdWatch=false;resolve();});
          window.fetch=async(url,options)=>{
            if(String(url).includes('/api/watch') && holdWatch)await gate;
            const response=await original(url,options);
            if(String(url).includes('/api/watch'))watchReplies++;
            return response;
          };
        })()""")
        (self.root / "z.py").write_text("value = 300\n" + "# context\n" * 60)
        self.browser.click('#modeSwitch [data-mode="review"]')
        self.waitFor('.review-file[data-review-path="/z.py"]')
        self.browser.click('.review-file[data-review-path="/z.py"]')
        self.browser.wait_for("document.querySelector('#diffHost').textContent.includes('300')")
        self.browser.evaluate("releaseWatch()")
        self.browser.wait_for("watchReplies>=2")
        self.browser.click('#tabs .tab[data-id="/z.py"]')
        self.waitFor("#reloadFile.is-stale")
        self.assertTrue(self.browser.evaluate("monaco.editor.getModels().find(m=>m.uri.scheme==='ficelo').getValue().startsWith('value = 2\\n')"))

        self.browser.click("#tabs .tab.is-diff")
        self.browser.evaluate("""(async()=>{
          const {api}=await import('/js/api.js'),original=api.gitDiff;
          let hold=true;window.diffHeld=false;window.diffReturned=false;
          const gate=new Promise(resolve=>window.releaseDiff=resolve);
          api.gitDiff=async(...args)=>{
            const body=await original(...args);
            if(hold){hold=false;diffHeld=true;await gate;diffReturned=true;}
            return body;
          };
        })()""")
        (self.root / "status-trigger.txt").write_text("Refresh the repository status\n")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("diffHeld")
        (self.root / "z.py").write_text("value = 400\n" + "# context\n" * 60)
        self.browser.click("#reloadFile")
        self.browser.wait_for("document.querySelector('#diffHost').textContent.includes('400') && !document.querySelector('#reloadFile').classList.contains('is-stale')")
        self.browser.evaluate("releaseDiff()")
        self.browser.wait_for("diffReturned")
        self.browser.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        self.assertFalse(self.browser.evaluate("document.querySelector('#reloadFile').classList.contains('is-stale')"))
        self.assertTrue(self.browser.evaluate("monaco.editor.getModels().find(m=>m.uri.scheme==='ficelo-new').getValue().startsWith('value = 400\\n')"))
        self.assertNoPageErrors()

    def test_repeated_commit_clicks_share_a_pending_load(self):
        self.open_history()
        self.browser.evaluate("(() => { const original = window.fetch; window.fetch = (url, options) => String(url).includes('/api/git/commit?') ? new Promise(resolve => setTimeout(() => resolve(original(url, options)), 250)) : original(url, options); const row = document.querySelector('.review-commit-row .commit'); row.click(); row.click(); })()")
        self.waitFor(".commit-file", count=3)
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.tab.is-commit').length"), 1)
        self.assertNoPageErrors()

    def test_pending_commit_cannot_repopulate_a_new_workspace(self):
        self.open_history()
        self.browser.evaluate("(() => { const original = window.fetch; window.fetch = (url, options) => String(url).includes('/api/git/commit?') ? new Promise(resolve => { window.releaseCommit = () => original(url, options).then(response => { resolve(response); window.commitResponded = true; }); }) : original(url, options); document.querySelector('.review-commit-row .commit').click(); })()")
        self.browser.wait_for("typeof window.releaseCommit === 'function'")
        previous_session = self.browser.evaluate("new URL(location.href).searchParams.get('session')")
        self.browser.click("#newSession")
        self.browser.wait_for("new URL(location.href).searchParams.get('session') !== " + json.dumps(previous_session), timeout=10)
        self.browser.evaluate("window.releaseCommit()")
        self.browser.wait_for("window.commitResponded === true")
        self.browser.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.tab.is-commit').length"), 0)
        self.assertNoPageErrors()

    def test_an_invalid_base_can_be_corrected_in_place(self):
        self.loaded("/#review")
        self.waitFor(".review-base-input")
        self.browser.evaluate("(() => { const input = document.querySelector('.review-base-input'); input.value = 'missing-base'; input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()")
        self.waitFor("#review .is-error")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.review-base-input').length"), 1)
        self.browser.evaluate("(() => { const input = document.querySelector('.review-base-input'); input.value = 'main'; input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()")
        self.waitFor(".review-switch")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#review .is-error').length"), 0)

    def test_repository_and_base_are_explicit_endpoint_context(self):
        with urlopen(self.url("/api/git/repos?scope=/nested")) as response:
            repositories = json.load(response)["repositories"]
        self.assertEqual(repositories[0]["path"], "/")
        with urlopen(self.url("/api/git/commits?repo=/nested&base=main")) as response:
            commits = json.load(response)["commits"]
        self.assertEqual([entry["sha"] for entry in commits], [self.sha])
        with urlopen(self.url("/api/git/commit?repo=/&full=1&sha=" + self.sha)) as response:
            commit = json.load(response)
        self.assertEqual(len(commit["files"]), 3)
        self.assertEqual(commit["files"][0]["old"].splitlines()[0], "value = 1")

    def copy_from_menu(self, selector, label):
        self.browser.evaluate("window.copied = null; document.querySelector(" + json.dumps(selector) + ").dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true, clientX:100, clientY:100}))")
        self.browser.evaluate("[...document.querySelectorAll('.menu-item')].find(item => item.textContent === " + json.dumps(label) + ").click()")
        return self.browser.wait_for("window.copied !== null && window.copied")

    def test_review_rows_headers_and_tabs_copy_repository_paths(self):
        self.open_history()
        self.browser.evaluate("Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text => { window.copied = text; }}, configurable:true})")
        row = '.review-file[data-review-path="/nested/deep/a.py"]'
        self.assertEqual(self.copy_from_menu(row, "Copy Path in Repo"), "nested/deep/a.py")
        self.assertEqual(self.copy_from_menu(row, "Copy Full Path"), str(self.root / "nested/deep/a.py"))
        self.assertEqual(self.copy_from_menu(row, "Copy File Name"), "a.py")
        self.assertEqual(self.copy_from_menu(".change-group", "Copy Folder Name"), "nested")
        self.browser.click(row)
        self.waitFor(".commit-file-heading-row", count=3)
        self.browser.evaluate("window.copied = null")
        self.browser.click(".commit-file-heading-row .review-copy-path")
        self.assertEqual(self.browser.wait_for("window.copied"), "nested/deep/a.py")
        self.assertEqual(self.copy_from_menu(".commit-file-heading-row", "Copy Full Path"), str(self.root))
        self.assertEqual(self.copy_from_menu(".tab.is-commit", "Copy Full Path"), str(self.root))
        self.assertEqual(self.copy_from_menu(".tab.is-commit", "Copy File Name"), self.git("rev-parse", "--short", self.sha))
        self.browser.evaluate("[...document.querySelectorAll('.review-switch button')].find(button => button.textContent === 'Files').click()")
        self.browser.click(row)
        self.waitFor(".review-diff-header")
        self.waitFor("#diffHost .view-line", count=4)
        self.browser.evaluate("window.copied = null")
        self.browser.click(".review-diff-header .review-copy-path")
        self.assertEqual(self.browser.wait_for("window.copied"), "nested/deep/a.py")
        self.assertEqual(self.copy_from_menu(".review-diff-header", "Copy File Name"), "a.py")
        self.assertEqual(self.copy_from_menu(".tab.is-diff", "Copy Full Path"), str(self.root / "nested/deep/a.py"))
        self.assertTrue(self.browser.evaluate("document.querySelector('.review-diff-canvas').getBoundingClientRect().top >= document.querySelector('.review-diff-header').getBoundingClientRect().bottom"))
        self.assertNoPageErrors()

    def test_repository_picker_dismisses_pending_results_and_worktree_selection_roots_tree(self):
        self.git("worktree", "add", "-b", "alternate", str(self.root / "alternate"), "main")
        self.loaded("/#review")
        self.waitFor(".review-worktrees")
        self.browser.evaluate("(() => { const original = window.fetch; window.fetch = (url, options) => String(url).includes('/api/git/repos?') ? new Promise(resolve => { window.releaseRepositories = () => original(url, options).then(response => { resolve(response); window.repositoriesResponded = true; }); }) : original(url, options); })()")
        self.browser.click(".review-repo-button")
        self.browser.wait_for("typeof window.releaseRepositories === 'function'")
        self.browser.click(".review-stat")
        self.assertTrue(self.browser.evaluate("document.querySelector('.review-picker').hidden"))
        self.browser.evaluate("window.releaseRepositories()")
        self.browser.wait_for("window.repositoriesResponded === true")
        self.browser.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
        self.assertEqual(self.browser.evaluate("document.querySelector('.review-repositories').childElementCount"), 0)
        self.browser.evaluate("(() => { const select = document.querySelector('.review-worktrees'); select.value = '/alternate'; select.dispatchEvent(new Event('change')); })()")
        self.browser.wait_for("document.querySelector('.review-branch')?.textContent === 'alternate' && document.querySelector('#tree .row')?.dataset.path?.startsWith('/alternate/')", timeout=10)
        self.assertTrue(self.browser.evaluate("document.querySelector('#review').getClientRects().length > 0"))
        self.browser.evaluate("window.dispatchEvent(new Event('pagehide'))")
        self.loaded(self.browser.evaluate("location.pathname + location.search"))
        self.browser.wait_for("document.querySelector('.review-branch')?.textContent === 'alternate'")
        self.assertTrue(self.browser.evaluate("[...document.querySelectorAll('#tree .row')].every(row => row.dataset.path.startsWith('/alternate/'))"))
        self.assertNoPageErrors()

    def test_review_keys_navigate_files_without_stealing_input(self):
        self.loaded("/#review")
        self.waitFor(".review-file", count=3)
        self.browser.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'j', bubbles:true, cancelable:true}))")
        self.waitFor('.tab.is-diff[data-path="/nested/deep/a.py"]')
        self.browser.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'j', bubbles:true, cancelable:true}))")
        self.waitFor('.tab[aria-selected="true"][data-path="/nested/b.py"]')
        self.browser.evaluate("document.querySelector('.review-base-input').dispatchEvent(new KeyboardEvent('keydown', {key:'k', bubbles:true, cancelable:true}))")
        self.assertEqual(self.browser.evaluate("document.querySelector('.review-file.is-active').dataset.reviewPath"), "/nested/b.py")
        self.browser.evaluate("document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'k', bubbles:true, cancelable:true}))")
        self.waitFor('.tab[aria-selected="true"][data-path="/nested/deep/a.py"]')
        (self.root / "chain/one/two").mkdir(parents=True)
        (self.root / "chain/one/two/new.py").write_text("new = 1\n")
        self.browser.evaluate("window.dispatchEvent(new Event('focus'))")
        self.browser.wait_for("[...document.querySelectorAll('.group-name')].some(node => node.textContent === 'chain/one/two')")
        self.browser.evaluate("[...document.querySelectorAll('.group-name')].find(node => node.textContent === 'chain/one/two').click()")
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('.review-file[data-review-path=\"/chain/one/two/new.py\"]').length"), 0)
        self.assertNoPageErrors()

    def test_whole_commit_layout_switch_preserves_the_reading_section(self):
        for name in ("nested/deep/a.py", "nested/b.py", "z.py"):
            (self.root / name).write_text("value = 3\n" + "# replaced\n" * 60)
        self.git("add", "."); self.git("commit", "-m", "Replace each complete file")
        self.open_history(); self.browser.click(".review-commit-row .commit")
        self.waitFor(".commit-file", count=3)
        self.browser.click('.review-file[data-review-path="/z.py"]')
        self.browser.wait_for("monaco.editor.getDiffEditors().length === 3 && monaco.editor.getDiffEditors().every(editor => editor.getLineChanges()?.length)")
        self.browser.click('.review-file[data-review-path="/z.py"]')
        self.browser.evaluate("window.headingTop = document.querySelector('.commit-file[data-path=\"/z.py\"]').getBoundingClientRect().top; window.firstHeight = document.querySelector('.commit-file-content').offsetHeight")
        self.browser.click('#diffLayout [data-layout="inline"]')
        self.browser.wait_for("document.querySelector('.commit-file-content').offsetHeight > window.firstHeight * 1.5 && !document.querySelector('.commit-document .side-by-side')")
        self.browser.wait_for("Math.abs(document.querySelector('.commit-file[data-path=\"/z.py\"]').getBoundingClientRect().top - window.headingTop) < 2")
        self.assertEqual(self.browser.evaluate("document.querySelector('.review-file.is-active').dataset.reviewPath"), "/z.py")
        self.browser.click('#diffLayout [data-layout="side"]')
        self.browser.wait_for("document.querySelector('.commit-file-content').offsetHeight < window.firstHeight * 1.2 && document.querySelectorAll('.commit-document .side-by-side').length === 3")
        self.browser.wait_for("Math.abs(document.querySelector('.commit-file[data-path=\"/z.py\"]').getBoundingClientRect().top - window.headingTop) < 2")
        self.assertNoPageErrors()
