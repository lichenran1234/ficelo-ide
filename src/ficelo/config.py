"""What the server was asked to do.

Defaults let someone who has just installed this browse and edit local files.
The server listens on loopback; read-only mode and network access are explicit
launch settings.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from . import state

DEFAULT_PORT = 9248
DEFAULT_ROOT = "/"
# Loopback, not 0.0.0.0. Serving your home directory to every interface is a
# decision, not a default, and the person best placed to make it is the one
# who reads the flag.
DEFAULT_HOST = "127.0.0.1"

# Machinery rather than anything anyone opens: object stores, caches, build
# output, vendored dependencies. Matched by name, since they mean the same
# thing wherever they appear. Names a real source folder might also use —
# build, dist, target — are deliberately absent.
SKIP_NAMES = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", ".venv", "venv", ".tox", ".eggs"}
)


def normalise_origin(value):
    """An exact HTTP(S) origin, without credentials, paths or wildcards."""
    message = "allowed origins must be HTTP(S) URLs without credentials, paths or wildcards"
    if not isinstance(value, str) or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError(message)
    if not value.isascii():
        raise ValueError("allowed origins must use ASCII hostnames; use the browser's punycode origin for internationalized domains")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment or
                any(char in value for char in "*?#\\")):
            raise ValueError(message)
        port = parsed.port
        if port is not None and port < 1:
            raise ValueError(message)
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        authority = host if port in (None, 443 if parsed.scheme == "https" else 80) else f"{host}:{port}"
        return f"{parsed.scheme}://{authority}"
    except ValueError as error:
        raise ValueError(message) from error


class Config:
    def __init__(self, root=None, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 writable=True, show_hidden=False, state_dir=None, terminal=False, allowed_origins=()):
        # The whole filesystem by default, so anything on the machine can be
        # opened; the tree still *starts* at the home folder (home_request).
        # Loopback binding and the cross-site checks in server.py limit who
        # can use it. The read/write mode is fixed when the server starts.
        self.root = Path(os.path.realpath(root or DEFAULT_ROOT))
        self.host = host
        self.port = port
        self.writable = writable
        self.terminal = terminal and writable
        if not isinstance(allowed_origins, (list, tuple)):
            raise TypeError("allowed_origins must be a list of HTTP(S) origins")
        self.allowed_origins = frozenset(normalise_origin(origin) for origin in allowed_origins)
        self.show_hidden = show_hidden
        # ficelo's own files (settings.json, config.json); see state.py.
        self.state_dir = Path(state_dir) if state_dir else state.home()

    @property
    def reachable_beyond_this_machine(self):
        """Whether the chosen host lets anything off the box connect.

        Used to decide whether to warn on startup. Loopback is the only
        address that answers no.
        """
        return self.host not in ("127.0.0.1", "::1", "localhost")

    def home_request(self):
        """Home as a path inside the served folder, or "/" if it is not.

        When serving the whole filesystem, the page starts at the account's
        home folder instead of the top of the disk.
        """
        try:
            home = Path(os.path.realpath(Path.home()))
        except (OSError, RuntimeError):
            return "/"
        if home == self.root:
            return "/"
        try:
            return "/" + str(home.relative_to(self.root))
        except ValueError:
            return "/"

    @property
    def url(self):
        # An IPv6 literal needs brackets in a URL, or the port reads as one
        # more group of the address.
        host = "[%s]" % self.host if ":" in self.host else self.host
        return "http://%s:%d" % (host, self.port)

    def describe(self):
        """The startup banner, and the honest version of what is being served."""
        lines = [
            "ficelo",
            "  serving  %s" % self.root,
            "  at       %s" % self.url,
            "  mode     %s" % ("read-write" if self.writable else "read-only"),
        ]
        if self.reachable_beyond_this_machine:
            lines.append(
                "  warning  %s is reachable from other machines, and there is no "
                "password. Anyone who can reach this port can read %s%s."
                % (
                    self.host,
                    self.root,
                    " and write to it" if self.writable else "",
                )
            )
        return "\n".join(lines)
