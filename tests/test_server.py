import json
import os
import posixpath
import re
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import files  # noqa: E402
from ficelo.config import Config  # noqa: E402
from ficelo.server import serve  # noqa: E402


class ServerCase(unittest.TestCase):
    """Driven over a real socket on an ephemeral port.

    Calling the handler directly would mean stubbing rfile, wfile and the
    header parsing, and the stubs would then be the thing under test. A real
    request costs a millisecond and exercises the status codes the browser
    will actually see.
    """

    writable = False

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.prepare()
        # Port 0 lets the OS pick a free one, so parallel test shards never
        # collide on a hard-coded number.
        mode = {} if self.writable is None else {"writable": self.writable}
        config = Config(root=self.root, port=0, state_dir=tempfile.mkdtemp(), **mode)
        self.server = serve(config, quiet=True)
        self.port = self.server.server_address[1]
        # A short poll interval: shutdown() only notices between polls, and
        # the 0.5s default turns a suite of fast tests into a slow one.
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def prepare(self):
        pass

    def url(self, route):
        return "http://127.0.0.1:%d%s" % (self.port, route)

    def get(self, route):
        with urllib.request.urlopen(self.url(route), timeout=5) as res:
            return res.status, res.read(), dict(res.headers)

    def get_json(self, route):
        status, body, _ = self.get(route)
        return status, json.loads(body)

    def post_json(self, route, payload):
        request = urllib.request.Request(
            self.url(route),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as res:
            return res.status, json.loads(res.read())

    def expect_failure(self, call):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            call()
        err = caught.exception
        return err.code, json.loads(err.read())


class PreviewResourcesTest(ServerCase):
    def test_active_documents_are_sandboxed_even_when_opened_directly(self):
        (self.root / "page.html").write_text("<script>parent.document.title='changed'</script>")
        status, body, headers = self.get("/preview/page.html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(headers["Content-Security-Policy"], "sandbox allow-scripts")
        self.assertIn(b"<script>", body)
        _, _, raw_headers = self.get("/api/raw?path=/page.html")
        self.assertIn("attachment", raw_headers["Content-Disposition"])

    def test_preview_resources_cannot_follow_a_symlink_outside_the_root(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.html"
            target.write_text("private")
            (self.root / "link.html").symlink_to(target)
            status, _ = self.expect_failure(lambda: self.get("/preview/link.html"))
            self.assertEqual(status, 403)


@unittest.skipUnless(socket.has_ipv6, "this machine has no IPv6")
class AddressFamilyTest(unittest.TestCase):
    """Binding on `::` must reach both families.

    Found on a real host whose name resolves only to IPv6: bound to
    0.0.0.0, ficelo answered on 127.0.0.1 and was unreachable by name, so the
    browser showed a page that would not load and the server logged nothing.
    """

    def start(self, host):
        root = Path(tempfile.mkdtemp())
        (root / "a.txt").write_text("x\n")
        server = serve(Config(root=root, host=host, port=0, state_dir=tempfile.mkdtemp()), quiet=True)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server.server_address[1]

    def fetch(self, url):
        # Direct, never via a proxy. An environment with http_proxy set and a
        # no_proxy list that names 127.0.0.1 but not ::1 sends the IPv6 case
        # off to the proxy, which refuses a loopback destination — a failure
        # of the test environment that looks exactly like a failure to bind.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as res:
            return res.status

    def test_double_colon_answers_over_ipv6(self):
        port = self.start("::")
        self.assertEqual(self.fetch("http://[::1]:%d/api/config" % port), 200)

    def test_double_colon_also_answers_over_ipv4(self):
        """Dual-stack, so one flag means every interface on both families."""
        port = self.start("::")
        self.assertEqual(self.fetch("http://127.0.0.1:%d/api/config" % port), 200)

    def test_an_ipv6_loopback_literal_binds(self):
        port = self.start("::1")
        self.assertEqual(self.fetch("http://[::1]:%d/api/config" % port), 200)

    def test_ipv4_still_binds_ipv4(self):
        port = self.start("127.0.0.1")
        self.assertEqual(self.fetch("http://127.0.0.1:%d/api/config" % port), 200)

    def test_the_banner_brackets_an_ipv6_address(self):
        """Otherwise the port reads as one more group of the address."""
        config = Config(root=tempfile.mkdtemp(), host="::", port=9248)
        self.assertEqual(config.url, "http://[::]:9248")
        self.assertEqual(Config(root=tempfile.mkdtemp(), port=8777).url, "http://127.0.0.1:8777")


class ConfigEndpointTest(ServerCase):
    def test_it_reports_explicit_read_only_mode(self):
        status, body = self.get_json("/api/config")
        self.assertEqual(status, 200)
        self.assertFalse(body["writable"])

    def test_it_names_the_root_for_the_title_bar(self):
        _, body = self.get_json("/api/config")
        self.assertEqual(body["root_name"], self.root.name)


class DefaultWriteModeTest(ServerCase):
    writable = None  # Exercise Config's default instead of a fixture override.

    def test_default_mode_is_reported_and_allows_file_changes(self):
        _, config = self.get_json("/api/config")
        self.assertTrue(config["writable"])
        self.assertFalse(config["terminal"])
        status, _ = self.post_json("/api/file", {"path": "/created.txt", "text": "saved\n"})
        self.assertEqual(status, 200)
        self.assertEqual((self.root / "created.txt").read_text(), "saved\n")


class SearchScopeTest(ServerCase):
    def prepare(self):
        (self.root / "mine").mkdir()
        (self.root / "mine" / "target.txt").write_text("x")
        (self.root / "elsewhere").mkdir()
        (self.root / "elsewhere" / "target.txt").write_text("x")

    def test_without_a_scope_it_searches_everything(self):
        _, body = self.get_json("/api/search?q=target")
        self.assertEqual(len(body["matches"]), 2)

    def test_a_scope_narrows_the_walk(self):
        """Serving a whole filesystem is a real configuration, and there the
        walk exhausts its budget somewhere under /var long before it reaches
        anything of yours. Scoping is what makes the box usable."""
        _, body = self.get_json("/api/search?q=target&scope=/mine")
        self.assertEqual([m["path"] for m in body["matches"]], ["/mine/target.txt"])

    def test_results_are_still_root_relative_inside_a_scope(self):
        _, body = self.get_json("/api/search?q=target&scope=/mine")
        self.assertTrue(body["matches"][0]["path"].startswith("/mine/"))

    def test_a_scope_cannot_escape_the_root(self):
        outside = self.root.parent / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "target.txt").write_text("x")
        (self.root / "link").symlink_to(outside)
        code, _ = self.expect_failure(lambda: self.get("/api/search?q=target&scope=/link"))
        self.assertEqual(code, 403)

    def test_two_scopes_do_not_share_a_cached_answer(self):
        """The walk is cached for fifteen seconds. Keyed without the scope,
        the second search of a session answers with the first one's tree —
        and separate tests never catch it, because each gets a fresh root."""
        _, mine = self.get_json("/api/search?q=target&scope=/mine")
        _, other = self.get_json("/api/search?q=target&scope=/elsewhere")
        self.assertEqual([m["path"] for m in mine["matches"]], ["/mine/target.txt"])
        self.assertEqual([m["path"] for m in other["matches"]], ["/elsewhere/target.txt"])

    def test_a_scope_that_is_not_a_folder_falls_back_to_the_root(self):
        _, body = self.get_json("/api/search?q=target&scope=/mine/target.txt")
        self.assertEqual(len(body["matches"]), 2)


class ConfigHomeTest(ServerCase):
    def test_config_reports_where_home_is(self):
        _, body = self.get_json("/api/config")
        self.assertIn("home", body)
        # The served root here is a temp dir, which never contains $HOME.
        self.assertEqual(body["home"], "/")


class TreeEndpointTest(ServerCase):
    def prepare(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print(1)\n")
        (self.root / "readme.md").write_text("# hi\n")

    def test_it_lists_the_root(self):
        status, body = self.get_json("/api/tree?path=/")
        self.assertEqual(status, 200)
        self.assertEqual([e["name"] for e in body["entries"]], ["src", "readme.md"])

    def test_it_lists_a_subfolder(self):
        _, body = self.get_json("/api/tree?path=/src")
        self.assertEqual([e["name"] for e in body["entries"]], ["main.py"])

    def test_no_path_means_the_root(self):
        _, body = self.get_json("/api/tree")
        self.assertEqual(body["path"], "/")

    def test_a_missing_folder_is_404(self):
        code, body = self.expect_failure(lambda: self.get("/api/tree?path=/nope"))
        self.assertEqual(code, 404)
        self.assertIn("folder", body["error"])

    def test_a_file_is_not_a_folder(self):
        code, _ = self.expect_failure(lambda: self.get("/api/tree?path=/readme.md"))
        self.assertEqual(code, 400)

    def test_an_unknown_endpoint_is_404_not_a_crash(self):
        code, _ = self.expect_failure(lambda: self.get("/api/nope"))
        self.assertEqual(code, 404)


class EscapeTest(ServerCase):
    """The one thing that must not work."""

    def prepare(self):
        self.secret = self.root.parent / "secret.txt"
        self.secret.write_text("password\n")
        (self.root / "escape").symlink_to(self.secret)

    def test_a_symlink_out_of_the_root_is_refused(self):
        code, body = self.expect_failure(lambda: self.get("/api/file?path=/escape"))
        self.assertEqual(code, 403)
        self.assertNotIn("password", json.dumps(body))

    def test_climbing_out_does_not_reach_the_parent(self):
        code, _ = self.expect_failure(
            lambda: self.get("/api/file?path=/../" + self.secret.name)
        )
        self.assertEqual(code, 404, "collapsed to a path inside the root, which is absent")

    def test_an_absolute_path_is_read_as_relative_to_the_root(self):
        code, _ = self.expect_failure(lambda: self.get("/api/file?path=/etc/passwd"))
        self.assertEqual(code, 404)

    def test_climbing_out_of_the_asset_route_gets_nowhere(self):
        """The asset route must not be a second way into the served tree.
        Passes on the textual collapse alone, which is why the symlink case
        below is tested separately."""
        code, _ = self.expect_failure(lambda: self.get("/../../../etc/passwd"))
        self.assertIn(code, (403, 404))

    def test_a_symlink_inside_the_asset_folder_is_refused(self):
        """What the textual collapse cannot catch, and the only case in which
        the containment check in asset() does any work: nothing in the request
        looks suspicious and the escape happens on disk. Without this the
        check can be deleted and the suite still passes."""
        from ficelo import server as server_module

        web = Path(tempfile.mkdtemp())
        (web / "index.html").write_text("<title>ficelo</title>")
        (web / "escape.txt").symlink_to(self.secret)
        real = server_module.WEB_ROOT
        server_module.WEB_ROOT = web
        try:
            code, body = self.expect_failure(lambda: self.get("/escape.txt"))
        finally:
            server_module.WEB_ROOT = real
        self.assertEqual(code, 403)
        self.assertNotIn("password", json.dumps(body))


class FileEndpointTest(ServerCase):
    def prepare(self):
        (self.root / "a.txt").write_text("one\ntwo\n")
        (self.root / "b.bin").write_bytes(b"\x00\x01\x02")
        (self.root / "dir").mkdir()

    def test_it_reads_a_file(self):
        status, body = self.get_json("/api/file?path=/a.txt")
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "one\ntwo\n")
        self.assertFalse(body["binary"])

    def test_it_reports_the_size_on_disk(self):
        """The status bar shows it, and it is the file's size rather than
        the length of the text handed over — those differ on a truncated
        read, and reporting the short one would be a lie about the file."""
        _, body = self.get_json("/api/file?path=/a.txt")
        self.assertEqual(body["size"], (self.root / "a.txt").stat().st_size)

    def test_a_truncated_read_still_reports_the_real_size(self):
        big = self.root / "big.txt"
        big.write_text("x" * (files.MAX_TEXT_BYTES + 500))
        _, body = self.get_json("/api/file?path=/big.txt")
        self.assertTrue(body["truncated"])
        self.assertEqual(body["size"], files.MAX_TEXT_BYTES + 500)
        self.assertLess(len(body["text"]), body["size"])

    def test_a_binary_file_says_so_instead_of_returning_mojibake(self):
        _, body = self.get_json("/api/file?path=/b.bin")
        self.assertTrue(body["binary"])
        self.assertIsNone(body["text"])

    def test_a_missing_file_is_404(self):
        code, _ = self.expect_failure(lambda: self.get("/api/file?path=/nope.txt"))
        self.assertEqual(code, 404)

    def test_a_dangling_symlink_says_what_is_wrong(self):
        """resolve() has already followed it, so the plain answer names a
        file the person never asked for and reads as a bug."""
        (self.root / "dangling.txt").symlink_to(self.root / "absent.txt")
        code, body = self.expect_failure(lambda: self.get("/api/file?path=/dangling.txt"))
        self.assertEqual(code, 404)
        self.assertIn("symlink", body["error"])

    def test_a_folder_is_not_a_file(self):
        code, _ = self.expect_failure(lambda: self.get("/api/file?path=/dir"))
        self.assertEqual(code, 400)

    def test_a_fifo_is_refused_without_waiting_for_a_writer(self):
        os.mkfifo(self.root / "pipe")
        code, body = self.expect_failure(lambda: self.get("/api/file?path=/pipe"))
        self.assertEqual(code, 400)
        self.assertIn("regular files", body["error"])

    def test_a_name_with_spaces_and_hashes_survives_the_url(self):
        (self.root / "a b#c.txt").write_text("odd\n")
        _, body = self.get_json("/api/file?path=/a%20b%23c.txt")
        self.assertEqual(body["text"], "odd\n")


class ReadOnlyTest(ServerCase):
    writable = False

    def prepare(self):
        (self.root / "a.txt").write_text("original\n")

    def test_saving_is_refused(self):
        code, body = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/a.txt", "text": "changed\n"})
        )
        self.assertEqual(code, 403)
        self.assertIn("--write", body["error"], "says how to allow it")

    def test_and_the_file_is_untouched(self):
        self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/a.txt", "text": "changed\n"})
        )
        self.assertEqual((self.root / "a.txt").read_text(), "original\n")


