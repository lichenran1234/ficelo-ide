"""The HTTP layer.

Thin on purpose: every route turns a request into a call on `files` or
`paths`, and turns the exceptions those raise into status codes. Nothing here
decides what is safe — `paths.resolve` does, once, and every route that takes
a path goes through it.
"""

import json
import base64
import binascii
import mimetypes
import os
import posixpath
import re
import socket
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import files, git, paths, search, state, streaming
from .config import normalise_origin

WEB_ROOT = Path(__file__).resolve().parent / "web"

# Refuse a body larger than this on a save. A browser editing a text file
# never approaches it, and without a ceiling a single request can decide how
# much memory the process uses.
MAX_UPLOAD_BYTES = files.MAX_TEXT_BYTES + (64 * 1024)


# Types the browser may render in place. Everything else is sent as an opaque
# download: serving an arbitrary file inline, same-origin, with a guessed
# content type is how a stray .html or .svg in someone's tree turns into
# script running against this page. SVG is excluded for exactly that reason —
# it is an image that can carry script.
INLINE_TYPES = (
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp",
    "image/x-icon", "image/vnd.microsoft.icon", "image/avif",
    "application/pdf",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/flac", "audio/mp4", "audio/aac",
    "video/mp4", "video/webm", "video/ogg", "video/quicktime", "video/x-m4v",
    "video/x-msvideo", "video/x-matroska",
)


LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def host_name(header):
    """The name part of a Host header, lower-cased, without port or brackets."""
    value = header.strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def denied(what):
    """The message for a PermissionError, with the likely reason on macOS.

    macOS blocks a terminal-launched program from Desktop, Documents,
    Downloads and a few other folders until it is granted access — the
    files are readable by the user, just not by this process. A bare
    "not allowed" there reads like a ficelo bug.
    """
    message = "not allowed to %s" % what
    if sys.platform == "darwin":
        message += (
            ". macOS may be blocking this folder: allow your terminal in System"
            " Settings → Privacy & Security → Full Disk Access, then restart ficelo"
        )
    return message


