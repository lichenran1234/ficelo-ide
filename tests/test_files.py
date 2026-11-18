import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import files  # noqa: E402


class ListingTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "Zebra").mkdir()
        (self.root / "apple").mkdir()
        (self.root / "Banana.txt").write_text("b\n")
        (self.root / "cherry.txt").write_text("c\n")
        (self.root / ".hidden").write_text("h\n")

    def names(self, **kw):
        return [e["name"] for e in files.listing(self.root, self.root, **kw)]

    def test_folders_come_first(self):
        self.assertEqual(self.names()[:2], ["apple", "Zebra"])

    def test_sorting_ignores_case(self):
        """Otherwise every capitalised name clumps above every lowercase one,
        which is not how anyone reads a folder."""
        self.assertEqual(self.names(), ["apple", "Zebra", "Banana.txt", "cherry.txt"])

    def test_hidden_entries_are_left_out_by_default(self):
        self.assertNotIn(".hidden", self.names())

    def test_and_included_when_asked_for(self):
        self.assertIn(".hidden", self.names(show_hidden=True))

    def test_paths_are_relative_to_the_root(self):
        by_name = {e["name"]: e for e in files.listing(self.root, self.root)}
        self.assertEqual(by_name["apple"]["path"], "/apple")
        self.assertEqual(by_name["cherry.txt"]["path"], "/cherry.txt")

    def test_every_entry_is_described(self):
        self.assertEqual(len(files.listing(self.root, self.root)), 4)

    def test_editing_preserves_an_existing_utf8_bom(self):
        target = self.root / "note.md"
        target.write_bytes(b"\xef\xbb\xbf# Heading\n")
        self.assertEqual(files.read_text(target)[0], "# Heading\n")
        files.write_text(target, "# Changed\n", expect=files.stamp(target))
        self.assertEqual(target.read_bytes(), b"\xef\xbb\xbf# Changed\n")
        plain = self.root / "plain.md"
        files.write_text(plain, "# Plain\n")
        self.assertEqual(plain.read_bytes(), b"# Plain\n")