class WritableTest(ServerCase):
    writable = True

    def prepare(self):
        (self.root / "a.txt").write_text("original\n")

    def test_it_saves(self):
        status, body = self.post_json("/api/file", {"path": "/a.txt", "text": "changed\n"})
        self.assertEqual(status, 200)
        self.assertEqual((self.root / "a.txt").read_text(), "changed\n")
        self.assertEqual(body["size"], 8)

    def test_it_creates_a_new_file(self):
        self.post_json("/api/file", {"path": "/new.txt", "text": "fresh\n"})
        self.assertEqual((self.root / "new.txt").read_text(), "fresh\n")

    def test_saving_outside_the_root_is_refused(self):
        outside = self.root.parent / "victim.txt"
        outside.write_text("safe\n")
        (self.root / "link").symlink_to(outside)
        code, _ = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/link", "text": "owned\n"})
        )
        self.assertEqual(code, 403)
        self.assertEqual(outside.read_text(), "safe\n")

    def test_a_body_that_is_not_json_is_400(self):
        request = urllib.request.Request(
            self.url("/api/file"),
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        code, _ = self.expect_failure(lambda: urllib.request.urlopen(request, timeout=5))
        self.assertEqual(code, 400)

    def test_a_body_without_text_is_400_rather_than_an_empty_file(self):
        code, _ = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/a.txt"})
        )
        self.assertEqual(code, 400)
        self.assertEqual((self.root / "a.txt").read_text(), "original\n")

    def test_saving_over_a_folder_is_refused(self):
        (self.root / "dir").mkdir()
        code, _ = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/dir", "text": "x"})
        )
        self.assertEqual(code, 400)

    def test_saving_over_a_fifo_is_refused_without_replacing_it(self):
        import stat

        pipe = self.root / "pipe"
        os.mkfifo(pipe)
        code, body = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/pipe", "text": "changed"})
        )
        self.assertEqual(code, 400)
        self.assertIn("regular files", body["error"])
        self.assertTrue(stat.S_ISFIFO(pipe.stat().st_mode))


