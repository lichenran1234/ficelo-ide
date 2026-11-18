"""Portable repository review behavior exercised against real local checkouts."""

import errno
import os
from pathlib import Path
from unittest.mock import patch

from test_git import RepoCase

from ficelo import git, paths


class ReviewRepositoryTest(RepoCase):
    def build(self):
        self.write("nested/a.txt", "before\n")
        self.write("z.txt", "same\n")
        self.first = self.commit("base")
        self.git("checkout", "-b", "work")

    def test_filesystem_root_can_review_a_nested_checkout(self):
        self.write("nested/a.txt", "after\n")
        result = git.review_status(Path("/"), str(self.repo / "nested"))
        self.assertEqual(result["root"], str(self.repo))
        self.assertEqual(result["files"][0]["request"], str(self.repo / "nested/a.txt"))
        self.assertEqual(result["base"], "main")

    def test_discovery_is_bounded_and_includes_an_enclosing_checkout(self):
        result = git.discover(self.repo, scopes=["/nested"], max_dirs=1)
        self.assertEqual([entry["path"] for entry in result["repositories"]], ["/"])
        self.assertLessEqual(result["examined"], 1)
        self.assertEqual(git.discover(self.repo, query="absent")["repositories"], [])

    def test_discovery_skips_inaccessible_scopes_and_nearby_mounts(self):
        scan = self.repo / "scan"
        disconnected = scan / "disconnected"
        disconnected.mkdir(parents=True)
        stat = Path.stat
        for failing_path in (scan, scan / ".git", disconnected / ".git"):
            with self.subTest(path=failing_path.relative_to(self.repo)):
                failures = []

                def unavailable(path, *args, unavailable_path=failing_path, failures_seen=failures, **kwargs):
                    if path == unavailable_path:
                        failures_seen.append(path)
                        raise OSError(errno.ENOTCONN, "filesystem unavailable")
                    return stat(path, *args, **kwargs)

                with patch.object(Path, "stat", unavailable):
                    result = git.discover(self.repo, scopes=["/scan"], query="/", seconds=1)
                self.assertTrue(failures)
                self.assertEqual([entry["path"] for entry in result["repositories"]], ["/"])

    def test_worktree_pointer_reports_checkout_and_branch(self):
        checkout = self.repo / "other-checkout"
        self.git("worktree", "add", "-b", "other", str(checkout), "main")
        result = git.worktrees(self.repo, self.repo)
        self.assertEqual({entry["path"] for entry in result}, {"/", "/other-checkout"})
        other = next(entry for entry in result if entry["path"] != "/")
        self.assertEqual(other["branch"], "other")
        self.assertEqual(git.repository(self.repo, "/other-checkout/nested"), checkout)

    def test_history_contains_only_branch_commits_and_all_file_lists(self):
        self.write("nested/a.txt", "after\n")
        changed = self.commit("edit")
        self.git("mv", "z.txt", "renamed\nfile.txt")
        renamed = self.commit("rename")
        result = git.review_commits(self.repo)
        self.assertEqual([entry["sha"] for entry in result["commits"]], [renamed, changed])
        entry = result["commits"][0]["files"][0]
        self.assertEqual((entry["status"], entry["from"], entry["path"]), ("R", "z.txt", "renamed\nfile.txt"))
        self.assertEqual(result["commits"][1]["files"][0]["added"], 1)
        self.assertEqual(git.review_commits(self.repo, base="HEAD")["commits"], [])

    def test_merge_is_labeled_and_does_not_claim_a_single_parent_diff(self):
        self.write("work.txt", "work\n")
        self.commit("work")
        self.git("checkout", "-b", "side", "main")
        self.write("side.txt", "side\n")
        side = self.commit("side")
        self.git("checkout", "work")
        self.git("merge", "--no-ff", "side", "-m", "merge side")
        result = git.review_commits(self.repo)["commits"]
        self.assertTrue(result[0]["merge"])
        self.assertEqual(result[0]["files"], [])
        self.assertIn(side, [entry["sha"] for entry in result])
        detail = git.review_commit(self.repo, "/", result[0]["sha"], full=True)
        self.assertTrue(detail["merge"])
        self.assertEqual(detail["files"], [])

    def test_whole_commit_includes_rename_and_addition_contents(self):
        self.git("mv", "z.txt", "new-name.txt")
        self.write("new.txt", "new\n")
        sha = self.commit("two files")
        detail = git.review_commit(self.repo, "/", sha, full=True)
        entries = {entry["path"]: entry for entry in detail["files"]}
        self.assertEqual(entries["new-name.txt"]["old"], "same\n")
        self.assertEqual(entries["new.txt"]["old"], None)
        self.assertEqual(entries["new.txt"]["new"], "new\n")
        self.assertTrue(detail["wholeCommit"])
        self.assertFalse(entries["new.txt"]["editable"])

    def test_working_diff_is_editable_and_refreshes_stamp_and_text(self):
        target = self.write("nested/a.txt", "after\n")
        before = git.review_diff(self.repo, "/", "/nested/a.txt")
        self.assertTrue(before["editable"])
        self.assertEqual((before["old"], before["new"]), ("before\n", "after\n"))
        status = git.review_status(self.repo)
        target.write_text("new content\n")
        after = git.review_diff(self.repo, "/", "/nested/a.txt")
        self.assertNotEqual(before["stamp"], after["stamp"])
        self.assertEqual(after["new"], "new content\n")
        self.assertNotEqual(status["version"], git.review_status(self.repo)["version"])

    def test_binary_and_truncated_diff_are_not_editable(self):
        (self.repo / "nested/a.txt").write_bytes(b"binary\0data")
        result = git.review_diff(self.repo, "/", "/nested/a.txt")
        self.assertTrue(result["binary"])
        self.assertFalse(result["editable"])
        self.write("nested/a.txt", "x" * 32)
        with patch.object(git.files, "MAX_TEXT_BYTES", 16):
            result = git.review_diff(self.repo, "/", "/nested/a.txt")
        self.assertTrue(result["truncated"])
        self.assertFalse(result["editable"])
        with patch.object(git, "MAX_BLOB_BYTES", 2):
            result = git.review_commit(self.repo, "/", self.first, full=True)
        self.assertTrue(all(entry["truncated"] for entry in result["files"]))

    def test_narrow_root_filters_commit_files_and_rename_sources(self):
        self.git("mv", "z.txt", "nested/moved.txt")
        self.write("outside.txt", "outside\n")
        sha = self.commit("move inside")
        detail = git.review_commit(self.repo / "nested", "/", sha, full=True)
        self.assertEqual([entry["request"] for entry in detail["files"]], ["/moved.txt"])
        self.assertIsNone(detail["files"][0]["old"])
        (self.repo / "nested/escape").symlink_to(self.repo / "outside.txt")
        with self.assertRaises(paths.OutsideRoot):
            git.review_diff(self.repo / "nested", "/", "/escape")

    def test_submodules_remain_noneditable_without_breaking_the_document(self):
        (self.repo / "submodule").mkdir()
        self.git("update-index", "--add", "--cacheinfo", "160000," + self.first + ",submodule")
        self.git("commit", "-m", "add submodule pointer")
        sha = self.git("rev-parse", "HEAD").strip()
        working = git.review_diff(self.repo, "/", "/submodule")
        self.assertFalse(working["editable"])
        self.assertIn("submodule", working["notice"])
        document = git.review_commit(self.repo, "/", sha, full=True)
        self.assertEqual(len(document["files"]), 1)
        self.assertIn("Submodule", document["files"][0]["notice"])

    def test_working_line_counts_preserve_tabs_in_filenames(self):
        self.write("tab\tname.txt", "before\n")
        self.commit("tab name")
        self.git("branch", "counts-base")
        self.write("tab\tname.txt", "after\nmore\n")
        result = git.review_status(self.repo, base="counts-base")
        self.assertEqual(result["files"][0]["path"], "tab\tname.txt")
        self.assertEqual((result["files"][0]["added"], result["files"][0]["removed"]), (2, 1))

    def test_environment_cannot_redirect_git_to_a_different_checkout(self):
        with patch.dict(os.environ, {"GIT_DIR": "/does-not-exist", "GIT_WORK_TREE": "/does-not-exist"}):
            self.assertEqual(git.top_level(self.repo), self.repo)
        with self.assertRaises(git.GitFailed):
            git.review_commit(self.repo, "/", "--all", full=True)
