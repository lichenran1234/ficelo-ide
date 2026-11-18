"""Navigation contracts exercised through the real HTTP server."""

from unittest.mock import patch

from test_server import ServerCase

from ficelo import files


class NavigationHTTPFixture(ServerCase):
    def prepare(self):
        (self.root / "folder").mkdir()
        (self.root / "folder/note.md").write_text("# Before\n")
        (self.root / "alias").symlink_to(self.root / "folder")


class NavigationEndpointsTest(NavigationHTTPFixture):
    def test_alias_listings_and_reads_keep_the_requested_path(self):
        _, tree = self.get_json("/api/tree?path=/alias")
        _, text = self.get_json("/api/file?path=/alias/note.md")
        self.assertEqual(tree["path"], "/alias")
        self.assertEqual(tree["entries"][0]["path"], "/alias/note.md")
        self.assertEqual(text["path"], "/alias/note.md")

    def test_listing_returns_the_stamp_from_before_scanning(self):
        original = files.listing

        def changing(*args, **kwargs):
            rows = original(*args, **kwargs)
            (self.root / "folder/arrived.txt").write_text("new")
            return rows

        with patch.object(files, "listing", side_effect=changing):
            _, tree = self.get_json("/api/tree?path=/folder")
        self.assertNotEqual(tree["stamp"], files.stamp(self.root / "folder"))
        self.assertEqual([row["name"] for row in tree["entries"]], ["note.md"])

    def test_read_returns_the_stamp_for_the_contents_it_started_reading(self):
        original = files.read_text

        def changing(path):
            body = original(path)
            path.write_text("# Arrived during read\n")
            return body

        with patch.object(files, "read_text", side_effect=changing):
            _, body = self.get_json("/api/file?path=/folder/note.md")
        self.assertEqual(body["text"], "# Before\n")
        self.assertNotEqual(body["stamp"], files.stamp(self.root / "folder/note.md"))

    def test_info_and_watch_are_available_on_read_only_servers(self):
        _, info = self.get_json("/api/info?path=/folder")
        _, stamps = self.post_json("/api/watch", {"paths": ["/folder", "/folder/note.md", "/missing"]})
        self.assertEqual(info["usage"]["bytes"], len("# Before\n"))
        self.assertTrue(info["usage"]["complete"])
        self.assertEqual(stamps["/folder/note.md"], files.stamp(self.root / "folder/note.md"))
        self.assertIsNone(stamps["/missing"])
        status, _ = self.expect_failure(lambda: self.post_json("/api/watch", {"paths": ["/"] * 513}))
        self.assertEqual(status, 400)

    def test_search_offers_directories_and_scoped_recent_files(self):
        _, completion = self.get_json("/api/search?q=/fol")
        self.assertEqual(completion["matches"][0]["path"], "/folder")
        self.assertTrue(completion["matches"][0]["is_dir"])
        self.server.settings.put("ficelo.recent", {"/folder/note.md": 1, "/missing": 2})
        _, recent = self.get_json("/api/search?scope=/folder")
        self.assertEqual([row["path"] for row in recent["matches"]], ["/folder/note.md"])


class NavigationMutationTest(NavigationHTTPFixture):
    writable = True

    def test_rename_and_delete_links_leave_the_target_intact(self):
        _, moved = self.post_json("/api/rename", {"from": "/alias", "to": "/renamed"})
        self.assertEqual(moved, {"from": "/alias", "to": "/renamed"})
        self.assertTrue((self.root / "renamed").is_symlink())
        self.assertTrue((self.root / "folder/note.md").exists())
        self.post_json("/api/delete", {"path": "/renamed"})
        self.assertFalse((self.root / "renamed").is_symlink())
        self.assertTrue((self.root / "folder/note.md").exists())

    def test_a_dangling_link_is_not_overwritten_by_a_move(self):
        (self.root / "dangling").symlink_to(self.root / "missing")
        status, _ = self.expect_failure(lambda: self.post_json(
            "/api/rename", {"from": "/folder/note.md", "to": "/dangling"}))
        self.assertEqual(status, 409)
        self.assertTrue((self.root / "dangling").is_symlink())
        self.assertTrue((self.root / "folder/note.md").exists())