class ConflictTest(ServerCase):
    writable = True

    def prepare(self):
        (self.root / "a.txt").write_text("original\n")

    def read(self):
        _, body = self.get_json("/api/file?path=/a.txt")
        return body

    def test_a_save_with_the_current_stamp_succeeds(self):
        stamp = self.read()["stamp"]
        status, _ = self.post_json("/api/file", {"path": "/a.txt", "text": "mine\n", "stamp": stamp})
        self.assertEqual(status, 200)
        self.assertEqual((self.root / "a.txt").read_text(), "mine\n")

    def test_a_save_with_a_stale_stamp_is_refused(self):
        """Another editor, a git checkout, or a second browser tab. Writing
        anyway silently destroys whatever arrived in between."""
        stamp = self.read()["stamp"]
        (self.root / "a.txt").write_text("someone else's work\n")
        code, body = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/a.txt", "text": "mine\n", "stamp": stamp})
        )
        self.assertEqual(code, 409)
        self.assertIn("changed on disk", body["error"])
        self.assertEqual((self.root / "a.txt").read_text(), "someone else's work\n")

    def test_a_save_without_a_stamp_still_works(self):
        """Creating a file has nothing to compare against."""
        status, _ = self.post_json("/api/file", {"path": "/new.txt", "text": "fresh\n"})
        self.assertEqual(status, 200)

    def test_the_stamp_changes_when_the_file_does(self):
        first = self.read()["stamp"]
        (self.root / "a.txt").write_text("different\n")
        self.assertNotEqual(self.read()["stamp"], first)

    def test_two_saves_in_the_same_second_are_still_distinguished(self):
        """mtime has one-second granularity on some filesystems, and two
        saves inside one second is precisely the case being guarded."""
        stamp = self.read()["stamp"]
        self.post_json("/api/file", {"path": "/a.txt", "text": "second\n", "stamp": stamp})
        code, _ = self.expect_failure(
            lambda: self.post_json("/api/file", {"path": "/a.txt", "text": "third\n", "stamp": stamp})
        )
        self.assertEqual(code, 409)


