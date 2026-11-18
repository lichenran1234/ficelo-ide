import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import cli, server  # noqa: E402
from ficelo.config import Config, normalise_origin  # noqa: E402


class DefaultsTest(unittest.TestCase):
    def test_the_whole_filesystem_is_served_by_default(self):
        self.assertEqual(Config().root, Path(os.path.realpath("/")))

    def test_local_read_write_is_the_default(self):
        config = Config()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 9248)
        self.assertEqual(config.url, "http://127.0.0.1:9248")
        self.assertTrue(config.writable)
        self.assertFalse(config.terminal)
        self.assertFalse(config.reachable_beyond_this_machine)

    def test_explicit_read_only_disables_writes_and_terminals(self):
        config = Config(writable=False, terminal=True)
        self.assertFalse(config.writable)
        self.assertFalse(config.terminal)
        self.assertIn("mode     read-only", config.describe())

    def test_the_tree_opens_at_the_home_folder(self):
        """Served from "/", home is a folder inside the root, and the page
        starts there rather than at the top of the disk."""
        with tempfile.TemporaryDirectory() as fake_home:
            with mock.patch.dict(os.environ, {"HOME": fake_home}):
                expected = "/" + str(Path(os.path.realpath(fake_home))).lstrip("/")
                self.assertEqual(Config().home_request(), expected)

    def test_a_narrower_root_without_home_in_it_opens_at_its_top(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(Config(root=root).home_request(), "/")

    def test_the_command_line_default_matches(self):
        args = cli.build_parser().parse_args([])
        self.assertIsNone(args.root, "no root given means the configured default")
        self.assertIsNone(args.write, "an omitted mode preserves the saved setting")

    def test_conflicting_launch_modes_are_rejected(self):
        for flags in (["--write", "--read-only"], ["--read-only", "--write"]):
            with self.subTest(flags=flags), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                cli.build_parser().parse_args(flags)
            self.assertEqual(caught.exception.code, 2)


class PlatformTest(unittest.TestCase):
    def test_windows_is_refused_with_a_reason(self):
        """Refused outright rather than half-working: paths, hidden files
        and saving are written for Linux and macOS."""
        err = io.StringIO()
        # If the refusal ever goes missing, main() would carry on and start a
        # real server that never returns; this makes that a fast failure
        # instead of a hung test run, which is what happened once.
        started = mock.patch.object(cli, "serve", side_effect=AssertionError("Windows was not refused"))
        with mock.patch.object(sys, "platform", "win32"), redirect_stderr(err), started:
            code = cli.main([])
        self.assertEqual(code, 2)
        self.assertIn("Windows is not supported", err.getvalue())

    def test_macos_explains_a_permission_refusal(self):
        """macOS blocks Desktop, Documents and Downloads from a terminal
        program until it is allowed; a bare "not allowed" reads like a bug."""
        with mock.patch.object(sys, "platform", "darwin"):
            message = server.denied("read that folder")
        self.assertIn("Full Disk Access", message)

    def test_linux_does_not_mention_macos(self):
        with mock.patch.object(sys, "platform", "linux"):
            message = server.denied("read that folder")
        self.assertEqual(message, "not allowed to read that folder")


class OriginConfigTest(unittest.TestCase):
    def test_exact_origins_normalise_default_ports_case_and_ipv6(self):
        self.assertEqual(normalise_origin("https://Files.Example:443/"), "https://files.example")
        self.assertEqual(normalise_origin("http://[::1]:8777"), "http://[::1]:8777")
        self.assertEqual(normalise_origin("http://files.example:80"), "http://files.example")
        self.assertEqual(normalise_origin("https://xn--fa-hia.de"), "https://xn--fa-hia.de")
        self.assertEqual(Config().allowed_origins, frozenset())

    def test_unicode_domains_require_the_browser_punycode_origin(self):
        for value in ["https://faß.de", "https://K.example"]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "punycode"):
                normalise_origin(value)

    def test_unsafe_or_inexact_origin_settings_are_refused(self):
        for value in ["*", "null", "file:///tmp/page", "https://*.example", "https://user:pass@example.test",
                      "https://example.test/path", "https://example.test?query", "https://example.test/#fragment",
                      "https://example.test:90000", "https://example.test:0", "https://example.test\n", 1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Config(allowed_origins=[value])
        with self.assertRaises(TypeError):
            Config(allowed_origins="https://example.test")


if __name__ == "__main__":
    unittest.main()
