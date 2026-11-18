"""Public CLI output parsing; the optional client is not a test dependency."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import git, sapling


class SaplingOutputTest(unittest.TestCase):
    def test_copies_and_renames_keep_paths_with_newlines(self):
        output = "A new\nname\0  old name\0R old name\0A copy\0  kept\0M kept\0? untracked\0! missing\0"
        with patch.object(sapling, "run", return_value=output):
            result = sapling.changed(Path("."), sapling.NULL)
        by_path = {entry["path"]: entry for entry in result}
        self.assertEqual(by_path["new\nname"], {"status": "R", "path": "new\nname", "from": "old name"})
        self.assertEqual(by_path["copy"]["status"], "C")
        self.assertEqual(by_path["missing"]["status"], "D")
        self.assertNotIn("old name", by_path)

    def test_history_metadata_handles_multiline_subjects_and_merge_parents(self):
        entry = {"node": "a" * 40, "p1node": "b" * 40, "p2node": sapling.NULL,
                 "author": 'A "quoted" author', "date": [0, 0], "desc": "subject\nbody",
                 "file_adds": ["new"], "file_dels": ["old"], "file_mods": [], "file_copies": {"new": "old"}}
        with patch.object(sapling, "run", return_value=json.dumps(entry) + "\n"):
            result = sapling.metadata(Path("."), "reverse(::.)")
        self.assertEqual(result[0]["subject"], "subject")
        self.assertEqual(result[0]["files"], [{"path": "new", "from": "old", "status": "R"}])
        self.assertFalse(result[0]["merge"])
        entry["p2node"] = "c" * 40
        with patch.object(sapling, "run", return_value=json.dumps(entry) + "\n"):
            result = sapling.metadata(Path("."), ".")
        self.assertTrue(result[0]["merge"])
        self.assertEqual(result[0]["files"], [])

    def test_content_that_looks_like_patch_headers_is_counted_as_content(self):
        output = 'diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1,2 @@\n-old\n+++ literal\n+new\n'
        with patch.object(sapling, "run", return_value=output):
            self.assertEqual(sapling.counts(Path("."), sapling.NULL), {"a": {"added": 2, "removed": 1}})

    def test_quoted_utf8_paths_in_patch_headers_are_decoded(self):
        self.assertEqual(sapling._patch_path('"b/\\303\\251.txt"'), "é.txt")
        self.assertEqual(sapling._patch_path('"b/tab\\tname.txt"'), "tab\tname.txt")

    def test_absent_optional_client_has_an_actionable_error(self):
        with patch.object(sapling.subprocess, "run", side_effect=FileNotFoundError), self.assertRaisesRegex(git.GitFailed, "public Sapling client"):
            sapling.run(Path("."), "log")