class ManageTest(ServerCase):
    writable = True

    def prepare(self):
        (self.root / "here.txt").write_text("x\n")
        (self.root / "dir").mkdir()

    def test_it_creates_a_file(self):
        self.post_json("/api/create", {"path": "/made.txt", "kind": "file"})
        self.assertTrue((self.root / "made.txt").is_file())

    def test_it_creates_a_folder(self):
        self.post_json("/api/create", {"path": "/made", "kind": "folder"})
        self.assertTrue((self.root / "made").is_dir())

    def test_creating_over_something_is_refused(self):
        code, _ = self.expect_failure(
            lambda: self.post_json("/api/create", {"path": "/here.txt", "kind": "file"})
        )
        self.assertEqual(code, 409)
        self.assertEqual((self.root / "here.txt").read_text(), "x\n")

    def test_a_refusal_does_not_reveal_where_the_root_sits(self):
        """Letting the OSError through would answer with
        "[Errno 17] File exists: '/tmp/xyz/here.txt'". Which folder is being
        served is not the browser's business, and an error is the easiest
        place for it to leak."""
        code, body = self.expect_failure(
            lambda: self.post_json("/api/create", {"path": "/here.txt", "kind": "file"})
        )
        self.assertEqual(code, 409)
        self.assertNotIn(str(self.root), body["error"])
        self.assertNotIn("Errno", body["error"])

    def test_it_renames(self):
        self.post_json("/api/rename", {"from": "/here.txt", "to": "/there.txt"})
        self.assertFalse((self.root / "here.txt").exists())
        self.assertEqual((self.root / "there.txt").read_text(), "x\n")

    def test_renaming_over_something_is_refused(self):
        (self.root / "other.txt").write_text("keep\n")
        code, _ = self.expect_failure(
            lambda: self.post_json("/api/rename", {"from": "/here.txt", "to": "/other.txt"})
        )
        self.assertEqual(code, 409)
        self.assertEqual((self.root / "other.txt").read_text(), "keep\n")

    def test_it_deletes_a_file(self):
        self.post_json("/api/delete", {"path": "/here.txt"})
        self.assertFalse((self.root / "here.txt").exists())

    def test_it_deletes_an_empty_folder(self):
        self.post_json("/api/delete", {"path": "/dir"})
        self.assertFalse((self.root / "dir").exists())

    def test_a_full_folder_is_not_deleted(self):
        """One mis-click in a file browser must not be able to destroy a
        tree. Anyone who means it has a shell."""
        (self.root / "dir" / "keep.txt").write_text("precious\n")
        code, body = self.expect_failure(lambda: self.post_json("/api/delete", {"path": "/dir"}))
        self.assertEqual(code, 409)
        self.assertIn("not empty", body["error"])
        self.assertEqual((self.root / "dir" / "keep.txt").read_text(), "precious\n")

    def test_the_served_root_cannot_be_deleted(self):
        code, _ = self.expect_failure(lambda: self.post_json("/api/delete", {"path": "/"}))
        self.assertEqual(code, 400)
        self.assertTrue(self.root.is_dir())

    def test_nothing_outside_the_root_can_be_touched(self):
        outside = self.root.parent / "victim.txt"
        outside.write_text("safe\n")
        (self.root / "link.txt").symlink_to(outside)
        for route, payload in (
            ("/api/delete", {"path": "/link.txt"}),
            ("/api/rename", {"from": "/link.txt", "to": "/moved.txt"}),
            ("/api/create", {"path": "/link.txt", "kind": "file"}),
        ):
            code, _ = self.expect_failure(lambda: self.post_json(route, payload))
            self.assertIn(code, (403, 409), route)
        self.assertEqual(outside.read_text(), "safe\n")


