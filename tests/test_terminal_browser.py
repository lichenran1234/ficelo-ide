import base64
import os
import time
from unittest import mock

from test_browser import BrowserCase


class TerminalBrowserTest(BrowserCase):
    writable = True
    terminal = True

    def setUp(self):
        environment = mock.patch.dict(os.environ, {"SHELL": "/bin/sh", "ENV": "/dev/null", "HISTFILE": "/dev/null"})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()

    def test_terminal_executes_input_and_closing_tab_reaps_it(self):
        self.loaded("/")
        self.browser.click("#terminalButton")
        self.browser.wait_for("document.querySelector('.terminal-view')?.dataset.ready === 'true'")
        self.browser.wait_for("(async () => { const r = await fetch('/api/config'); return (await r.json()).terminal; })()")
        field = self.browser.evaluate("document.querySelector('.xterm-screen').getBoundingClientRect().toJSON()")
        self.assertGreater(field["height"], 0)
        self.browser.evaluate("document.querySelector('#search').focus()")
        self.browser.click('#tabs .tab[aria-selected="true"]')
        self.assertTrue(self.browser.evaluate("document.activeElement === document.querySelector('.xterm-helper-textarea')"))
        self.browser._call("Input.insertText", {"text": "printf 'UI_%s_END\\n' terminal"})
        self.browser._call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})
        self.browser._call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            sessions = list(self.server.terminals.sessions.values())
            if sessions and b"UI_terminal_END" in base64.b64decode(sessions[0].read()["data"]):
                break
            time.sleep(.03)
        else:
            self.fail("terminal input did not reach the shell")
        self.browser.click("#tabs .tab .close")
        self.browser.wait_for("document.querySelectorAll('#tabs .tab').length === 0")
        deadline = time.monotonic() + 3
        while self.server.terminals.sessions and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(self.server.terminals.sessions, {})
        self.assertNoPageErrors()

    def test_toggle_reuses_shell_and_shell_control_keys_stay_in_terminal(self):
        self.loaded("/")
        self.browser.click("#terminalButton")
        self.browser.wait_for("document.querySelector('.terminal-view')?.dataset.ready === 'true'")
        session_id = next(iter(self.server.terminals.sessions))
        self.browser.click("#terminalButton")
        self.assertTrue(self.browser.evaluate("document.querySelector('#viewerHost').hidden"))
        self.browser.click("#terminalButton")
        self.assertEqual(list(self.server.terminals.sessions), [session_id])
        self.assertEqual(self.browser.evaluate("document.querySelectorAll('#tabs .tab').length"), 1)
        captured = self.browser.evaluate("""(() => {
          const field = document.querySelector('.xterm-helper-textarea'); field.focus();
          const event = new KeyboardEvent('keydown',{key:'p',code:'KeyP',ctrlKey:true,bubbles:true,cancelable:true});
          document.dispatchEvent(new KeyboardEvent('keyup',{key:'p',code:'KeyP',ctrlKey:true}));
          field.dispatchEvent(event); return document.activeElement.id === 'pathInput';
        })()""")
        self.assertFalse(captured)
        self.assertNoPageErrors()

    def test_terminal_shortcut_from_focused_html_preview(self):
        (self.root / "page.html").write_text('<h1>Preview</h1><div style="height:3000px"></div>')
        self.loaded("/#/page.html")
        self.waitFor("iframe.html-preview")
        self.browser.click("#scrollFileBottom")
        self.browser.wait_for("(async () => (await import('/js/settings.js')).readScroll('/page.html')?.frac >= 0.999)()")
        self.browser.evaluate("document.querySelector('iframe.html-preview').focus()")
        for kind in ("keyDown", "keyUp"):
            self.browser._call("Input.dispatchKeyEvent", {
                "type": kind, "key": "`", "code": "Backquote", "modifiers": 2,
            })
        self.browser.wait_for("document.querySelector('.terminal-view')?.dataset.ready === 'true'")
        self.assertEqual(len(self.server.terminals.sessions), 1)
        self.assertNoPageErrors()
