import base64
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ficelo import cli, state, terminal
from ficelo.terminal import Pool, Session, TerminalError, dimensions


class TerminalSessionTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        with mock.patch.dict(os.environ, {"ENV": "/dev/null", "HISTFILE": "/dev/null"}):
            self.session = Session(self.folder.name, shell="/bin/sh")
        self.addCleanup(self.session.close)
        self.cursor = 0
        self.output = b""

    def output_until(self, marker):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.session.read(self.cursor)
            self.cursor = result["cursor"]
            self.output += base64.b64decode(result["data"])
            if marker in self.output:
                return self.output
            time.sleep(0.02)
        self.fail("terminal output did not contain the command result")

    def test_interactive_shell_has_tty_working_directory_and_resize(self):
        self.session.resize(91, 31)
        self.session.write(b"printf 'START_%s_END\\n' result; pwd; stty size\n")
        output = self.output_until(b"31 91")
        self.assertIn(b"START_result_END", output)
        self.assertIn(self.folder.name.encode(), output)
        self.assertEqual(self.session.read(self.cursor)["data"], "")

    def test_control_c_interrupts_a_foreground_command(self):
        self.session.write(b"sleep 30\n")
        self.output_until(b"sleep 30")
        self.session.write(b"\x03printf 'AFTER_%s_END\\n' interrupt\n")
        self.assertIn(b"AFTER_interrupt_END", self.output_until(b"AFTER_interrupt_END"))

    def test_close_reaps_the_shell_and_refuses_more_input(self):
        self.session.close()
        self.assertIsNotNone(self.session.process.poll())
        with self.assertRaises(TerminalError):
            self.session.write(b"echo unexpected\n")

    def test_invalid_size_and_input_are_bounded(self):
        for size in [(0, 20), (80, True), (501, 24)]:
            with self.assertRaises(TerminalError):
                dimensions(*size)
        with self.assertRaises(TerminalError):
            self.session.write(b"x" * 65537)

    def test_close_cannot_recycle_descriptor_under_pending_input(self):
        paused, resume = threading.Event(), threading.Event()
        errors = []
        select = terminal.select.select

        def controlled_select(readable, writable, exceptional, timeout):
            if writable and threading.current_thread().name == "pending-input":
                paused.set()
                if not resume.wait(3):
                    raise AssertionError("test input was not released")
            return select(readable, writable, exceptional, timeout)

        def write():
            try:
                self.session.write(b"echo old-input\n")
            except TerminalError as error:
                errors.append(error)

        with mock.patch.object(terminal.select, "select", controlled_select):
            writer = threading.Thread(target=write, name="pending-input")
            writer.start()
            self.assertTrue(paused.wait(2))
            closer = threading.Thread(target=self.session.close)
            closer.start()
            try:
                self.assertTrue(self.session.closed.wait(2))
                closer.join(.1)
                with mock.patch.dict(os.environ, {"ENV": "/dev/null", "HISTFILE": "/dev/null"}):
                    second = Session(self.folder.name, shell="/bin/sh")
                try:
                    self.assertNotEqual(second.master, self.session.master)
                finally:
                    second.close()
            finally:
                resume.set()
                writer.join(3)
                closer.join(3)
        self.assertFalse(writer.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(len(errors), 1)

    def test_stopped_pool_cannot_start_a_shell_without_a_reaper(self):
        pool = Pool()
        pool.close()
        with self.assertRaises(TerminalError):
            pool.create(self.folder.name)


class TerminalFlagsTest(unittest.TestCase):
    def test_terminals_are_opt_in_with_the_default_write_mode(self):
        with (tempfile.TemporaryDirectory() as folder,
              mock.patch.object(state, "home", return_value=Path(folder)),
              mock.patch.object(cli, "serve", side_effect=RuntimeError("captured")) as serve):
            for flags, enabled in (([], False), (["--terminal"], True), (["--terminal", "--write"], True)):
                with self.subTest(flags=flags), self.assertRaisesRegex(RuntimeError, "captured"):
                    cli.main(flags)
                self.assertTrue(serve.call_args[0][0].writable)
                self.assertEqual(serve.call_args[0][0].terminal, enabled)

    def test_terminal_is_rejected_in_explicit_and_saved_read_only_mode(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(state, "home", return_value=Path(folder)):
            with mock.patch.object(cli, "serve") as serve:
                for saved, flags in (("{}", ["--read-only", "--terminal"]),
                                     ('{"write":false}', ["--terminal"]),
                                     ('{"terminal":true}', ["--read-only"])):
                    (Path(folder) / "config.json").write_text(saved)
                    errors = io.StringIO()
                    with self.subTest(saved=saved, flags=flags), redirect_stderr(errors):
                        self.assertEqual(cli.main(flags), 2)
                    self.assertIn("requires read-write mode", errors.getvalue())
                serve.assert_not_called()
            (Path(folder) / "config.json").write_text('{"write":false,"terminal":true}')
            with mock.patch.object(cli, "serve", side_effect=RuntimeError("captured")) as serve:
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    cli.main(["--write"])
                self.assertTrue(serve.call_args[0][0].terminal)
