"""Boundaries and latency guarantees for interactive filesystem navigation."""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import files, paths, search


class NavigationFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def file(self, name, content="hello"):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target


class BoundedScanTest(NavigationFixture):
    def test_directories_and_skipped_entries_count_toward_the_budget(self):
        for number in range(8):
            (self.root / f".hidden{number}").mkdir()
        entries, complete, scanned = search.scan(self.root, self.root, limit=3)
        self.assertEqual(entries, ())
        self.assertFalse(complete)
        self.assertEqual(scanned, 3)

    def test_links_are_listed_but_loops_and_outside_targets_are_not_followed(self):
        self.file("folder/readme.md")
        (self.root / "folder/loop").symlink_to(self.root)
        (self.root / "outside").symlink_to(self.root.parent)
        entries, complete, _ = search.scan(self.root, self.root)
        self.assertIn(("/folder", True), entries)
        self.assertIn(("/folder/loop", True), entries)
        self.assertIn(("/folder/readme.md", False), entries)
        self.assertNotIn("/outside", [name for name, _ in entries])
        self.assertFalse(any("/loop/" in name for name, _ in entries))
        self.assertTrue(complete)

    def test_virtual_roots_are_excluded_without_hiding_project_names(self):
        self.file("kernel/live")
        self.file("project/proc/source.py")
        with patch.object(search, "VIRTUAL_ROOTS", {str(self.root / "kernel")}):
            entries, _, _ = search.scan(self.root, self.root)
        names = [name for name, _ in entries]
        self.assertNotIn("/kernel/live", names)
        self.assertIn("/project/proc/source.py", names)
        self.assertTrue(search.excluded_root("/proc/self"))
        self.assertFalse(search.excluded_root("/projects/proc"))

    def test_zero_time_or_cancelled_scan_reports_partial(self):
        self.file("a")
        cancelled = threading.Event()
        cancelled.set()
        for options in ({"seconds": 0}, {"cancel": cancelled}):
            entries, complete, scanned = search.scan(self.root, self.root, **options)
            self.assertEqual((entries, complete, scanned), ((), False, 0))

    def test_scope_cannot_escape_served_root(self):
        with self.assertRaises(paths.OutsideRoot):
            search.find(self.root.parent, "name", root=self.root)


class BackgroundIndexTest(NavigationFixture):
    def finish(self, manager, release):
        release.set()
        deadline = time.monotonic() + 2
        while manager.running and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(manager.running, 0, "index workers did not finish")

    def test_reads_do_not_wait_for_the_cold_worker_and_partial_results_are_visible(self):
        started, release, published = threading.Event(), threading.Event(), threading.Event()

        def scanner(scope, root, hidden, **options):
            started.set()
            options["publish"]((("/first", False),), 1)
            published.set()
            release.wait(2)
            return (("/first", False), ("/later", True)), True, 2

        manager = search.BackgroundIndex(scanner)
        self.addCleanup(self.finish, manager, release)
        before = time.monotonic()
        initial = manager.get(self.root, self.root)
        self.assertLess(time.monotonic() - before, 0.2)
        self.assertTrue(initial["indexing"])
        self.assertTrue(started.wait(1))
        self.assertTrue(published.wait(1))
        partial = manager.get(self.root, self.root)
        self.assertEqual(partial["entries"], (("/first", False),))
        self.assertTrue(partial["indexing"])
        self.finish(manager, release)
        ready = manager.get(self.root, self.root)
        self.assertTrue(ready["complete"])
        self.assertFalse(ready["indexing"])
        self.assertEqual(len(ready["entries"]), 2)

    def test_scopes_share_a_bounded_worker_pool_and_cache(self):
        release = threading.Event()
        active, peak = 0, 0
        lock = threading.Lock()

        def scanner(scope, root, hidden, **options):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
            release.wait(2)
            with lock:
                active -= 1
            return (), True, 0

        manager = search.BackgroundIndex(scanner)
        self.addCleanup(self.finish, manager, release)
        with patch.object(search, "MAX_BUILDERS", 2), patch.object(search, "MAX_INDEXES", 3):
            for number in range(8):
                manager.get(self.root / str(number), self.root)
            self.assertLessEqual(manager.running, 2)
            self.assertEqual(len(manager.cache), 3)
            self.finish(manager, release)
        self.assertLessEqual(peak, 2)

    def test_refresh_serves_the_previous_snapshot(self):
        release, refreshing = threading.Event(), threading.Event()
        calls = []

        def scanner(scope, root, hidden, **options):
            calls.append(scope)
            if len(calls) > 1:
                refreshing.set()
                release.wait(2)
                return (("/new", False),), True, 1
            return (("/old", False),), True, 1

        manager = search.BackgroundIndex(scanner)
        self.addCleanup(self.finish, manager, release)
        manager.get(self.root, self.root)
        self.finish(manager, release)
        release.clear()
        with manager.lock:
            next(iter(manager.cache.values())).updated = 0
        pending = manager.get(self.root, self.root)
        self.assertTrue(refreshing.wait(1))
        self.assertEqual(pending["entries"], (("/old", False),))
        self.assertTrue(pending["indexing"])
        self.finish(manager, release)
        self.assertEqual(manager.get(self.root, self.root)["entries"], (("/new", False),))


