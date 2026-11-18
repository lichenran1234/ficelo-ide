import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import git  # noqa: E402

HAVE_GIT = shutil.which("git") is not None


@unittest.skipUnless(HAVE_GIT, "git is not installed")
class RepoCase(unittest.TestCase):
    """Built against a real repository. A faked `git` would be testing the
    fake, and the interesting cases here — renames, merge bases, untracked
    files — are exactly where a fake would be wrong."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        # A committer identity is not enough on a machine with commit signing
        # configured globally, which would prompt and hang.
        self.git("config", "commit.gpgsign", "false")
        self.build()

    def git(self, *args):
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(self.repo),
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def build(self):
        pass


class DiscoveryTest(RepoCase):
    def build(self):
        self.write("a.txt", "one\n")
        self.commit("first")

    def test_it_finds_the_repository_from_inside(self):
        (self.repo / "deep" / "deeper").mkdir(parents=True)
        found = git.top_level(self.repo / "deep" / "deeper")
        self.assertEqual(Path(found).resolve(), self.repo.resolve())

    def test_a_plain_folder_is_not_a_repository(self):
        self.assertIsNone(git.top_level(Path(tempfile.mkdtemp())))

    def test_it_describes_the_branch_and_head(self):
        described = git.describe(self.repo)
        self.assertEqual(described["branch"], "main")
        self.assertEqual(len(described["head"]), 7)


class BaseTest(RepoCase):
    def build(self):
        self.write("a.txt", "one\n")
        self.first = self.commit("first")

    def test_it_falls_back_to_the_first_commit_without_a_remote(self):
        """A scratch repository with one oddly-named branch is normal, and
        showing its whole history beats refusing to show anything."""
        self.git("branch", "-m", "scratch")
        name, sha = git.resolve_base(self.repo)
        self.assertEqual(sha, self.first)

    def test_main_is_preferred_when_it_exists(self):
        name, _ = git.resolve_base(self.repo)
        self.assertEqual(name, "main")

    def test_an_explicit_base_is_honoured(self):
        name, sha = git.resolve_base(self.repo, "HEAD")
        self.assertEqual(sha, self.first)

    def test_a_nonexistent_base_is_an_error_not_a_silent_fallback(self):
        """Falling back would compare against something the caller did not
        ask for and report the result as though they had."""
        with self.assertRaises(git.GitFailed):
            git.resolve_base(self.repo, "no-such-branch")


class ChangedFilesTest(RepoCase):
    def build(self):
        self.write("kept.txt", "unchanged\n")
        self.write("edited.txt", "before\n")
        self.write("deleted.txt", "goes away\n")
        self.write("renamed.txt", "a" * 200 + "\n")
        self.commit("base")
        self.git("checkout", "-b", "work")

    def changed(self):
        return {e["path"]: e for e in git.changed_files(self.repo)["files"]}

    def test_an_edit_is_reported(self):
        self.write("edited.txt", "after\n")
        self.assertEqual(self.changed()["edited.txt"]["status"], "M")

    def test_a_deletion_is_reported(self):
        (self.repo / "deleted.txt").unlink()
        self.assertEqual(self.changed()["deleted.txt"]["status"], "D")

    def test_an_untracked_file_is_reported(self):
        """git diff never mentions these, and in a review they are usually
        the most interesting thing present."""
        self.write("brand_new.txt", "hello\n")
        self.assertEqual(self.changed()["brand_new.txt"]["status"], "?")

    def test_an_ignored_file_is_not_reported(self):
        self.write(".gitignore", "junk.log\n")
        self.write("junk.log", "noise\n")
        self.assertNotIn("junk.log", self.changed())

    def test_an_unchanged_file_is_not_reported(self):
        self.write("edited.txt", "after\n")
        self.assertNotIn("kept.txt", self.changed())

    def test_a_rename_carries_its_old_name(self):
        self.git("mv", "renamed.txt", "moved.txt")
        entry = self.changed().get("moved.txt")
        self.assertIsNotNone(entry, "the rename was not reported")
        self.assertEqual(entry["status"], "R")
        self.assertEqual(entry["from"], "renamed.txt")

    def test_renames_are_detected_even_when_the_user_disabled_them(self):
        """Rename detection is on by default, so the flag looks redundant.
        It is not: `diff.renames = false` in someone's config turns it off,
        and the rename then appears as an unrelated delete and add."""
        self.git("config", "diff.renames", "false")
        self.git("mv", "renamed.txt", "moved.txt")
        entry = self.changed().get("moved.txt")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "R")

    def test_the_merge_base_is_used_not_the_branch_tip(self):
        """The point of merge-base. After the base branch moves on, its new
        commits are not this branch's changes, and diffing against the tip
        would show them as though they had been reverted here."""
        self.write("mine.txt", "my work\n")
        self.commit("my change")
        self.git("checkout", "main")
        self.write("theirs.txt", "their work\n")
        self.commit("their change")
        self.git("checkout", "work")
        self.assertNotIn("theirs.txt", self.changed())
        self.assertIn("mine.txt", self.changed())

    def test_a_filename_with_a_newline_survives(self):
        """-z exists for this. Line-splitting the output corrupts the path
        and the file becomes unopenable."""
        self.write("odd\nname.txt", "x\n")
        self.assertIn("odd\nname.txt", self.changed())


class BlobTest(RepoCase):
    def build(self):
        self.write("a.txt", "original\n")
        self.first = self.commit("first")
        self.write("a.txt", "changed\n")
        self.second = self.commit("second")

    def test_it_reads_a_file_at_a_revision(self):
        self.assertEqual(git.blob(self.repo, self.first, "a.txt"), "original\n")
        self.assertEqual(git.blob(self.repo, self.second, "a.txt"), "changed\n")

    def test_a_file_absent_at_that_revision_is_none(self):
        """Which is how a newly added file is told apart from an empty one."""
        self.assertIsNone(git.blob(self.repo, self.first, "never_existed.txt"))

    def test_an_empty_file_is_not_none(self):
        self.write("empty.txt", "")
        sha = self.commit("empty")
        self.assertEqual(git.blob(self.repo, sha, "empty.txt"), "")


class CommitsTest(RepoCase):
    def build(self):
        self.write("a.txt", "1\n")
        self.first = self.commit("first subject")
        self.write("b.txt", "2\n")
        self.second = self.commit("second subject")

    def test_newest_first(self):
        found = git.commits(self.repo)
        self.assertEqual(found[0]["subject"], "second subject")
        self.assertEqual(found[1]["subject"], "first subject")

    def test_the_limit_is_respected(self):
        self.assertEqual(len(git.commits(self.repo, limit=1)), 1)

    def test_a_multiline_body_does_not_split_a_record(self):
        """Records are separated by NULs, not newlines, because every commit
        body may contain newlines and most do."""
        self.write("c.txt", "3\n")
        self.git("add", "-A")
        self.git("commit", "-m", "title line\n\nbody line one\nbody line two")
        found = git.commits(self.repo)
        self.assertEqual(found[0]["subject"], "title line")
        self.assertEqual(len(found), 3)

    def test_rev_anchors_the_walk(self):
        """Without it a request for one commit's details reports whatever is
        newest, which is right only by coincidence."""
        found = git.commits(self.repo, limit=1, rev=self.first)
        self.assertEqual(found[0]["subject"], "first subject")

    def test_commit_files_lists_what_changed(self):
        entries = git.commit_files(self.repo, self.second)
        self.assertEqual([e["path"] for e in entries], ["b.txt"])
        self.assertEqual(entries[0]["status"], "A")

    def test_the_first_commit_reports_its_files(self):
        """It has no parent to diff against, so without --root it reports
        nothing at all."""
        entries = git.commit_files(self.repo, self.first)
        self.assertEqual([e["path"] for e in entries], ["a.txt"])


if __name__ == "__main__":
    unittest.main()
