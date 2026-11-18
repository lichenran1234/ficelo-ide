import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import paths  # noqa: E402


class NormaliseTest(unittest.TestCase):
    def test_a_request_is_always_absolute(self):
        self.assertEqual(paths.normalise("a/b"), "/a/b")
        self.assertEqual(paths.normalise("/a/b"), "/a/b")
        self.assertEqual(paths.normalise(""), "/")
        self.assertEqual(paths.normalise(None), "/")

    def test_dots_are_collapsed_textually(self):
        self.assertEqual(paths.normalise("/a/./b"), "/a/b")
        self.assertEqual(paths.normalise("/a/b/../c"), "/a/c")
        self.assertEqual(paths.normalise("/a/b/../../c"), "/c")

    def test_climbing_past_the_top_stops_at_the_top(self):
        """`..` at the root has nowhere to go. Left to the filesystem this is
        how a request reaches the parent of the root."""
        for climb in ("/..", "/../..", "/../../etc/passwd", "../../etc/passwd"):
            self.assertTrue(
                paths.normalise(climb) in ("/", "/etc/passwd"), climb
            )
        self.assertEqual(paths.normalise("/../.."), "/")
        self.assertEqual(paths.normalise("/a/../../.."), "/")

    def test_backslashes_are_separators_too(self):
        """Otherwise `..\\..` arrives as one harmless-looking filename."""
        self.assertEqual(paths.normalise("/a\\..\\b"), "/b")


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        (self.root / "sub").mkdir(parents=True)
        (self.root / "sub" / "file.txt").write_text("hello\n")
        (self.tmp / "outside.txt").write_text("secret\n")

    def test_a_path_inside_the_root_resolves(self):
        got = paths.resolve("/sub/file.txt", self.root)
        self.assertEqual(got.read_text(), "hello\n")

    def test_the_root_itself_resolves(self):
        self.assertEqual(paths.resolve("/", self.root), Path(os.path.realpath(self.root)))

    def test_a_path_that_does_not_exist_still_resolves(self):
        """Creating a file asks about a path that is not there yet."""
        got = paths.resolve("/sub/new.txt", self.root)
        self.assertEqual(got.name, "new.txt")
        self.assertFalse(got.exists())

    def test_climbing_out_is_refused(self):
        """The textual collapse alone stops this, which is why it happens first."""
        got = paths.resolve("/../outside.txt", self.root)
        self.assertEqual(paths.to_request(got, self.root), "/outside.txt")
        self.assertFalse(got.exists(), "the climb was collapsed, not followed")

    def test_a_symlink_pointing_out_of_the_root_is_refused(self):
        """The case the textual collapse cannot catch: nothing in the request
        looks suspicious, and the escape happens on disk."""
        (self.root / "escape").symlink_to(self.tmp / "outside.txt")
        with self.assertRaises(paths.OutsideRoot):
            paths.resolve("/escape", self.root)

    def test_a_symlink_staying_inside_the_root_is_fine(self):
        (self.root / "inside").symlink_to(self.root / "sub" / "file.txt")
        self.assertEqual(paths.resolve("/inside", self.root).read_text(), "hello\n")

    def test_a_sibling_whose_name_starts_with_the_root_is_outside(self):
        """Containment, not a string prefix. `/x/root` and `/x/rootsecrets`
        share their first characters and share nothing else, and comparing the
        text is the oldest way to get this wrong."""
        sibling = self.tmp / (self.root.name + "secrets")
        sibling.mkdir()
        (sibling / "keys.txt").write_text("no\n")
        (self.root / "link").symlink_to(sibling)
        with self.assertRaises(paths.OutsideRoot):
            paths.resolve("/link/keys.txt", self.root)

    def test_a_directory_symlink_out_of_the_root_is_refused(self):
        (self.tmp / "elsewhere").mkdir()
        (self.tmp / "elsewhere" / "x.txt").write_text("no\n")
        (self.root / "link").symlink_to(self.tmp / "elsewhere")
        with self.assertRaises(paths.OutsideRoot):
            paths.resolve("/link/x.txt", self.root)


class ToRequestTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "a").mkdir()

    def test_the_root_is_a_single_slash(self):
        self.assertEqual(paths.to_request(self.root, self.root), "/")

    def test_a_child_is_named_from_the_root(self):
        """Never from the filesystem root: where the root sits on the machine
        is not the browser's business."""
        self.assertEqual(paths.to_request(self.root / "a", self.root), "/a")

    def test_it_is_the_inverse_of_resolve(self):
        for request in ("/", "/a"):
            self.assertEqual(
                paths.to_request(paths.resolve(request, self.root), self.root), request
            )


class IsDirTest(unittest.TestCase):
    def test_it_answers_rather_than_raising(self):
        class Dead:
            def is_dir(self):
                raise OSError(107, "Transport endpoint is not connected")

        self.assertFalse(paths.is_dir(Dead()))

    def test_it_still_answers_for_real_paths(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertTrue(paths.is_dir(tmp))
        self.assertFalse(paths.is_dir(tmp / "nope"))

    def test_it_takes_a_scandir_entry_without_re_stating(self):
        """The hot caller. A DirEntry already knows, and turning it back into
        a Path to ask would stat every file in the listing a second time."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "d").mkdir()
        (tmp / "f").write_text("x")
        got = {e.name: paths.is_dir(e) for e in os.scandir(tmp)}
        self.assertEqual(got, {"d": True, "f": False})


if __name__ == "__main__":
    unittest.main()
