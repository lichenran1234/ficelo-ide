import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import search  # noqa: E402


class FuzzyTest(unittest.TestCase):
    def test_letters_must_all_be_present_in_order(self):
        self.assertIsNotNone(search.fuzzy("main.py", "mpy"))
        self.assertIsNone(search.fuzzy("main.py", "pym"), "order matters")
        self.assertIsNone(search.fuzzy("main.py", "mainz"))

    def test_it_is_case_insensitive(self):
        self.assertIsNotNone(search.fuzzy("README.md", "readme"))
        self.assertIsNotNone(search.fuzzy("readme.md", "README"))

    def test_an_empty_query_matches_everything_at_zero(self):
        self.assertEqual(search.fuzzy("anything", ""), 0)

    def test_adjacent_letters_beat_scattered_ones(self):
        """The whole point of the scoring: `main` in `main.py` is the word
        being typed; the same four letters strewn through a long path are a
        coincidence."""
        together = search.fuzzy("main.py", "main")
        apart = search.fuzzy("m-a-i-n.py", "main")
        self.assertGreater(together, apart)

    def test_a_word_boundary_beats_the_middle_of_a_word(self):
        self.assertGreater(search.fuzzy("my_parser.py", "p"), search.fuzzy("component.py", "p"))


class RankTest(unittest.TestCase):
    def test_a_filename_match_beats_a_directory_match(self):
        """Typing `server` means the file called server, not every file in a
        folder called server."""
        self.assertGreater(
            search.rank("/src/server.py", "server"),
            search.rank("/server/utils.py", "server"),
        )

    def test_a_run_of_the_typed_letters_beats_them_scattered(self):
        """The rule that matters most, learned from a real search: the
        product's own name answered with a file where its letters merely
        appear in order. In `file_icon_selector_old.py` f-i-c-e-l-o are all
        present, all in order, and entirely coincidental. A contiguous run
        is different evidence, even when it is in a folder name rather than
        the filename."""
        decoy = search.rank("/x/file_icon_selector_old.py", "ficelo")
        self.assertIsNotNone(decoy, "the decoy must match, or this compares against nothing")
        self.assertGreater(search.rank("/gitrepos/ficelo/README.md", "ficelo"), decoy)

    def test_a_run_in_the_filename_beats_one_in_the_path(self):
        self.assertGreater(
            search.rank("/other/server.py", "server"),
            search.rank("/server/utils.py", "server"),
        )

    def test_earlier_in_the_name_beats_later(self):
        """Names of equal length, so the shorter-name tiebreak cannot be
        what decides it — which is how this first passed with the position
        term deleted."""
        self.assertEqual(len("ficeloAB.py"), len("ABficelo.py"))
        self.assertGreater(
            search.rank("/a/ficeloAB.py", "ficelo"), search.rank("/a/ABficelo.py", "ficelo")
        )

    def test_a_scattered_match_still_counts_when_nothing_better_exists(self):
        """Fuzzy matching is the point of the box; it is only outranked, not
        discarded."""
        self.assertIsNotNone(search.rank("/x/main_python_yak.txt", "mpy"))

    def test_a_scattered_name_match_beats_a_scattered_path_match(self):
        """Deleting the name tier leaves both matching on the path, so the
        pair has to be one where the path-only candidate would otherwise
        score higher: its letters all sit at folder boundaries, which is
        worth more than the same letters mid-word in a filename."""
        name_match = search.rank("/x/m1p2y3.txt", "mpy")
        path_match = search.rank("/m1/p2/y3.txt", "mpy")
        self.assertIsNotNone(name_match)
        self.assertIsNotNone(path_match)
        self.assertGreater(name_match, path_match)

    def test_a_shorter_name_wins_a_tie(self):
        self.assertGreater(
            search.rank("/test.py", "test"),
            search.rank("/test_of_everything_at_once.py", "test"),
        )

    def test_a_shallower_path_wins_a_tie(self):
        self.assertGreater(
            search.rank("/main.py", "main"), search.rank("/a/b/c/d/main.py", "main")
        )

    def test_no_match_is_none_not_zero(self):
        """Zero is a real score for an empty query, so a miss cannot use it."""
        self.assertIsNone(search.rank("/src/server.py", "zzz"))


class WalkTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("x")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("x")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "left-pad.js").write_text("x")
        (self.root / ".env").write_text("x")
        (self.root / "readme.md").write_text("x")

    def names(self, **kw):
        found, _ = search.walk(self.root, **kw)
        return sorted(Path(p).name for p in found)

    def test_it_finds_files_at_any_depth(self):
        self.assertIn("main.py", self.names())

    def test_machinery_is_skipped(self):
        """Descending into .git costs more than the whole repository and never
        returns anything anyone was looking for."""
        self.assertNotIn("config", self.names())
        self.assertNotIn("left-pad.js", self.names())

    def test_dotfiles_are_skipped_unless_asked_for(self):
        self.assertNotIn(".env", self.names())
        self.assertIn(".env", self.names(show_hidden=True))

    def test_it_goes_wide_before_it_goes_deep(self):
        """Only matters because the walk is capped: depth-first spends the
        whole budget inside the first subtree it enters, and searching a
        machine's root reached /var and stopped.

        The invariant is that depth never decreases as the list goes on.
        Weaker phrasings do not work: depth-first also puts *a* shallow file
        first, and also reaches every folder when nothing truncates it."""
        for top in ("aaa", "bbb", "ccc"):
            deep = self.root / top / "one" / "two"
            deep.mkdir(parents=True)
            (deep / "deep.txt").write_text("x")
            (self.root / top / "shallow.txt").write_text("x")

        found, _ = search.walk(self.root)
        depths = [p[len(str(self.root)):].count("/") for p in found]
        self.assertEqual(
            depths, sorted(depths), "files came back deepest-first somewhere: %s" % depths
        )

    def test_it_reports_when_it_stopped_early(self):
        """A truncated walk that claims to be complete is a search that
        silently lies about having looked."""
        _, complete = search.walk(self.root, limit=1)
        self.assertFalse(complete)
        _, complete = search.walk(self.root)
        self.assertTrue(complete)

    def test_a_symlink_loop_does_not_hang_it(self):
        """Following it would recurse until the cap, so the walk would
        finish either way — but it would finish *truncated*, having spent
        the entire budget going round in circles. Completeness is the thing
        to assert."""
        (self.root / "src" / "loop").symlink_to(self.root)
        found, complete = search.walk(self.root)
        self.assertIn("main.py", [Path(p).name for p in found])
        self.assertTrue(complete, "the loop consumed the walk's budget")
        # Following it terminates anyway, once the repeated path exceeds
        # PATH_MAX and scandir starts failing — so completeness alone proves
        # nothing. What must not happen is descending through the link.
        through = [p for p in found if "/loop/" in p]
        self.assertEqual(through, [], "the walk went through the symlink")