class Failed(Exception):
    """A request that has a status code and something to say about it."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def guess_type(path):
    kind, _ = mimetypes.guess_type(str(path))
    if kind is None:
        return "application/octet-stream"
    if kind.startswith("text/") or kind in ("application/javascript", "application/json"):
        return kind + "; charset=utf-8"
    return kind


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 closes the connection after every response, which the browser
    # pays for on each of the several dozen requests a page load makes.
    protocol_version = "HTTP/1.1"
    server_version = "ficelo"
    sys_version = ""

    @property
    def config(self):
        return self.server.config

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        """One line per request, on stderr, without the default's date noise."""
        if not getattr(self.server, "quiet", False):
            super().log_message(fmt, *args)

    def respond(self, status, body, content_type="application/octet-stream", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.begin_response(status, len(body), content_type, extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def begin_response(self, status, length, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        # The tree changes under the browser constantly; a cached listing is
        # worse than no listing. Static assets are small enough not to care.
        self.send_header("Cache-Control", "no-store")
        # The frontend never frames anything and nothing should frame it.
        self.send_header("X-Content-Type-Options", "nosniff")
        # SAMEORIGIN rather than DENY: the PDF viewer is an <embed> pointing
        # at /api/raw on this same server, which DENY would blank.
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def send_json(self, payload, status=HTTPStatus.OK):
        self.respond(status, json.dumps(payload), "application/json; charset=utf-8")

    def query(self):
        return parse_qs(urlparse(self.path).query)

    def one(self, name, default=""):
        return self.query().get(name, [default])[0]

    def target(self, name="path"):
        """The requested path, resolved inside the root or refused."""
        try:
            return paths.resolve(self.one(name, "/"), self.config.root)
        except paths.OutsideRoot as err:
            raise Failed(HTTPStatus.FORBIDDEN, "outside the served folder: %s" % err)

    # -- dispatch ---------------------------------------------------------

    def do_GET(self):
        self.dispatch()

    def do_HEAD(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    # -- who is asking ------------------------------------------------------

    def refuse(self, status, message):
        """Refuse a request the cross-site checks caught, and say so in the
        log with the headers that decided it. Behind a proxy that rewrites
        Host, these refusals are the first sign anything is wrong, and the
        browser only ever shows a failed save."""
        sys.stderr.write(
            "refused %s %s: %s (Host=%r Origin=%r Sec-Fetch-Site=%r)\n"
            % (
                self.command,
                urlparse(self.path).path,
                message,
                self.headers.get("Host"),
                self.headers.get("Origin"),
                self.headers.get("Sec-Fetch-Site"),
            )
        )
        raise Failed(status, message)

    def check_host(self):
        """Refuse a request addressed to some other name, when bound to loopback.

        The defence against DNS rebinding: a page on evil.example points its
        own name at 127.0.0.1, after which the browser treats ficelo as
        same-origin with it and lets the page read whatever ficelo serves —
        which, with the root at "/", is the whole disk. The browser still
        sends `Host: evil.example`, so that is what gets checked.

        Not applied once --host exposes the server: the names that reach it
        then (a proxy, a VPN hostname) cannot be known in advance, and such
        a server already answers anyone on the network without a password.
        """
        if self.config.reachable_beyond_this_machine:
            return
        name = host_name(self.headers.get("Host", ""))
        if name not in LOOPBACK_NAMES and not name.endswith(".localhost"):
            self.refuse(
                HTTPStatus.FORBIDDEN,
                "this server only answers to localhost; open it as http://localhost:%d"
                % self.config.port,
            )

    def check_post(self):
        """Refuse a POST another website could have sent.

        Browsers send a cross-site POST without asking first only when it
        looks like a form — text/plain, urlencoded, multipart. Requiring JSON
        means any other site would have to ask first, and ficelo never says
        yes. Origin is checked as well when the browser supplies it.
        """
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            self.refuse(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "expected Content-Type: application/json")
        origin = self.headers.get("Origin")
        if origin and origin != "null":
            try:
                normalised = normalise_origin(origin)
            except ValueError:
                self.refuse(HTTPStatus.FORBIDDEN, "invalid Origin header")
            # Proxy forwarding headers are not trusted implicitly. Only an
            # explicitly configured browser origin may differ from Host.
            if (urlparse(origin).netloc.lower() != (self.headers.get("Host") or "").lower()
                    and normalised not in self.config.allowed_origins):
                self.refuse(HTTPStatus.FORBIDDEN, "refusing a request from another website")
        if (self.headers.get("Sec-Fetch-Site") or "") == "cross-site":
            self.refuse(HTTPStatus.FORBIDDEN, "refusing a request from another website")

    # -- the request body ---------------------------------------------------

    def read_body(self):
        """Read the whole request body off the connection, whatever its framing.

        Done for every request, before anything else, because connections are
        kept open: a body left unread is parsed as the start of the next
        request. Reverse proxies may forward GETs with chunked empty bodies;
        leaving their terminator unread would corrupt the next request on
        the connection.
        """
        self.raw_body = b""
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            self.raw_body = self.read_chunks()
            return
        try:
            value = (self.headers.get("Content-Length") or "0").strip()
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError("invalid length")
            length = int(value)
        except ValueError:
            self.close_connection = True
            raise Failed(HTTPStatus.BAD_REQUEST, "unreadable Content-Length")
        if length > MAX_UPLOAD_BYTES:
            # Not read, so the connection cannot be reused safely.
            self.close_connection = True
            raise Failed(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "that request is too large")
        if length > 0:
            self.raw_body = self.rfile.read(length)

    def read_chunks(self):
        """Decode a chunked body (RFC 9112 §7.1), with the same size ceiling."""
        parts = []
        total = 0
        while True:
            line = self.rfile.readline(1024)
            if not line:
                self.close_connection = True
                raise Failed(HTTPStatus.BAD_REQUEST, "the request body ended early")
            try:
                # Ignore optional chunk extensions after the size.
                value = line.split(b";", 1)[0].strip()
                if not line.endswith(b"\r\n") or not re.fullmatch(rb"[0-9a-fA-F]+", value):
                    raise ValueError("invalid chunk size")
                size = int(value, 16)
            except ValueError:
                self.close_connection = True
                raise Failed(HTTPStatus.BAD_REQUEST, "malformed chunked body")
            if size == 0:
                break
            total += size
            if total > MAX_UPLOAD_BYTES:
                self.close_connection = True
                raise Failed(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "that request is too large")
            chunk = self.rfile.read(size)
            if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                self.close_connection = True
                raise Failed(HTTPStatus.BAD_REQUEST, "malformed chunked body")
            parts.append(chunk)
        # Trailer fields, if any, end at a blank line.
        while True:
            line = self.rfile.readline(1024)
            if line in (b"\r\n", b"\n", b""):
                break
        return b"".join(parts)

    def dispatch(self):
        route = urlparse(self.path).path
        try:
            self.read_body()
            self.check_host()
            if self.command == "POST":
                self.check_post()
            if route.startswith("/api/"):
                self.api(route)
            elif route.startswith("/preview/") and self.command in ("GET", "HEAD"):
                self.api_raw(preview=unquote(route[len("/preview"):]))
            elif self.command == "POST":
                raise Failed(HTTPStatus.METHOD_NOT_ALLOWED, "POST only on /api/")
            else:
                self.asset(route)
        except Failed as err:
            self.send_json({"error": err.message}, err.status)
        except BrokenPipeError:
            # The browser navigated away mid-response. Routine, not an error.
            pass
        except Exception:
            traceback.print_exc()
            self.send_json(
                {"error": "internal error, see the server log"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def api(self, route):
        if route == "/api/terminal":
            return self.api_terminal()
        if route == "/api/config" and self.command in ("GET", "HEAD"):
            return self.api_config()
        if route == "/api/tree" and self.command in ("GET", "HEAD"):
            return self.api_tree()
        if route == "/api/info" and self.command in ("GET", "HEAD"):
            return self.api_info()
        if route == "/api/watch" and self.command == "POST":
            try:
                return self.send_json(files.fingerprints(self.body().get("paths"), self.config.root))
            except ValueError as err:
                raise Failed(HTTPStatus.BAD_REQUEST, str(err))
        if route == "/api/file" and self.command in ("GET", "HEAD"):
            return self.api_read()
        if route == "/api/file" and self.command == "POST":
            return self.api_write()
        if route == "/api/create" and self.command == "POST":
            return self.api_create()
        if route == "/api/rename" and self.command == "POST":
            return self.api_rename()
        if route == "/api/delete" and self.command == "POST":
            return self.api_delete()
        if route == "/api/search" and self.command in ("GET", "HEAD"):
            return self.api_search()
        if route == "/api/raw" and self.command in ("GET", "HEAD"):
            return self.api_raw()
        if route == "/api/state" and self.command in ("GET", "HEAD"):
            return self.api_state_read()
        if route == "/api/state" and self.command == "POST":
            return self.api_state_write()
        if route == "/api/workspaces/merge" and self.command == "POST":
            try:
                self.send_json(self.settings().merge_workspaces(self.body().get("expected")))
            except state.BadState as err:
                raise Failed(HTTPStatus.CONFLICT, str(err))
            return
        if route.startswith("/api/git/") and self.command in ("GET", "HEAD"):
            return self.api_git(route)
        raise Failed(HTTPStatus.NOT_FOUND, "no such endpoint: %s" % route)

    # -- endpoints --------------------------------------------------------

    def api_config(self):
        """What the frontend needs to know before it draws anything."""
        self.send_json(
            {
                "root": str(self.config.root),
                "root_name": self.config.root.name or str(self.config.root),
                "writable": self.config.writable,
                "terminal": self.config.terminal,
                "show_hidden": self.config.show_hidden,
                "home": self.config.home_request(),
            }
        )

    def api_terminal(self):
        pool = self.server.terminals
        if pool is None or not self.config.writable:
            raise Failed(HTTPStatus.FORBIDDEN, "terminals require --terminal in read-write mode")
        from .terminal import TerminalError

        try:
            if self.command in ("GET", "HEAD"):
                try:
                    after = int(self.one("after", "0"))
                except ValueError:
                    raise TerminalError("invalid terminal position")
                return self.send_json(pool.get(self.one("id", "")).read(after))
            if self.command != "POST":
                raise Failed(HTTPStatus.METHOD_NOT_ALLOWED, "unsupported terminal action")
            payload = self.body()
            action = payload.get("action", "open")
            if action == "open":
                folder = self.resolved(payload.get("path", "/"))
                if not folder.is_dir():
                    raise TerminalError("choose a folder for the terminal")
                ident = pool.create(folder, payload.get("cols", 80), payload.get("rows", 24))
                return self.send_json({"id": ident})
            ident = payload.get("id")
            if not isinstance(ident, str):
                raise TerminalError("invalid terminal id")
            if action == "close":
                pool.remove(ident)
            elif action == "resize":
                pool.get(ident).resize(payload.get("cols"), payload.get("rows"))
            elif action == "input":
                data = payload.get("data")
                if not isinstance(data, str) or len(data) > 90_000:
                    raise TerminalError("invalid terminal input")
                try:
                    decoded = base64.b64decode(data, validate=True)
                except (ValueError, binascii.Error):
                    raise TerminalError("invalid terminal input")
                pool.get(ident).write(decoded)
            else:
                raise TerminalError("unknown terminal action")
            self.send_json({"ok": True})
        except TerminalError as error:
            raise Failed(HTTPStatus.BAD_REQUEST, str(error))

    def wants_hidden(self):
        """Per request, defaulting to however the server was started.

        A viewing preference, not a permission: dotfiles are inside the
        served root and already reachable by name, so letting the page ask
        for them grants nothing that --hidden was withholding.
        """
        asked = self.one("hidden")
        if asked in ("1", "true", "yes"):
            return True
        if asked in ("0", "false", "no"):
            return False
        return self.config.show_hidden

    def api_tree(self):
        target = self.target()
        requested = paths.normalise(self.one("path", "/"))
        try:
            baseline = files.stamp(target)
            entries = files.listing(
                target, self.config.root, show_hidden=self.wants_hidden(), request_path=requested
            )
        except FileNotFoundError:
            raise Failed(HTTPStatus.NOT_FOUND, "no such folder")
        except NotADirectoryError:
            raise Failed(HTTPStatus.BAD_REQUEST, "not a folder")
        except PermissionError:
            raise Failed(HTTPStatus.FORBIDDEN, denied("read that folder"))
        self.send_json(
            {"path": requested, "entries": entries, "stamp": baseline}
        )

    def api_info(self):
        requested = paths.normalise(self.one("path", "/"))
        self.resolved(requested)
        try:
            self.send_json(files.information(requested, self.config.root))
        except FileNotFoundError:
            raise Failed(HTTPStatus.NOT_FOUND, self.missing_message(requested))
        except PermissionError:
            raise Failed(HTTPStatus.FORBIDDEN, denied("read that path"))

    def missing_message(self, requested):
        """Why a file is not there, when the answer is more specific than
        "it is not there". A dangling symlink is common on a shared box and
        looks like a plain absence, because resolve() has already followed
        it and reports the name it pointed at."""
        raw = Path(str(self.config.root).rstrip("/") + "/" + requested.lstrip("/"))
        if raw.is_symlink():
            return "this is a symlink and what it points to is missing"
        return "no such file"

    def api_read(self):
        target = self.target()
        try:
            baseline = files.stamp(target)
            text, truncated, binary = files.read_text(target)
        except FileNotFoundError:
            raise Failed(HTTPStatus.NOT_FOUND, self.missing_message(self.one("path", "/")))
        except IsADirectoryError:
            raise Failed(HTTPStatus.BAD_REQUEST, "that is a folder")
        except PermissionError:
            raise Failed(HTTPStatus.FORBIDDEN, denied("read that file"))
        except ValueError as error:
            raise Failed(HTTPStatus.BAD_REQUEST, str(error))
        self.send_json(
            {
                "path": paths.normalise(self.one("path", "/")),
                "text": text,
                "truncated": truncated,
                "binary": binary,
                "writable": self.config.writable,
                "stamp": baseline,
                "modified": files.modified(target),
                "size": files.size(target),
            }
        )

    def api_write(self):
        if not self.config.writable:
            raise Failed(
                HTTPStatus.FORBIDDEN,
                "this server is read-only; restart it with --write to save files",
            )
        payload = self.body()
        if not isinstance(payload.get("text"), str):
            raise Failed(HTTPStatus.BAD_REQUEST, "expected {\"path\": ..., \"text\": ...}")
        try:
            target = paths.resolve(payload.get("path", "/"), self.config.root)
        except paths.OutsideRoot as err:
            raise Failed(HTTPStatus.FORBIDDEN, "outside the served folder: %s" % err)
        if target.is_dir():
            raise Failed(HTTPStatus.BAD_REQUEST, "that is a folder")
        try:
            written = files.write_text(target, payload["text"], expect=payload.get("stamp"))
        except files.Conflict as err:
            # 409 rather than 403: the request was allowed, the state moved.
            raise Failed(HTTPStatus.CONFLICT, str(err))
        except FileNotFoundError:
            raise Failed(HTTPStatus.NOT_FOUND, "that folder does not exist")
        except PermissionError:
            raise Failed(HTTPStatus.FORBIDDEN, denied("write that file"))
        except ValueError as error:
            raise Failed(HTTPStatus.BAD_REQUEST, str(error))
        self.send_json(
            {
                "path": paths.to_request(target, self.config.root),
                "size": written,
                "stamp": files.stamp(target),
            }
        )

    def api_search(self):
        try:
            limit = max(1, min(200, int(self.one("limit", "50"))))
        except ValueError:
            limit = 50
        # `scope` narrows the walk to a subtree. Without it, serving a whole
        # filesystem means the walk exhausts its budget somewhere under /var
        # before it ever reaches anything of yours.
        scope = self.target("scope") if self.one("scope") else self.config.root
        if not scope.is_dir():
            scope = self.config.root
        self.send_json(
            search.find(
                scope,
                self.one("q"),
                limit=limit,
                show_hidden=self.wants_hidden(),
                root=self.config.root,
                recent=self.settings().read().get("ficelo.recent", {}),
            )
        )

    def api_raw(self, preview=None):
        """A file's bytes, for the things a browser can display itself."""
        target = self.resolved(preview) if preview is not None else self.target()
        kind, _ = mimetypes.guess_type(str(target))
        extra = {"Accept-Ranges": "bytes"}
        if preview is not None:
            kind = guess_type(target)
            extra.update({"Content-Security-Policy": "sandbox allow-scripts",
                          "Content-Disposition": "inline"})
        elif kind in INLINE_TYPES:
            extra["Content-Disposition"] = "inline"
        else:
            kind = "application/octet-stream"
            safe = "".join(ch for ch in target.name if 32 <= ord(ch) < 127 and ch not in '"\\')
            encoded = quote(target.name, safe="")
            extra["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded}; filename=\"{safe or 'download'}\""
        try:
            with streaming.open_regular(target) as (source, size):
                header = self.headers.get("Range") if self.command == "GET" and not self.headers.get("If-Range") else None
                try:
                    interval = streaming.byte_range(header, size)
                except streaming.UnsatisfiableRange:
                    extra["Content-Range"] = f"bytes */{size}"
                    return self.respond(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", kind, extra)
                start, end = interval if interval else (0, size - 1)
                length = end - start + 1
                if interval:
                    extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                self.begin_response(HTTPStatus.PARTIAL_CONTENT if interval else HTTPStatus.OK, length, kind, extra)
                if self.command != "HEAD" and not streaming.copy_bytes(source, self.wfile, start, length):
                    self.close_connection = True
        except FileNotFoundError:
            raise Failed(HTTPStatus.NOT_FOUND, "no such file")
        except IsADirectoryError:
            raise Failed(HTTPStatus.BAD_REQUEST, "that is a folder")
        except PermissionError:
            raise Failed(HTTPStatus.FORBIDDEN, denied("read that file"))
        except ValueError as error:
            raise Failed(HTTPStatus.BAD_REQUEST, str(error))

    def body(self):
        """The JSON body of a POST, or a 400 explaining why not."""
        try:
            payload = json.loads(self.raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise Failed(HTTPStatus.BAD_REQUEST, "expected a JSON body")
        if not isinstance(payload, dict):
            raise Failed(HTTPStatus.BAD_REQUEST, "expected a JSON object")
        return payload

    def require_writable(self):
        if not self.config.writable:
            raise Failed(
                HTTPStatus.FORBIDDEN,
                "this server is read-only; restart it with --write to change files",
            )

    def resolved(self, requested):
        try:
            return paths.resolve(requested, self.config.root)
        except paths.OutsideRoot as err:
            raise Failed(HTTPStatus.FORBIDDEN, "outside the served folder: %s" % err)

    def entry_target(self, requested):
        """Validate containment, then retain the final link for rename/delete."""
        requested = paths.normalise(requested)
        self.resolved(requested)
        if requested == "/":
            return self.config.root
        logical = Path(requested)
        return self.resolved(str(logical.parent)) / logical.name

    def api_create(self):
        self.require_writable()
        payload = self.body()
        kind = payload.get("kind", "file")
        if kind not in ("file", "folder"):
            raise Failed(HTTPStatus.BAD_REQUEST, "kind must be file or folder")
        target = self.resolved(payload.get("path", ""))
        if target == Path(os.path.realpath(self.config.root)):
            raise Failed(HTTPStatus.BAD_REQUEST, "that is the folder being served")
        try:
            files.create(target, kind)
        except FileExistsError as err:
            raise Failed(HTTPStatus.CONFLICT, str(err))
        except (PermissionError, OSError) as err:
            raise Failed(HTTPStatus.FORBIDDEN, str(err))
        search.clear_cache()
        self.send_json({"path": paths.normalise(payload.get("path", "/"))})

    def api_rename(self):
        self.require_writable()
        payload = self.body()
        source = self.entry_target(payload.get("from", ""))
        target = self.entry_target(payload.get("to", ""))
        root = Path(os.path.realpath(self.config.root))
        if source == root or target == root:
            raise Failed(HTTPStatus.BAD_REQUEST, "that is the folder being served")
        try:
            files.rename(source, target)
        except FileNotFoundError as err:
            raise Failed(HTTPStatus.NOT_FOUND, str(err))
        except FileExistsError as err:
            raise Failed(HTTPStatus.CONFLICT, str(err))
        except (PermissionError, OSError) as err:
            raise Failed(HTTPStatus.FORBIDDEN, str(err))
        search.clear_cache()
        self.send_json(
            {
                "from": paths.normalise(payload.get("from", "/")),
                "to": paths.normalise(payload.get("to", "/")),
            }
        )

    def api_delete(self):
        self.require_writable()
        payload = self.body()
        target = self.entry_target(payload.get("path", ""))
        if target == Path(os.path.realpath(self.config.root)):
            raise Failed(HTTPStatus.BAD_REQUEST, "that is the folder being served")
        try:
            files.remove(target)
        except FileNotFoundError:
            raise Failed(HTTPStatus.NOT_FOUND, "there is nothing there")
        except IsADirectoryError as err:
            raise Failed(HTTPStatus.CONFLICT, str(err))
        except (PermissionError, OSError) as err:
            raise Failed(HTTPStatus.FORBIDDEN, str(err))
        search.clear_cache()
        self.send_json({"path": paths.normalise(payload.get("path", "/")), "deleted": True})

    # -- ficelo's own settings ---------------------------------------------

    def settings(self):
        return self.server.settings

    def api_state_read(self):
        self.send_json({"settings": self.settings().read()})

    def api_state_write(self):
        """Store one setting. Allowed on a read-only server: it writes only
        under the state folder, never into the served tree."""
        payload = self.body()
        try:
            if "changes" in payload:
                return self.send_json(self.settings().patch(payload.get("key"), payload["changes"]))
            self.settings().put(payload.get("key"), payload.get("value"))
        except state.BadState as err:
            raise Failed(HTTPStatus.BAD_REQUEST, str(err))
        except OSError as err:
            raise Failed(HTTPStatus.INTERNAL_SERVER_ERROR, "could not save settings: %s" % err)
        self.send_json({"saved": payload.get("key")})

    # -- git ---------------------------------------------------------------

    def api_git(self, route):
        try:
            if route == "/api/git/repos":
                return self.send_json(git.discover(self.config.root, self.query().get("scope", []), self.one("q")))
            if route == "/api/git/status":
                return self.api_git_status()
            if route == "/api/git/diff":
                return self.api_git_diff()
            if route == "/api/git/commits":
                return self.api_git_commits()
            if route == "/api/git/commit":
                return self.api_git_commit()
        except git.GitFailed as err:
            raise Failed(HTTPStatus.BAD_REQUEST, str(err))
        raise Failed(HTTPStatus.NOT_FOUND, "no such endpoint: %s" % route)

    def api_git_status(self):
        self.send_json(git.review_status(self.config.root, self.one("repo", "/"), self.one("base") or None))

    def api_git_diff(self):
        payload = git.review_diff(self.config.root, self.one("repo") or self.one("path", "/"),
                                  self.one("path", "/"), self.one("base") or None, self.one("rev") or None)
        payload["editable"] = payload["editable"] and self.config.writable
        self.send_json(payload)

    def api_git_commits(self):
        try:
            limit = int(self.one("limit", "200"))
        except ValueError:
            limit = 200
        self.send_json(git.review_commits(self.config.root, self.one("repo", "/"),
                                         self.one("base") or None, limit))

    def api_git_commit(self):
        sha = self.one("sha")
        if not sha:
            raise Failed(HTTPStatus.BAD_REQUEST, "which commit?")
        self.send_json(git.review_commit(self.config.root, self.one("repo", "/"), sha,
                                        self.one("full") == "1"))

    # -- the page itself --------------------------------------------------

    def asset(self, route):
        """Files from web/, with / meaning index.html.

        Served from a fixed directory that has nothing to do with the root
        being browsed, and joined with posixpath so a request cannot climb
        out of it.
        """
        rel = posixpath.normpath(unquote(route)).lstrip("/")
        if rel in ("", "."):
            rel = "index.html"
        target = WEB_ROOT / rel
        real = Path(os.path.realpath(target))
        web_real = Path(os.path.realpath(WEB_ROOT))
        if real != web_real and web_real not in real.parents:
            raise Failed(HTTPStatus.FORBIDDEN, "not an asset")
        try:
            body = real.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise Failed(HTTPStatus.NOT_FOUND, "no such asset: %s" % rel)
        self.respond(HTTPStatus.OK, body, guess_type(real))


def address_family(host):
    """IPv6 for an IPv6 literal or `::`, IPv4 otherwise.

    The standard library server is IPv4 unless told; handed "::" it fails to
    bind, and handed "0.0.0.0" it is unreachable on any machine whose name
    resolves only to an IPv6 address — which a browser then reports as a
    page that will not load, with nothing in the server log to say why.
    """
    return socket.AF_INET6 if ":" in host else socket.AF_INET


class Server(ThreadingHTTPServer):
    # Threading so one slow read does not hold up the page, and
    # allow_reuse_address so a restart does not wait out TIME_WAIT.
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config, quiet=False):
        self.config = config
        self.settings = state.Settings(config.state_dir)
        self.quiet = quiet
        self.address_family = address_family(config.host)
        super().__init__((config.host, config.port), Handler)
        self.terminals = None
        if config.terminal:
            from .terminal import Pool
            self.terminals = Pool()

    def server_close(self):
        if getattr(self, "terminals", None):
            self.terminals.close()
        super().server_close()

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            # Dual-stack: "::" also accepts IPv4 clients, so one flag means
            # "every interface" on both families. Some systems default this
            # to on, which would quietly drop IPv4.
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        super().server_bind()


def serve(config, quiet=False):
    return Server(config, quiet=quiet)