class ManageReadOnlyTest(ServerCase):
    writable = False

    def prepare(self):
        (self.root / "here.txt").write_text("x\n")

    def test_every_mutating_endpoint_is_refused(self):
        for route, payload in (
            ("/api/create", {"path": "/made.txt", "kind": "file"}),
            ("/api/rename", {"from": "/here.txt", "to": "/there.txt"}),
            ("/api/delete", {"path": "/here.txt"}),
        ):
            code, body = self.expect_failure(lambda: self.post_json(route, payload))
            self.assertEqual(code, 403, route)
            self.assertIn("--write", body["error"], route)
        self.assertTrue((self.root / "here.txt").exists())
        self.assertFalse((self.root / "made.txt").exists())


class GitAbsentTest(ServerCase):
    """A plain folder with no repository anywhere above it."""

    def setUp(self):
        super().setUp()

    def prepare(self):
        (self.root / "a.txt").write_text("x\n")

    def test_status_answers_rather_than_failing(self):
        """The page asks on every load so the tree can show change marks. A
        404 here puts a console error in front of everyone serving a plain
        folder, which is most people."""
        import subprocess

        # A temp dir can sit inside someone's checkout; make sure it does not.
        inside = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
            capture_output=True,
        )
        if inside.returncode == 0:
            self.skipTest("the temp directory is inside a repository")
        status, body = self.get_json("/api/git/status")
        self.assertEqual(status, 200)
        self.assertFalse(body["repo"])
        self.assertEqual(body["files"], [])


class RequestFramingTest(ServerCase):
    """Bodies in every framing a client or proxy may use, over one kept-alive
    connection — which is where a body left unread does its damage.

    Reverse proxies may forward GETs with chunked empty bodies. Every body
    must be consumed before parsing the next request on the connection."""

    writable = True

    def raw(self, payload, expect):
        """Send raw bytes on one connection; return the status lines."""
        import socket
        import time

        connection = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        connection.sendall(payload)
        received = b""
        end = time.time() + 5
        while received.count(b"HTTP/1.1 ") < expect and time.time() < end:
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            received += chunk
        connection.close()
        # Matched by pattern, not by line: a response body has no trailing
        # newline, so the next status line follows it directly.
        return re.findall(r"HTTP/1\.1 \d{3} [^\r]*", received.decode("latin-1"))

    def host(self):
        return b"Host: 127.0.0.1:%d\r\n" % self.port

    def test_a_get_with_an_empty_chunked_body_leaves_the_connection_clean(self):
        statuses = self.raw(
            b"GET /api/config HTTP/1.1\r\n" + self.host() + b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
            + b"GET /api/config HTTP/1.1\r\n" + self.host() + b"\r\n",
            expect=2,
        )
        self.assertEqual(statuses, ["HTTP/1.1 200 OK", "HTTP/1.1 200 OK"])

    def test_a_chunked_save_is_read_whole(self):
        body = b'{"path": "/chunked.txt", "text": "arrived in pieces\\n"}'
        first, second = body[:20], body[20:]
        chunked = b"%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (len(first), first, len(second), second)
        statuses = self.raw(
            b"POST /api/file HTTP/1.1\r\n" + self.host()
            + b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n" + chunked,
            expect=1,
        )
        self.assertEqual(statuses, ["HTTP/1.1 200 OK"])
        self.assertEqual((self.root / "chunked.txt").read_text(), "arrived in pieces\n")

    def test_a_get_with_a_content_length_body_does_not_poison_the_next(self):
        statuses = self.raw(
            b"GET /api/config HTTP/1.1\r\n" + self.host() + b"Content-Length: 5 \t\r\n\r\nhello"
            + b"GET /api/config HTTP/1.1\r\n" + self.host() + b"\r\n",
            expect=2,
        )
        self.assertEqual(statuses, ["HTTP/1.1 200 OK", "HTTP/1.1 200 OK"])

    def test_chunk_trailers_are_consumed(self):
        statuses = self.raw(
            b"GET /api/config HTTP/1.1\r\n" + self.host()
            + b"Transfer-Encoding: chunked\r\n\r\n0\r\nX-Trailer: yes\r\n\r\n"
            + b"GET /api/config HTTP/1.1\r\n" + self.host() + b"\r\n",
            expect=2,
        )
        self.assertEqual(statuses, ["HTTP/1.1 200 OK", "HTTP/1.1 200 OK"])

    def test_an_oversized_chunked_body_is_refused(self):
        from ficelo.server import MAX_UPLOAD_BYTES

        size = MAX_UPLOAD_BYTES + 1
        statuses = self.raw(
            b"POST /api/file HTTP/1.1\r\n" + self.host()
            + b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
            + b"%x\r\n" % size,
            expect=1,
        )
        self.assertEqual(statuses, ["HTTP/1.1 413 Request Entity Too Large"])

    def test_a_malformed_chunk_size_is_a_400_not_a_hang(self):
        for size in (b"zz", b"-1", b"+1", b"0x1", b"1_0"):
            with self.subTest(size=size):
                statuses = self.raw(
                    b"POST /api/file HTTP/1.1\r\n" + self.host()
                    + b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
                    + size + b"\r\n",
                    expect=1,
                )
                self.assertEqual(statuses, ["HTTP/1.1 400 Bad Request"])

    def test_an_invalid_content_length_is_refused(self):
        for length in (b"-1", b"+1", b"1_0"):
            with self.subTest(length=length):
                statuses = self.raw(
                    b"GET /api/config HTTP/1.1\r\n" + self.host()
                    + b"Content-Length: " + length + b"\r\n\r\n",
                    expect=1,
                )
                self.assertEqual(statuses, ["HTTP/1.1 400 Bad Request"])

    def test_a_chunk_without_its_terminator_is_refused(self):
        statuses = self.raw(
            b"GET /api/config HTTP/1.1\r\n" + self.host()
            + b"Transfer-Encoding: chunked\r\n\r\n1\r\nx!!",
            expect=1,
        )
        self.assertEqual(statuses, ["HTTP/1.1 400 Bad Request"])