class AsRequestTest(unittest.TestCase):
    """The shortcut that made search usable. It must agree with the real
    conversion, or search results point at paths nothing can open."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_it_matches_the_real_conversion(self):
        from ficelo import paths as real_paths

        (self.root / "sub").mkdir()
        cases = [self.root, self.root / "a.txt", self.root / "sub" / "b.txt"]
        for case in cases:
            self.assertEqual(
                search.as_request(str(case), str(self.root).rstrip("/")),
                real_paths.to_request(case, self.root),
                str(case),
            )

    def test_the_root_itself_is_a_single_slash(self):
        self.assertEqual(search.as_request(str(self.root), str(self.root).rstrip("/")), "/")

    def test_a_path_outside_the_root_is_refused(self):
        """It cannot arise from the walk, but returning a path the rest of
        the app would reject is worse than returning nothing."""
        self.assertIsNone(search.as_request("/somewhere/else.txt", str(self.root)))

    def test_a_sibling_sharing_a_prefix_is_refused(self):
        """`/x/root` and `/x/rootsecrets` share their first characters and
        nothing else — the same trap as the containment check in paths."""
        sibling = str(self.root) + "secrets"
        self.assertIsNone(search.as_request(sibling + "/keys.txt", str(self.root)))


class CacheTest(unittest.TestCase):
    """Search runs on every keystroke and a large checkout takes seconds to
    walk, so the walk is reused briefly. Measured at 4.4s on a real tree,
    which is why this exists at all."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "one.txt").write_text("x")
        search.clear_cache()
        self.addCleanup(search.clear_cache)
        self.walks = 0
        self.real = search.walk

        def counted(*args, **kwargs):
            self.walks += 1
            return self.real(*args, **kwargs)

        search.walk = counted
        self.addCleanup(setattr, search, "walk", self.real)

    def test_a_second_query_reuses_the_walk(self):
        search.search(self.root, "one", now=100.0)
        search.search(self.root, "on", now=100.5)
        search.search(self.root, "o", now=101.0)
        self.assertEqual(self.walks, 1, "the tree was walked once per keystroke")

    def test_the_cache_expires(self):
        search.search(self.root, "one", now=100.0)
        search.search(self.root, "one", now=100.0 + search.CACHE_SECONDS + 1)
        self.assertEqual(self.walks, 2)

    def test_a_new_file_is_findable_once_the_cache_expires(self):
        search.search(self.root, "two", now=100.0)
        (self.root / "two.txt").write_text("x")
        later = search.search(self.root, "two", now=100.0 + search.CACHE_SECONDS + 1)
        self.assertEqual([m["path"] for m in later["matches"]], ["/two.txt"])

    def test_two_scopes_do_not_share_a_cached_answer(self):
        """Keyed without the scope, the second search of a session answers
        with the first one's tree. Separate server tests cannot catch it —
        each gets a fresh root, so the keys differ anyway."""
        (self.root / "mine").mkdir()
        (self.root / "mine" / "target.txt").write_text("x")
        (self.root / "other").mkdir()
        (self.root / "other" / "target.txt").write_text("x")
        mine = search.search(self.root / "mine", "target", now=100.0, root=self.root)
        other = search.search(self.root / "other", "target", now=100.1, root=self.root)
        self.assertEqual([m["path"] for m in mine["matches"]], ["/mine/target.txt"])
        self.assertEqual([m["path"] for m in other["matches"]], ["/other/target.txt"])

    def test_hidden_and_visible_searches_do_not_share_a_cache(self):
        """Otherwise asking for hidden files returns the previous answer,
        or the toggle appears not to work until the cache expires."""
        (self.root / ".secret").write_text("x")
        search.search(self.root, "secret", now=100.0, show_hidden=False)
        found = search.search(self.root, "secret", now=100.1, show_hidden=True)
        self.assertEqual([m["path"] for m in found["matches"]], ["/.secret"])


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir()
        (self.root / "src" / "server.py").write_text("x")
        (self.root / "src" / "search.py").write_text("x")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_server.py").write_text("x")
        (self.root / "README.md").write_text("x")

    def setUpCache(self):
        search.clear_cache()

    def paths(self, query, **kw):
        search.clear_cache()
        return [m["path"] for m in search.search(self.root, query, **kw)["matches"]]

    def test_it_finds_by_part_of_a_name(self):
        self.assertIn("/src/server.py", self.paths("server"))

    def test_results_are_request_paths_not_disk_paths(self):
        """Where the root sits on the machine is not the browser's business."""
        for path in self.paths("py"):
            self.assertTrue(path.startswith("/"))
            self.assertNotIn(str(self.root), path)

    def test_the_best_match_comes_first(self):
        self.assertEqual(self.paths("server")[0], "/src/server.py")

    def test_an_empty_query_returns_nothing_rather_than_everything(self):
        self.assertEqual(self.paths(""), [])
        self.assertEqual(self.paths("   "), [])

    def test_the_limit_is_respected(self):
        self.assertEqual(len(self.paths("py", limit=2)), 2)

    def test_ordering_is_stable_between_runs(self):
        """Filesystem order is arbitrary; a result list that reshuffles under
        an unchanged query makes the second keystroke unusable."""
        self.assertEqual(self.paths("s"), self.paths("s"))

    def test_results_come_back_best_first(self):
        """Stability alone is satisfied by no sorting at all, since the walk
        is cached and hands back the same arbitrary order twice.

        Enough candidates that filesystem order coming out sorted by luck is
        not a plausible explanation."""
        for name in ("sort.py", "assert.py", "s.py", "session.md", "pass.txt",
                     "class_s.py", "streams.py", "misc.py", "sub/sample.py",
                     "sub/scan.py", "sub/deep/spider.py", "sub/deep/s_notes.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        search.clear_cache()
        found = search.search(self.root, "s", limit=40)["matches"]
        scores = [m["score"] for m in found]
        self.assertGreater(len(scores), 8, "too few matches to be meaningful")
        self.assertEqual(scores, sorted(scores, reverse=True), scores)


if __name__ == "__main__":
    unittest.main()
