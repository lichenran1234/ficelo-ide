# Mutations for ficelo's own files: settings.json, config.json, and the
# defaults that make a "/" root safe.
ST = "src/ficelo/state.py"
SV = "src/ficelo/server.py"
CL = "src/ficelo/cli.py"
CF = "src/ficelo/config.py"
UT = "src/ficelo/web/js/util.js"
BS = "tests.test_browser.SettingsOnServerTest"

CASES = [
    (ST, "keys are no longer restricted to the ficelo namespace",
     '    if not isinstance(key, str) or not KEY.match(key):', "    if not isinstance(key, str):",
     "tests.test_state"),
    (ST, "an oversized value is accepted",
     "        if len(encoded) > MAX_VALUE_BYTES:", "        if False:", "tests.test_state"),
    (ST, "the state folder is created world-readable",
     "    folder.mkdir(mode=0o700, parents=True, exist_ok=True)",
     "    folder.mkdir(mode=0o755, parents=True, exist_ok=True)", "tests.test_state"),
    (ST, "writes overwrite the file instead of merging one key",
     '            current = self._read_unlocked()\n            if value is None:', '            current = {}\n            if value is None:', "tests.test_state"),
    (ST, "the write lock is dropped",
     '        with self.lock:\n            current = self._read_unlocked()\n            if value is None:',
     '        if True:\n            current = self._read_unlocked()\n            if value is None:', "tests.test_state"),
    (ST, "reads stop waiting for a write in progress (reload reads stale settings)",
     "        with self.lock:\n            return self._read_unlocked()",
     "        return self._read_unlocked()", "tests.test_state"),
    (ST, "a corrupt settings file breaks the page",
     "        except ValueError:\n            # A hand-edit gone wrong should cost the settings, not the page.\n            return {}",
     "        except ValueError:\n            raise", "tests.test_state"),
    (ST, "an unknown config key is silently ignored",
     "        if key not in CONFIG_KEYS:", "        if False:", "tests.test_state"),
    (ST, "true is accepted as a port",
     "        if not isinstance(value, wanted) or (wanted is int and isinstance(value, bool)):",
     "        if not isinstance(value, wanted):", "tests.test_state"),
    (ST, "FICELO_HOME is ignored",
     '    override = os.environ.get("FICELO_HOME")', "    override = None", "tests.test_state"),
    (CL, "config.json is never read",
     "        saved = state.read_config(state.home())", "        saved = {}", "tests.test_state"),
    (CL, "config.json beats a command-line flag",
     "        if flag is not None:\n            return flag\n        return saved.get(key, default)",
     "        if key in saved:\n            return saved[key]\n        return flag if flag is not None else default",
     "tests.test_state"),
    (SV, "settings can only be saved on a writable server",
     "    def api_state_write(self):", "    def api_state_write(self):\n        self.require_writable()",
     "tests.test_server"),
    (CF, "the default root falls back to the home folder again",
     'DEFAULT_ROOT = "/"', 'DEFAULT_ROOT = str(Path.home())', "tests.test_cli"),
    (UT, "a one-off change is held back again (a quick reload loses it)",
     '    else send(key);', '    else timers.set(key, setTimeout(() => send(key), 1000));', BS),
    (UT, "writes stop using keepalive (navigating away cancels them)",
     '        keepalive: body.length < 60 * 1024 });', '        keepalive: false });', BS),
    (UT, "bursts are no longer merged",
     '    if (delay) timers.set(key, setTimeout(() => send(key), delay));',
     '    if (delay) send(key);', BS),
    (CL, "Windows is no longer refused",
     '    if sys.platform.startswith("win") or os.name == "nt":', "    if False:", "tests.test_cli"),
]
