"""Optional local PTYs. No shell starts until an enabled client asks for one."""

import base64
import collections
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid

MAX_OUTPUT = 2 * 1024 * 1024
MAX_INPUT = 64 * 1024
MAX_TERMINALS = 8
IDLE_SECONDS = 30 * 60


class TerminalError(Exception):
    pass


def dimensions(cols, rows):
    if (not isinstance(cols, int) or isinstance(cols, bool) or not 2 <= cols <= 500
            or not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= 300):
        raise TerminalError("terminal size is outside the supported range")
    return cols, rows


class Session:
    def __init__(self, folder, cols=80, rows=24, shell=None):
        dimensions(cols, rows)
        shell = shell or os.environ.get("SHELL", "/bin/sh")
        if not os.path.isabs(shell) or not os.path.isfile(shell):
            shell = "/bin/sh"
        self.lock = threading.RLock()
        self.input_lock = threading.Lock()
        self.chunks = collections.deque()
        self.end = 0
        self.last_used = time.monotonic()
        self.closed = threading.Event()
        self.master, slave = pty.openpty()
        os.set_blocking(self.master, False)
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            environment = dict(os.environ, TERM="xterm-256color", TERM_PROGRAM="ficelo", COLORTERM="truecolor")
            # The small child helper owns the controlling-terminal setup.
            # preexec_fn is unsafe in a threaded HTTP server.
            self.process = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--child", shell],
                cwd=folder, stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, env=environment,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        while not self.closed.is_set():
            try:
                if not select.select([self.master], [], [], 0.2)[0]:
                    continue
                data = os.read(self.master, 65536)
                if not data:
                    break
            except BlockingIOError:
                continue
            except OSError as error:
                if error.errno not in (errno.EIO, errno.EBADF):
                    with self.lock:
                        self.failure = str(error)
                break
            with self.lock:
                self.chunks.append((self.end, data))
                self.end += len(data)
                while self.chunks and self.end - self.chunks[0][0] > MAX_OUTPUT:
                    self.chunks.popleft()

    def read(self, after=0):
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise TerminalError("invalid terminal position")
        with self.lock:
            self.last_used = time.monotonic()
            start = self.chunks[0][0] if self.chunks else self.end
            cursor = max(start, min(after, self.end))
            pieces = []
            remaining = 128 * 1024
            for offset, data in self.chunks:
                if offset + len(data) <= cursor:
                    continue
                part = data[max(0, cursor - offset):][:remaining]
                pieces.append(part)
                cursor += len(part)
                remaining -= len(part)
                if not remaining:
                    break
            return {"data": base64.b64encode(b"".join(pieces)).decode("ascii"),
                    "cursor": cursor, "truncated": after < start,
                    "exited": self.process.poll() is not None,
                    "error": getattr(self, "failure", None)}

    def write(self, data):
        if not isinstance(data, bytes) or len(data) > MAX_INPUT:
            raise TerminalError("terminal input is too large")
        with self.input_lock:
            if self.closed.is_set() or self.process.poll() is not None:
                raise TerminalError("this terminal has exited")
            self.last_used = time.monotonic()
            deadline = time.monotonic() + 2
            while data:
                if self.closed.is_set():
                    raise TerminalError("this terminal has exited")
                if time.monotonic() > deadline:
                    raise TerminalError("terminal input timed out")
                try:
                    if not select.select([], [self.master], [], 0.1)[1]:
                        continue
                    if self.closed.is_set():
                        raise TerminalError("this terminal has exited")
                    written = os.write(self.master, data)
                    data = data[written:]
                except BlockingIOError:
                    continue
                except OSError as error:
                    raise TerminalError("terminal input could not be delivered") from error

    def resize(self, cols, rows):
        dimensions(cols, rows)
        with self.lock:
            if not self.closed.is_set():
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def close(self):
        with self.lock:
            if self.closed.is_set():
                return
            self.closed.set()
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            if self.process.poll() is None:
                self.process.terminate()
        # Both descriptor users must finish before close permits the OS to
        # reuse its number for a different terminal. Neither waits on output
        # while holding self.lock, so the reader can finish its last chunk.
        with self.input_lock:
            self.reader.join()
            with self.lock:
                os.close(self.master)
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                self.process.kill()
            self.process.wait(timeout=1)


class Pool:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.reaper = threading.Thread(target=self._expire, daemon=True)
        self.reaper.start()

    def create(self, folder, cols=80, rows=24):
        with self.lock:
            if self.stopped.is_set():
                raise TerminalError("terminal service has stopped")
            if len(self.sessions) >= MAX_TERMINALS:
                raise TerminalError("close a terminal before opening another")
            session = Session(folder, cols, rows)
            ident = uuid.uuid4().hex
            self.sessions[ident] = session
            return ident

    def get(self, ident):
        with self.lock:
            session = self.sessions.get(ident)
        if session is None:
            raise TerminalError("that terminal has closed")
        return session

    def remove(self, ident):
        with self.lock:
            session = self.sessions.pop(ident, None)
        if session:
            session.close()

    def _expire(self):
        while not self.stopped.wait(15):
            with self.lock:
                expired = [ident for ident, session in self.sessions.items()
                           if time.monotonic() - session.last_used > IDLE_SECONDS]
            for ident in expired:
                self.remove(ident)

    def close(self):
        self.stopped.set()
        with self.lock:
            identifiers = list(self.sessions)
        for ident in identifiers:
            self.remove(ident)
        self.reaper.join(timeout=1)


def child(shell):
    """Run only in the newly launched, single-threaded helper process."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execv(shell, [shell, "-i"])


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--child":
        raise SystemExit("terminals are started through ficelo")
    child(sys.argv[2])
