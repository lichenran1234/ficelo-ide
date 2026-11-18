"""A very small Chrome DevTools Protocol client, for the browser tests.

Why this exists rather than `--dump-dom --virtual-time-budget`: virtual time
advances timers but **never fires requestAnimationFrame**, and Monaco renders
its text entirely inside rAF. Under virtual time the editor reports a correct
size and zero rendered lines, so every test of what the editor actually shows
passes or fails for reasons unrelated to the code. Measured, not assumed —
a probe reported `lines=0 raf=0` under four different flag combinations.

So: drive a real browser in real time, wait for a real condition, and read
the DOM once it is true. Roughly a hundred lines of WebSocket, and no
dependency — which is the whole point of this project.

Only what the tests need: navigate, evaluate, wait-for, click, read the DOM,
and collect console messages.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

def find_chrome():
    """A Chromium-family browser, wherever this platform keeps one.

    On macOS the browser lives inside an application bundle rather than on
    PATH, so looking only at PATH finds nothing there — and the browser tests
    would then skip, silently shrinking the suite. FICELO_CHROME overrides.
    """
    override = os.environ.get("FICELO_CHROME")
    if override:
        # A path that is not there means "no browser", so the browser tests
        # skip (and --strict can catch that) rather than all fail to launch.
        return override if os.path.exists(override) else None
    for name in ("chromium-browser", "chromium", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    for bundle in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if os.path.exists(bundle):
            return bundle
    return None


CHROME = find_chrome()


class Timeout(Exception):
    pass


class WebSocket:
    """Enough of RFC 6455 to talk to DevTools on loopback.

    No extensions, no continuation frames on send, no TLS: the peer is a
    browser this process just started on 127.0.0.1.
    """

    def __init__(self, url):
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                "GET /%s HTTP/1.1\r\n"
                "Host: %s\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: %s\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
            ).encode()
        )
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("the browser closed the connection during the handshake")
            self.buffer += chunk
        head, _, rest_bytes = self.buffer.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise IOError("websocket upgrade refused: %r" % head[:200])
        self.buffer = rest_bytes

    def _recv_exact(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise IOError("the browser closed the connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def send(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text frame
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        """One text message, reassembled across continuation frames."""
        chunks = []
        while True:
            first, second = self._recv_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            # The server never masks.
            data = self._recv_exact(length) if length else b""
            if opcode == 0x8:
                raise IOError("the browser closed the websocket")
            if opcode == 0x9:  # ping
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0xA:  # pong
                continue
            chunks.append(data)
            if final:
                return b"".join(chunks).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Browser:
    """A headless Chromium, driven over CDP."""

    def __init__(self, extra_args=(), window="1280,900"):
        if not CHROME:
            raise RuntimeError("no Chromium")
        self.profile = tempfile.mkdtemp()
        self.process = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-features=CalculateNativeWinOcclusion",
                "--user-data-dir=" + self.profile,
                "--window-size=" + window,
                # Port 0: Chromium picks a free one and writes it to
                # DevToolsActivePort in the profile. Picking one here by
                # binding and releasing it races when many browsers start at
                # once, which is exactly what the parallel runner does.
                "--remote-debugging-port=0",
                "about:blank",
            ]
            + list(extra_args),
            stdout=subprocess.DEVNULL,
            # Not PIPE: nothing reads it, and once Chromium has logged about
            # 64 KB it blocks on the full pipe and the test hangs.
            stderr=subprocess.DEVNULL,
        )
        self.port = self._debugging_port()
        self.next_id = 0
        self.console = []
        self.ws = WebSocket(self._page_target())
        self._call("Runtime.enable")
        self._call("Page.enable")
        self._call("Log.enable")

    def _debugging_port(self, deadline=30):
        """The port Chromium chose, read from the file it writes on startup."""
        marker = os.path.join(self.profile, "DevToolsActivePort")
        end = time.time() + deadline
        while time.time() < end:
            if self.process.poll() is not None:
                raise RuntimeError("Chromium exited during startup (code %s)" % self.process.returncode)
            try:
                with open(marker) as handle:
                    first = handle.readline().strip()
                if first.isdigit():
                    return int(first)
            except OSError:
                pass
            time.sleep(0.05)
        raise Timeout("Chromium never reported its debugging port")

    def _page_target(self, deadline=30):
        end = time.time() + deadline
        last = None
        while time.time() < end:
            try:
                # Direct, never through a proxy from the environment.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open("http://127.0.0.1:%d/json/list" % self.port, timeout=2) as res:
                    targets = json.load(res)
                for target in targets:
                    if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                        return target["webSocketDebuggerUrl"]
            except Exception as err:  # noqa: BLE001 - the browser is still starting
                last = err
            time.sleep(0.1)
        raise Timeout("the browser never offered a page target (%s)" % last)

    def _call(self, method, params=None, timeout=30):
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            self.sock_timeout(max(0.5, end - time.time()))
            raw = self.ws.recv()
            message = json.loads(raw)
            if message.get("method") in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown", "Log.entryAdded"):
                self.console.append(raw)
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError("%s: %s" % (method, message["error"]))
                return message.get("result", {})
            # Any other event is not interesting to these tests.
        raise Timeout("no reply to %s" % method)

    def sock_timeout(self, seconds):
        self.ws.sock.settimeout(seconds)

    def navigate(self, url):
        self._call("Page.navigate", {"url": url})

    def evaluate(self, expression, timeout=30):
        """Evaluate JS and return the value, awaiting a promise if given one."""
        result = self._call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise RuntimeError("page threw: %s" % text)
        return result.get("result", {}).get("value")

    @staticmethod
    def truthy(value):
        """JavaScript truthiness, not Python's.

        They disagree on the one value a DOM query most often returns: an
        element comes back over CDP as `{}`, which JS calls true and Python
        calls false. A wait on `document.querySelector(...)` then spins
        until it times out while the element is sitting right there.
        """
        if value is None or value is False:
            return False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value != 0
        if isinstance(value, str):
            return value != ""
        # Objects and arrays, including the empty ones, are true in JS.
        return True

    def wait_for(self, expression, timeout=30, interval=0.1, what=None):
        """Poll until a JS expression is truthy. Real time, not virtual."""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = self.evaluate(expression, timeout=10)
                if self.truthy(last):
                    return last
            except RuntimeError as err:
                last = str(err)
            time.sleep(interval)
        raise Timeout("timed out waiting for %s (last: %r)" % (what or expression, last))

    def click(self, selector, timeout=10):
        """Click via the element's own click(), which is enough for buttons
        and rows and avoids coordinate arithmetic entirely."""
        self.wait_for(
            "!!document.querySelector(%s)" % json.dumps(selector),
            timeout=timeout,
            what="selector " + selector,
        )
        self.evaluate("document.querySelector(%s).click()" % json.dumps(selector))

    def screenshot(self, path):
        """Save a PNG of the viewport. Used to look at the thing, which no
        DOM assertion substitutes for."""
        result = self._call("Page.captureScreenshot", {"format": "png"}, timeout=60)
        with open(path, "wb") as out:
            out.write(base64.b64decode(result["data"]))
        return path

    def html(self):
        return self.evaluate("document.documentElement.outerHTML")

    def text(self, selector="body"):
        return self.evaluate(
            "(document.querySelector(%s) || {}).innerText || ''" % json.dumps(selector)
        )

    def errors(self):
        """Console messages that look like real problems."""
        bad = []
        for raw in self.console:
            message = json.loads(raw)
            params = message.get("params", {})
            if message.get("method") == "Runtime.consoleAPICalled":
                if params.get("type") == "error":
                    bad.append(
                        " ".join(
                            str(arg.get("value", arg.get("description", "")))
                            for arg in params.get("args", [])
                        )
                    )
            elif message.get("method") == "Runtime.exceptionThrown":
                detail = params.get("exceptionDetails", {})
                bad.append(detail.get("exception", {}).get("description", detail.get("text", "Uncaught exception")))
            else:
                entry = params.get("entry", {})
                if entry.get("level") == "error":
                    bad.append(entry.get("text", ""))
        return bad

    def close(self):
        try:
            self.ws.close()
        finally:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            shutil.rmtree(self.profile, ignore_errors=True)