class StateEndpointTest(ServerCase):
    """The page's settings, kept in the server's state folder."""

    writable = False  # deliberately: settings must save on a read-only server

    def state_dir(self):
        return self.server.config.state_dir

    def test_nothing_saved_reads_as_empty(self):
        _, body = self.get_json("/api/state")
        self.assertEqual(body["settings"], {})

    def test_a_setting_round_trips(self):
        self.post_json("/api/state", {"key": "ficelo.theme", "value": "dark"})
        _, body = self.get_json("/api/state")
        self.assertEqual(body["settings"], {"ficelo.theme": "dark"})

    def test_it_lands_in_the_state_folder_not_the_served_tree(self):
        self.post_json("/api/state", {"key": "ficelo.theme", "value": "dark"})
        self.assertTrue((self.state_dir() / "settings.json").is_file())
        self.assertEqual(list(self.root.iterdir()), [], "nothing written into the served tree")

    def test_a_read_only_server_still_saves_settings(self):
        """Read-only is a promise about the served files; ficelo's own
        settings are not those."""
        self.assertFalse(self.server.config.writable)
        status, _ = self.post_json("/api/state", {"key": "ficelo.x", "value": 1})
        self.assertEqual(status, 200)

    def test_a_bad_key_is_400(self):
        code, body = self.expect_failure(
            lambda: self.post_json("/api/state", {"key": "../../etc/passwd", "value": 1})
        )
        self.assertEqual(code, 400)
        self.assertIn("key", body["error"])

    def test_another_website_cannot_change_settings(self):
        request = urllib.request.Request(
            self.url("/api/state"),
            data=b'{"key": "ficelo.x", "value": 1}',
            headers={"Content-Type": "text/plain", "Origin": "http://evil.example"},
            method="POST",
        )
        code, _ = self.expect_failure(lambda: urllib.request.urlopen(request, timeout=5))
        self.assertEqual(code, 415)
        self.assertFalse((self.state_dir() / "settings.json").exists())