class DescribeTest(unittest.TestCase):
    """A dead mount, or a file removed between the scan and the stat. A folder
    that silently drops an entry is worse than one showing a broken entry,
    because nothing tells you it happened.

    Driven with a stand-in rather than a patched Path: the listing iterates
    os.DirEntry, which is a C type and cannot be patched — an earlier version
    of this test patched Path.stat, which the code under test never calls, and
    passed for the wrong reason."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def dead_entry(self):
        root = self.root

        class Dead:
            name = "dead-mount"
            path = str(root / "dead-mount")

            def stat(self):
                raise OSError(107, "Transport endpoint is not connected")

            def is_dir(self):
                raise OSError(107, "Transport endpoint is not connected")

        return Dead()

    def test_it_answers_with_the_reason(self):
        got = files.describe(self.dead_entry(), self.root)
        self.assertIn("Transport endpoint", got["error"])

    def test_it_is_named_and_placed_like_any_other(self):
        got = files.describe(self.dead_entry(), self.root)
        self.assertEqual(got["name"], "dead-mount")
        self.assertEqual(got["path"], "/dead-mount")

    def test_it_claims_to_be_neither_a_folder_nor_a_size(self):
        got = files.describe(self.dead_entry(), self.root)
        self.assertFalse(got["is_dir"])
        self.assertEqual(got["size"], 0)
        self.assertIsNone(got["modified"])

    def test_a_healthy_entry_carries_no_error(self):
        (self.root / "fine.txt").write_text("x\n")
        with os.scandir(self.root) as scan:
            entry = next(iter(scan))
        self.assertNotIn("error", files.describe(entry, self.root))


class BinarySniffTest(unittest.TestCase):
    def test_a_nul_settles_it(self):
        self.assertTrue(files.looks_binary(b"hello\x00world"))

    def test_plain_text_is_text(self):
        self.assertFalse(files.looks_binary(b"def main():\n    return 1\n"))

    def test_empty_is_text(self):
        """An empty file opens in the editor, not as a download."""
        self.assertFalse(files.looks_binary(b""))

    def test_utf8_prose_is_text(self):
        """High bytes are normal in UTF-8 and must not count against it."""
        self.assertFalse(files.looks_binary("héllo wörld — a note\n".encode("utf-8")))

    def test_control_character_soup_is_binary(self):
        self.assertTrue(files.looks_binary(bytes(range(1, 32)) * 4))

    def test_ansi_escapes_are_still_text(self):
        """Logs are full of colour codes and are the thing most worth reading."""
        self.assertFalse(files.looks_binary(b"\x1b[32mpassed\x1b[0m\n" * 20))


class ReadTextTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_it_reads_a_file(self):
        path = self.root / "a.txt"
        path.write_text("hello\n")
        text, truncated, binary = files.read_text(path)
        self.assertEqual(text, "hello\n")
        self.assertFalse(truncated)
        self.assertFalse(binary)

    def test_a_binary_file_is_reported_not_decoded(self):
        path = self.root / "a.bin"
        path.write_bytes(b"\x00\x01\x02" * 100)
        text, _, binary = files.read_text(path)
        self.assertTrue(binary)
        self.assertIsNone(text)

    def test_a_large_file_is_cut_short_and_says_so(self):
        path = self.root / "big.txt"
        path.write_text("x" * (files.MAX_TEXT_BYTES + 1000))
        text, truncated, _ = files.read_text(path)
        self.assertTrue(truncated)
        self.assertEqual(len(text), files.MAX_TEXT_BYTES)

    def test_a_file_at_the_limit_is_not_called_truncated(self):
        path = self.root / "exact.txt"
        path.write_text("x" * files.MAX_TEXT_BYTES)
        text, truncated, _ = files.read_text(path)
        self.assertFalse(truncated)
        self.assertEqual(len(text), files.MAX_TEXT_BYTES)

    def test_one_bad_byte_costs_a_character_not_the_file(self):
        path = self.root / "mixed.txt"
        path.write_bytes(b"before \xff after\n")
        text, _, binary = files.read_text(path)
        self.assertFalse(binary)
        self.assertIn("before", text)
        self.assertIn("after", text)


class WriteTextTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.path = self.root / "note.txt"

    def test_it_writes_and_reports_the_byte_count(self):
        written = files.write_text(self.path, "hello\n")
        self.assertEqual(self.path.read_text(), "hello\n")
        self.assertEqual(written, 6)

    def test_bytes_not_characters(self):
        """The count is what landed on disk, which for anything non-ASCII is
        not the length of the string."""
        written = files.write_text(self.path, "é")
        self.assertEqual(written, 2)

    def test_it_replaces_rather_than_appends(self):
        files.write_text(self.path, "first\n")
        files.write_text(self.path, "second\n")
        self.assertEqual(self.path.read_text(), "second\n")

    def test_it_can_empty_a_file(self):
        files.write_text(self.path, "content\n")
        files.write_text(self.path, "")
        self.assertEqual(self.path.read_text(), "")

    def test_it_keeps_the_existing_permissions(self):
        """Saving a private file from a browser must not widen it, and saving
        a script must not cost it the execute bit."""
        self.path.write_text("x\n")
        os.chmod(self.path, 0o700)
        files.write_text(self.path, "y\n")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)

    def test_a_failed_write_leaves_the_old_contents_and_no_litter(self):
        """The reason for the rename. A direct open(path, 'w') truncates
        before it writes, so the same failure would lose the file."""
        self.path.write_text("precious\n")
        before = set(os.listdir(self.root))

        class Boom(str):
            def encode(self, *a, **kw):
                raise MemoryError("no room")

        with self.assertRaises(MemoryError):
            files.write_text(self.path, Boom("new"))
        self.assertEqual(self.path.read_text(), "precious\n")
        self.assertEqual(set(os.listdir(self.root)), before)

    def test_an_interrupted_write_leaves_the_old_contents_and_no_litter(self):
        """The same guarantee one step later: the temporary exists and has
        been partly written when the failure lands."""
        self.path.write_text("precious\n")
        before = set(os.listdir(self.root))
        real = os.replace

        def refuse(*a, **kw):
            raise OSError(28, "No space left on device")

        os.replace = refuse
        try:
            with self.assertRaises(OSError):
                files.write_text(self.path, "new\n")
        finally:
            os.replace = real
        self.assertEqual(self.path.read_text(), "precious\n")
        self.assertEqual(set(os.listdir(self.root)), before, "the temporary was cleaned up")

    def test_it_creates_a_file_that_was_not_there(self):
        files.write_text(self.root / "new.txt", "fresh\n")
        self.assertEqual((self.root / "new.txt").read_text(), "fresh\n")

    def test_a_missing_folder_is_an_error_not_a_silent_no_op(self):
        with self.assertRaises(FileNotFoundError):
            files.write_text(self.root / "nope" / "a.txt", "x")


if __name__ == "__main__":
    unittest.main()