class PathCompletionTest(NavigationFixture):
    def setUp(self):
        super().setUp()
        self.file("docs/alpha.txt")
        self.file("docs/prefix-alpha.txt")
        self.file("docs/nested/alpha-deep.txt")
        self.file("docs/.hidden")
        self.file("other/alpha.txt")

    def test_typed_paths_widen_scope_and_tilde_uses_the_account_home(self):
        for query in ("/docs/al", str(self.root / "docs/al"), "~/al"):
            directory, fragment, completing = search.query_scope(
                self.root / "other", query, self.root, home=self.root / "docs")
            self.assertEqual((directory, fragment, completing), (self.root / "docs", "al", True))
        directory, fragment, completing = search.query_scope(
            self.root, "~/", self.root, home=self.root / "docs")
        self.assertEqual((directory, fragment, completing), (self.root / "docs", "", True))

    def test_a_trailing_slash_lists_only_immediate_children_without_starting_an_index(self):
        with patch.object(search.INDEXES, "get", side_effect=AssertionError("cold walk")):
            result = search.find(self.root, "/docs/", root=self.root)
        names = [row["path"] for row in result["matches"]]
        self.assertEqual(names[0], "/docs/nested")
        self.assertIn("/docs/alpha.txt", names)
        self.assertNotIn("/docs/nested/alpha-deep.txt", names)
        self.assertFalse(result["indexing"])

    def test_immediate_prefixes_precede_substrings_and_cached_deep_matches(self):
        snapshot = {"entries": (("/docs/nested/alpha-deep.txt", False),),
                    "complete": True, "indexing": False, "scanned": 1}
        with patch.object(search.INDEXES, "get", return_value=snapshot), patch.object(
                search, "scan", side_effect=AssertionError("synchronous deep walk")):
            result = search.find(self.root / "other", "/docs/al", root=self.root)
        self.assertEqual([row["path"] for row in result["matches"]],
                         ["/docs/alpha.txt", "/docs/prefix-alpha.txt", "/docs/nested/alpha-deep.txt"])
        self.assertTrue(result["widened"])

    def test_explicit_dot_prefix_completes_hidden_children(self):
        ordinary, _ = search.complete_children(self.root / "docs", "", self.root)
        hidden, _ = search.complete_children(self.root / "docs", ".h", self.root)
        self.assertNotIn(("/docs/.hidden", False), ordinary)
        self.assertEqual(hidden, [("/docs/.hidden", False)])
        snapshot = {"entries": (), "scanned": 0, "complete": True, "indexing": False}
        with patch.object(search.INDEXES, "get", return_value=snapshot):
            result = search.find(self.root, "/docs/.", root=self.root)
        self.assertEqual([row["path"] for row in result["matches"]], ["/docs/.hidden"])

    def test_recents_are_existing_contained_scoped_paths_and_break_equal_score_ties(self):
        recent = {"/missing": 9, "/docs/.hidden": 8, "/other/alpha.txt": 7, "/docs/alpha.txt": 6}
        result = search.find(self.root / "docs", "", root=self.root, recent=recent)
        self.assertEqual([row["path"] for row in result["matches"]], ["/docs/alpha.txt"])
        self.file("first/name")
        self.file("other/name")
        snapshot = {"entries": (("/first/name", False), ("/other/name", False)),
                    "complete": True, "indexing": False, "scanned": 2}
        with patch.object(search.INDEXES, "get", return_value=snapshot):
            result = search.find(self.root, "name", recent={"/other/name": 1})
        self.assertEqual(result["matches"][0]["path"], "/other/name")


class FileInformationTest(NavigationFixture):
    def test_a_link_outside_the_root_is_one_broken_row(self):
        self.file("normal")
        (self.root / "outside").symlink_to(self.root.parent)
        rows = {row["name"]: row for row in files.listing(self.root, self.root)}
        self.assertNotIn("error", rows["normal"])
        self.assertEqual(rows["outside"]["path"], "/outside")
        self.assertFalse(rows["outside"]["is_dir"])
        self.assertIn("outside the served folder", rows["outside"]["error"])

    def test_links_keep_their_own_name_and_children_keep_the_opened_alias(self):
        self.file("folder/file")
        (self.root / "alias").symlink_to(self.root / "folder")
        rows = {row["name"]: row for row in files.listing(self.root, self.root)}
        self.assertEqual(rows["alias"]["path"], "/alias")
        self.assertTrue(rows["alias"]["is_dir"])
        children = files.listing(self.root / "folder", self.root, request_path="/alias")
        self.assertEqual(children[0]["path"], "/alias/file")

    def test_recursive_totals_do_not_follow_symlink_loops_or_count_link_targets_twice(self):
        self.file("folder/a", "abc")
        self.file("folder/nested/b", "defgh")
        (self.root / "folder/nested/loop").symlink_to(self.root / "folder")
        result = files.information("/folder", self.root)
        self.assertTrue(result["is_dir"])
        self.assertEqual(result["usage"], {
            "bytes": 8, "files": 2, "folders": 1, "links": 1, "scanned": 4, "complete": True})

    def test_count_and_time_limits_explicitly_report_partial_totals(self):
        self.file("a")
        self.file("b")
        for options in ({"max_entries": 1}, {"seconds": 0}):
            result = files.folder_usage(self.root, **options)
            self.assertFalse(result["complete"])
            self.assertLessEqual(result["scanned"], 1)

    def test_stat_many_shares_read_stamps_and_reports_deleted_or_outside_links(self):
        target = self.file("a", "a")
        (self.root / "outside").symlink_to(self.root.parent)
        initial = files.fingerprints(["/a", "/missing", "/outside"], self.root)
        self.assertEqual(initial, {"/a": files.stamp(target), "/missing": None, "/outside": None})
        target.write_text("longer")
        self.assertNotEqual(initial["/a"], files.fingerprints(["/a"], self.root)["/a"])
        target.unlink()
        self.assertIsNone(files.fingerprints(["/a"], self.root)["/a"])

    def test_stat_many_rejects_unbounded_or_malformed_inputs(self):
        for value in ("/a", ["/a"] * 513, [None], ["x" * 4097]):
            with self.assertRaises(ValueError):
                files.fingerprints(value, self.root)