class CrossSiteTest(ServerCase):
    """Another website, open in the same browser, must not reach ficelo.

    Both attacks below worked against ficelo before this class existed:
    a text/plain POST wrote a file, and a request addressed to an attacker's
    hostname read one. With the root at "/" that is the whole disk."""

    writable = True

    def prepare(self):
        (self.root / "secret.txt").write_text("secret\n")

    def send(self, path, method="GET", body=None, headers=None):
        request = urllib.request.Request(
            self.url(path), data=body, headers=headers or {}, method=method
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as res:
                return res.status
        except urllib.error.HTTPError as err:
            return err.code

    def test_a_form_style_post_from_another_site_writes_nothing(self):
        """text/plain is what a browser sends cross-site without asking."""
        code = self.send(
            "/api/file",
            "POST",
            b'{"path": "/pwned.txt", "text": "x"}',
            {"Content-Type": "text/plain", "Origin": "http://evil.example"},
        )
        self.assertEqual(code, 415)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_a_json_post_naming_another_origin_writes_nothing(self):
        code = self.send(
            "/api/file",
            "POST",
            b'{"path": "/pwned.txt", "text": "x"}',
            {"Content-Type": "application/json", "Origin": "http://evil.example"},
        )
        self.assertEqual(code, 403)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_a_post_the_browser_marks_cross_site_writes_nothing(self):
        code = self.send(
            "/api/file",
            "POST",
            b'{"path": "/pwned.txt", "text": "x"}',
            {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(code, 403)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_the_page_itself_can_still_save(self):
        """Same-origin JSON, which is what the frontend sends."""
        code = self.send(
            "/api/file",
            "POST",
            b'{"path": "/ok.txt", "text": "fine"}',
            {
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1:%d" % self.port,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual((self.root / "ok.txt").read_text(), "fine")

    def test_a_rebound_hostname_cannot_read(self):
        """DNS rebinding: the attacker's name now resolves to 127.0.0.1, so
        the browser calls it same-origin — but still sends that name."""
        code = self.send("/api/file?path=/secret.txt", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(code, 403)

    def test_configured_browser_origin_can_save_through_a_rewritten_host(self):
        self.server.config.host = "0.0.0.0"
        self.server.config.allowed_origins = Config(allowed_origins=["https://files.example.test/"]).allowed_origins
        headers = {"Host": "proxy.example.test", "Origin": "https://files.example.test",
                   "Content-Type": "application/json"}
        # Plain HTTP proxy hops may not include Fetch Metadata headers.
        code = self.send("/api/state", "POST", b'{"key":"ficelo.proxy","value":{"saved":true}}', headers)
        self.assertEqual(code, 200)
        self.assertEqual(self.server.settings.read()["ficelo.proxy"], {"saved": True})
        self.assertEqual(self.send("/api/watch", "POST", b'{"paths":[]}', headers), 200)

    def test_proxy_origin_allowlist_does_not_grant_other_sites_access(self):
        self.server.config.host = "0.0.0.0"
        self.server.config.allowed_origins = Config(allowed_origins=["https://files.example.test"]).allowed_origins
        for origin in ["https://evil.example.test", "http://files.example.test",
                       "https://files.example.test:8443", "https://files.example.test.evil.test"]:
            with self.subTest(origin=origin):
                code = self.send("/api/file", "POST", b'{"path":"/pwned.txt","text":"x"}', {
                    "Host": "proxy.example.test", "Origin": origin,
                    "X-Forwarded-Host": "files.example.test", "Content-Type": "application/json"})
                self.assertEqual(code, 403)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_configured_origin_does_not_override_cross_site_or_json_checks(self):
        self.server.config.host = "0.0.0.0"
        self.server.config.allowed_origins = Config(allowed_origins=["https://files.example.test"]).allowed_origins
        for extra, status in [({"Content-Type": "text/plain"}, 415),
                              ({"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}, 403)]:
            with self.subTest(extra=extra):
                code = self.send("/api/file", "POST", b'{"path":"/pwned.txt","text":"x"}', {
                    "Host": "proxy.example.test", "Origin": "https://files.example.test", **extra})
                self.assertEqual(code, status)
        self.assertFalse((self.root / "pwned.txt").exists())

    def test_configured_origin_does_not_bypass_loopback_host_protection(self):
        self.server.config.allowed_origins = Config(allowed_origins=["https://files.example.test"]).allowed_origins
        self.assertEqual(self.send("/api/state", "POST", b'{"key":"ficelo.proxy","value":1}', {
            "Host": "evil.example.test", "Origin": "https://files.example.test", "Content-Type": "application/json"}), 403)

    def test_host_rewriting_requires_explicit_configuration(self):
        self.server.config.host = "0.0.0.0"
        self.assertEqual(self.send("/api/watch", "POST", b'{"paths":[]}', {
            "Host": "proxy.example.test", "Origin": "https://files.example.test", "Content-Type": "application/json"}), 403)

    def test_a_rebound_hostname_cannot_load_the_page_either(self):
        code = self.send("/", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)

    def test_every_loopback_spelling_is_accepted(self):
        for host in ("localhost", "127.0.0.1", "[::1]", "app.localhost"):
            header = "%s:%d" % (host, self.port)
            self.assertEqual(
                self.send("/api/config", headers={"Host": header}), 200, header
            )


class ExposedHostTest(ServerCase):
    """Once --host exposes the server, the names that reach it cannot be
    known in advance — a proxy, a VPN hostname — so Host is not checked."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        config = Config(root=self.root, host="0.0.0.0", port=0, state_dir=tempfile.mkdtemp())
        self.server = serve(config, quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.stop)

    def test_any_hostname_is_answered(self):
        request = urllib.request.Request(
            self.url("/api/config"), headers={"Host": "devbox.example.net:%d" % self.port}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as res:
            self.assertEqual(res.status, 200)


class HostNameTest(unittest.TestCase):
    def test_it_strips_ports_and_brackets(self):
        from ficelo.server import host_name

        self.assertEqual(host_name("LocalHost:8777"), "localhost")
        self.assertEqual(host_name("[::1]:8777"), "::1")
        self.assertEqual(host_name("127.0.0.1"), "127.0.0.1")
        self.assertEqual(host_name("evil.example:80"), "evil.example")


class RawTest(ServerCase):
    PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    def prepare(self):
        (self.root / "pic.png").write_bytes(self.PNG)
        (self.root / "page.html").write_text("<script>alert(1)</script>")
        (self.root / "drawing.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        (self.root / "doc.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    def test_an_image_comes_back_as_itself(self):
        status, body, headers = self.get("/api/raw?path=/pic.png")
        self.assertEqual(status, 200)
        self.assertEqual(body, self.PNG)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Disposition"], "inline")

    def test_a_pdf_is_inline_so_the_browser_can_show_it(self):
        _, _, headers = self.get("/api/raw?path=/doc.pdf")
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertEqual(headers["Content-Disposition"], "inline")

    def test_html_is_a_download_not_a_page(self):
        """Otherwise any .html in the tree becomes script running against
        this origin, with access to everything the page can reach."""
        _, _, headers = self.get("/api/raw?path=/page.html")
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_svg_is_a_download_too(self):
        """An SVG is an image that can carry script, which is why it is not
        on the inline list even though every other image is."""
        _, _, headers = self.get("/api/raw?path=/drawing.svg")
        self.assertEqual(headers["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_a_quote_in_the_filename_cannot_break_the_header(self):
        (self.root / 'od"d.bin').write_bytes(b"x")
        _, _, headers = self.get("/api/raw?path=/od%22d.bin")
        self.assertNotIn('"', headers["Content-Disposition"].split("filename=")[1][1:-1])

    def test_it_cannot_escape_the_root(self):
        outside = self.root.parent / "secret.png"
        outside.write_bytes(b"secret")
        (self.root / "link.png").symlink_to(outside)
        code, _ = self.expect_failure(lambda: self.get("/api/raw?path=/link.png"))
        self.assertEqual(code, 403)

    def test_a_missing_file_is_404(self):
        code, _ = self.expect_failure(lambda: self.get("/api/raw?path=/nope.png"))
        self.assertEqual(code, 404)


class AssetTest(ServerCase):
    def test_the_page_is_served_at_the_root(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>ficelo</title>", body)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))

    def test_the_header_icon_opens_the_project_page_in_a_new_tab(self):
        """The icon left of the breadcrumb is a plain link, so middle-click and
        keyboard activation work. `noreferrer` keeps this server's address out
        of the request GitHub receives."""
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.found = [], None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "a":
                    self.stack.append(attrs)
                if tag == "img" and "brand" in (attrs.get("class") or "").split() and self.stack:
                    self.found = self.stack[-1]

            def handle_endtag(self, tag):
                if tag == "a" and self.stack:
                    self.stack.pop()

        _, page, _ = self.get("/")
        parser = Links()
        parser.feed(page.decode("utf-8"))
        link = parser.found
        self.assertIsNotNone(link, "the header icon should be wrapped in a link")
        self.assertEqual(link["href"], "https://github.com/lichenran1234/ficelo-ide")
        self.assertEqual(link["target"], "_blank")
        self.assertLessEqual({"noopener", "noreferrer"}, set(link["rel"].split()))

    def test_every_asset_the_page_asks_for_exists(self):
        """Catches a rename on one side only, which a browser reports as a
        blank page and nothing else does. The list is read out of the page
        rather than written here, so moving a file keeps being caught instead
        of quietly making this test check nothing."""
        _, page, _ = self.get("/")
        refs = re.findall(rb'(?:src|href)="(/[^"]+)"', page)
        self.assertGreaterEqual(len(refs), 4, "the page should pull in its own assets")
        for ref in refs:
            route = ref.decode()
            status, body, headers = self.get(route)
            self.assertEqual(status, 200, route)
            self.assertGreater(len(body), 0, route)
            self.assertNotEqual(headers["Content-Type"], "application/octet-stream", route)

    def test_the_javascript_modules_resolve_each_other(self):
        """An ES module graph fails at the first bad import with nothing in
        the DOM to show for it. Walking the imports here turns that into a
        test failure naming the file."""
        seen, queue = set(), ["/js/app.js"]
        while queue:
            route = queue.pop()
            if route in seen:
                continue
            seen.add(route)
            status, body, _ = self.get(route)
            self.assertEqual(status, 200, route)
            base = route.rsplit("/", 1)[0]
            for target in re.findall(rb'from\s+"(\.[^"]+)"', body):
                resolved = posixpath.normpath(base + "/" + target.decode())
                queue.append(resolved)
        self.assertGreater(len(seen), 4, "app.js should import its siblings")

    def test_a_missing_asset_is_404(self):
        code, _ = self.expect_failure(lambda: self.get("/nope.js"))
        self.assertEqual(code, 404)

    def test_the_vendored_libraries_are_served(self):
        """A missing or half-copied vendor tree is otherwise invisible until a
        browser loads the page and shows nothing."""
        for route in (
            "/vendor/monaco/vs/loader.js",
            "/vendor/monaco/vs/editor/editor.main.js",
            "/vendor/monaco/vs/editor/editor.main.css",
            "/vendor/marked/marked.umd.js",
        ):
            status, body, _ = self.get(route)
            self.assertEqual(status, 200, route)
            self.assertGreater(len(body), 500, route)

    def test_every_vendored_library_ships_its_licence(self):
        """Redistributing MIT code without its notice is the one licensing
        mistake this project can make on its own."""
        for route in ("/vendor/monaco/LICENSE", "/vendor/marked/LICENSE"):
            status, body, _ = self.get(route)
            self.assertEqual(status, 200, route)
            self.assertIn(b"permission notice", body, route)

    def test_the_two_licences_are_not_the_same_file(self):
        """They were, briefly: two tarballs extracted into one staging
        directory, and the second package's LICENSE overwrote the first."""
        _, monaco, _ = self.get("/vendor/monaco/LICENSE")
        _, marked, _ = self.get("/vendor/marked/LICENSE")
        self.assertNotEqual(monaco, marked)
        self.assertIn(b"Microsoft", monaco)
        self.assertIn(b"MarkedJS", marked)

    def test_the_page_may_frame_its_own_origin(self):
        """The PDF viewer is an <embed> pointing at /api/raw here; DENY would
        blank it."""
        _, _, headers = self.get("/")
        self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")

    def test_nothing_is_cached(self):
        """The tree changes under the browser constantly."""
        _, _, headers = self.get("/api/config")
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
