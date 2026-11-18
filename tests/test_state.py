import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import cli, state  # noqa: E402


class HomeTest(unittest.TestCase):
    def test_it_lives_in_a_dot_folder_in_home(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}, clear=False):
            os.environ.pop("FICELO_HOME", None)
            self.assertEqual(state.home(), Path("/home/someone/.ficelo"))

    def test_ficelo_home_overrides_it(self):
        with mock.patch.dict(os.environ, {"FICELO_HOME": "/srv/ficelo-state"}):
            self.assertEqual(state.home(), Path("/srv/ficelo-state"))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp()) / ".ficelo"
        self.settings = state.Settings(self.folder)

    def test_nothing_saved_reads_as_empty(self):
        self.assertEqual(self.settings.read(), {})

    def test_a_value_round_trips(self):
        self.settings.put("ficelo.theme", {"name": "dark"})
        self.assertEqual(self.settings.read(), {"ficelo.theme": {"name": "dark"}})

    def test_the_folder_is_created_private(self):
        """Settings name files and folders on the machine."""
        self.settings.put("ficelo.theme", "dark")
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)

    def test_it_is_plain_json_a_person_can_read(self):
        self.settings.put("ficelo.theme", "dark")
        on_disk = json.loads((self.folder / "settings.json").read_text())
        self.assertEqual(on_disk, {"ficelo.theme": "dark"})

    def test_none_removes_a_key(self):
        self.settings.put("ficelo.theme", "dark")
        self.settings.put("ficelo.theme", None)
        self.assertEqual(self.settings.read(), {})

    def test_two_keys_do_not_undo_each_other(self):
        """Two browser tabs saving different settings at once."""
        self.settings.put("ficelo.a", 1)
        self.settings.put("ficelo.b", 2)
        self.assertEqual(self.settings.read(), {"ficelo.a": 1, "ficelo.b": 2})

    def test_concurrent_writes_all_land(self):
        threads = [
            threading.Thread(target=self.settings.put, args=("ficelo.k%d" % n, n))
            for n in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.settings.read()), 20)

    def test_a_read_during_a_slow_write_sees_the_new_value(self):
        """A page saves a setting and reloads straight away. If the read
        does not wait for a save that is still flushing to disk, it returns
        the old file — and the new page then saves the old value back over
        the new one. Reproduced reliably under parallel test load; here the
        save is made slow on purpose so it happens every time."""
        self.settings.put("ficelo.star", False)
        real_write = state.files.write_text
        writing = threading.Event()

        def slow_write(path, text, expect=None):
            writing.set()
            time.sleep(0.3)
            return real_write(path, text, expect)

        seen = {}
        with mock.patch.object(state.files, "write_text", side_effect=slow_write):
            writer = threading.Thread(target=self.settings.put, args=("ficelo.star", True))
            writer.start()
            writing.wait(5)
            seen.update(self.settings.read())  # arrives mid-write
            writer.join()
        self.assertEqual(seen.get("ficelo.star"), True, "the read returned the file from before the save")

    def test_a_key_outside_the_namespace_is_refused(self):
        for key in ("other.thing", "ficelo.", "ficelo.../../etc/passwd", "ficelo.A", "", None, 3):
            with self.assertRaises(state.BadState, msg=repr(key)):
                self.settings.put(key, 1)

    def test_an_oversized_value_is_refused(self):
        with self.assertRaises(state.BadState):
            self.settings.put("ficelo.big", "x" * (state.MAX_VALUE_BYTES + 10))
        self.assertEqual(self.settings.read(), {})

    def test_a_corrupt_file_reads_as_empty_rather_than_failing(self):
        """A hand-edit gone wrong should cost the settings, not the page."""
        self.folder.mkdir(parents=True)
        (self.folder / "settings.json").write_text("{not json")
        self.assertEqual(self.settings.read(), {})

    def test_foreign_keys_in_the_file_are_ignored(self):
        self.folder.mkdir(parents=True)
        (self.folder / "settings.json").write_text('{"ficelo.ok": 1, "evil": 2}')
        self.assertEqual(self.settings.read(), {"ficelo.ok": 1})


class WorkspaceStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = state.Settings(Path(self.temp.name))
        self.key = "ficelo.workspace.one"

    def patch(self, path, value=None, remove=False, key=None):
        return self.settings.patch(key or self.key, [{"path": path, "value": value, "remove": remove}])

    def test_concurrent_changes_keep_other_tabs_and_positions(self):
        self.patch(["tabs", "/a.md"], {"id": "/a.md", "path": "/a.md"})
        workers = [threading.Thread(target=self.patch, args=(["state", "positions", str(n)], {"top": n})) for n in range(20)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        record = self.settings.read()[self.key]
        self.assertIn("/a.md", record["tabs"])
        self.assertEqual(len(record["state"]["positions"]), 20)
        self.patch(["tabs", "/a.md"], remove=True)
        self.assertEqual(self.settings.read()[self.key]["tabs"], {})

    def test_unload_replay_deduplicates_out_of_order_writes(self):
        first = {"path": ["name"], "value": "first", "writer": "page-a", "sequence": 1}
        last = {"path": ["name"], "value": "last", "writer": "page-a", "sequence": 2}
        self.settings.patch(self.key, [first, last])
        self.settings.patch(self.key, [first])
        self.assertEqual(self.settings.read()[self.key]["name"], "last")
        self.patch(["name"], "another reader")
        self.settings.patch(self.key, [first, last])
        self.assertEqual(self.settings.read()[self.key]["name"], "another reader")
        restarted = state.Settings(Path(self.temp.name))
        restarted.patch(self.key, [first, last])
        self.assertEqual(restarted.read()[self.key]["name"], "another reader")
        self.assertNotIn("_writers", restarted.read())

    def test_session_activity_keeps_the_newest_time_from_different_browsers(self):
        self.patch(["name"], "Shared")
        for writer, at in (("recent", 300), ("delayed", 200), ("latest", 400)):
            result = self.settings.patch(self.key, [{
                "path": ["at"], "value": at, "writer": writer, "sequence": 1,
            }])
            self.assertEqual(result["value"], {"name": "Shared", "at": max(300, at)})
        restarted = state.Settings(Path(self.temp.name))
        restarted.patch(self.key, [{"path": ["at"], "value": 100, "writer": "offline", "sequence": 1}])
        self.assertEqual(restarted.read()[self.key]["at"], 400)
        self.patch(["at"], 300, key="ficelo.other")
        self.patch(["at"], 200, key="ficelo.other")
        self.assertEqual(self.settings.read()["ficelo.other"]["at"], 200)

    def test_deleted_workspace_ignores_stale_saves_until_restored(self):
        self.patch(["name"], "kept")
        self.patch(["deletedAt"], 12)
        self.patch(["name"], "stale")
        self.assertEqual(self.settings.read()[self.key]["name"], "kept")
        self.patch(["deletedAt"], remove=True)
        self.patch(["name"], "restored")
        self.assertEqual(self.settings.read()[self.key]["name"], "restored")

    def test_merge_preserves_missing_tabs_and_redirects_stale_additions(self):
        one = {"id": "one", "name": "First", "tabs": {"/missing": {"id": "/missing", "path": "/missing"}}, "order": ["/missing"]}
        two = {"id": "two", "name": "Second", "tabs": {"/b": {"id": "/b", "path": "/b"}}, "order": ["/b"]}
        self.settings.put(self.key, one)
        self.settings.put("ficelo.workspace.two", two)
        result = self.settings.merge_workspaces({"one": one, "two": two})
        self.assertTrue((Path(self.temp.name) / result["backup"]).is_file())
        self.patch(["name"], "stale", key="ficelo.workspace.two")
        self.patch(["tabs", "/new"], {"id": "/new"}, key="ficelo.workspace.two")
        self.patch(["tabs", "/missing"], remove=True, key="ficelo.workspace.two")
        saved = self.settings.read()[self.key]
        self.assertEqual(saved["name"], "First")
        self.assertEqual(set(saved["tabs"]), {"/missing", "/b", "/new"})

    def test_merge_refuses_a_stale_preview_without_writing(self):
        one, two = {"id": "one"}, {"id": "two"}
        self.settings.put(self.key, one)
        self.settings.put("ficelo.workspace.two", two)
        self.patch(["name"], "changed")
        before = self.settings.path.read_bytes()
        with self.assertRaises(state.BadState):
            self.settings.merge_workspaces({"one": one, "two": two})
        self.assertEqual(before, self.settings.path.read_bytes())

    def test_patch_validation_is_atomic_and_rejects_prototype_fields(self):
        for changes in ([{"path": ["name"], "value": "partial"}, {"path": 7}], [{"path": ["__proto__", "x"], "value": 1}]):
            with self.assertRaises(state.BadState):
                self.settings.patch(self.key, changes)
            self.assertEqual(self.settings.read(), {})


class ConfigFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def write(self, text):
        (self.folder / "config.json").write_text(text)

    def test_no_file_means_no_defaults(self):
        self.assertEqual(state.read_config(self.folder), {})

    def test_known_keys_are_read(self):
        self.write('{"port": 9000, "write": true, "host": "::", "root": "/srv", "hidden": false}')
        self.assertEqual(
            state.read_config(self.folder),
            {"port": 9000, "write": True, "host": "::", "root": "/srv", "hidden": False},
        )

    def test_an_unknown_key_is_an_error_naming_it(self):
        """A typo silently ignored is a setting that mysteriously does
        nothing."""
        self.write('{"prot": 9000}')
        with self.assertRaises(state.BadState) as caught:
            state.read_config(self.folder)
        self.assertIn("prot", str(caught.exception))

    def test_a_wrong_type_is_an_error(self):
        self.write('{"port": "9000"}')
        with self.assertRaises(state.BadState):
            state.read_config(self.folder)

    def test_true_is_not_a_port(self):
        """bool is a subclass of int; without the explicit check this would
        listen on port 1."""
        self.write('{"port": true}')
        with self.assertRaises(state.BadState):
            state.read_config(self.folder)

    def test_allowed_origins_are_lists_of_strings(self):
        self.write('{"allowed_origins":["https://files.example.test"]}')
        self.assertEqual(state.read_config(self.folder)["allowed_origins"], ["https://files.example.test"])
        for raw in ['{"allowed_origins":"https://files.example.test"}', '{"allowed_origins":[null]}']:
            self.write(raw)
            with self.assertRaises(state.BadState):
                state.read_config(self.folder)

    def test_invalid_json_is_an_error_with_the_path(self):
        self.write("{port: 1}")
        with self.assertRaises(state.BadState) as caught:
            state.read_config(self.folder)
        self.assertIn("config.json", str(caught.exception))


class ConfigPrecedenceTest(unittest.TestCase):
    """Flags win over config.json, which wins over the built-in defaults."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        patch = mock.patch.dict(os.environ, {"FICELO_HOME": str(self.folder)})
        patch.start()
        self.addCleanup(patch.stop)
        self.seen = {}

        def capture(config):
            self.seen["config"] = config
            raise SystemExit(0)

        server_patch = mock.patch.object(cli, "serve", side_effect=capture)
        server_patch.start()
        self.addCleanup(server_patch.stop)

    def run_cli(self, *argv):
        with self.assertRaises(SystemExit):
            cli.main(list(argv))
        return self.seen["config"]

    def test_config_file_values_are_used(self):
        (self.folder / "config.json").write_text('{"port": 9123, "write": true}')
        config = self.run_cli()
        self.assertEqual(config.port, 9123)
        self.assertTrue(config.writable)

    def test_a_flag_beats_the_config_file(self):
        (self.folder / "config.json").write_text('{"port": 9123}')
        self.assertEqual(self.run_cli("--port", "9456").port, 9456)

    def test_saved_read_only_is_preserved_until_explicitly_overridden(self):
        (self.folder / "config.json").write_text('{"write": false}')
        self.assertFalse(self.run_cli().writable)
        self.assertTrue(self.run_cli("--write").writable)

    def test_read_only_flag_overrides_default_and_saved_write_mode(self):
        self.assertFalse(self.run_cli("--read-only").writable)
        (self.folder / "config.json").write_text('{"write": true}')
        self.assertFalse(self.run_cli("--read-only").writable)

    def test_proxy_origins_load_from_config_and_flags_override_them(self):
        (self.folder / "config.json").write_text('{"allowed_origins":["https://files.example.test"]}')
        self.assertEqual(self.run_cli().allowed_origins, {"https://files.example.test"})
        config = self.run_cli("--allow-origin", "https://other.example.test/", "--allow-origin", "http://localhost:8777")
        self.assertEqual(config.allowed_origins, {"https://other.example.test", "http://localhost:8777"})

    def test_bad_origin_stops_startup_before_listening(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(cli.main(["--allow-origin", "*"]), 2)
        self.assertIn("allowed origins", err.getvalue())
        self.assertNotIn("config", self.seen)

    def test_without_either_the_built_in_defaults_apply(self):
        config = self.run_cli()
        self.assertEqual((config.host, config.port, config.writable), ("127.0.0.1", 9248, True))

    def test_the_state_folder_follows_ficelo_home(self):
        self.assertEqual(self.run_cli().state_dir, self.folder)

    def test_a_broken_config_stops_startup_with_the_reason(self):
        (self.folder / "config.json").write_text('{"prot": 1}')
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main([])
        self.assertEqual(code, 2)
        self.assertIn("prot", err.getvalue())


if __name__ == "__main__":
    unittest.main()
