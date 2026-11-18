import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402  (tests/run.py, the parallel runner)


class DealTest(unittest.TestCase):
    """Longest first, each onto the least-loaded worker."""

    def test_the_workers_finish_together(self):
        durations = {"m.a": 9, "m.b": 5, "m.c": 4, "m.d": 3, "m.e": 2, "m.f": 1}
        shards = run.deal(list(durations), durations, 2)
        loads = sorted(sum(durations[t] for t in shard) for shard in shards)
        self.assertEqual(loads, [12, 12])

    def test_the_longest_test_is_dealt_first(self):
        """Dealing a long test last is what leaves one worker straggling."""
        durations = {"m.short": 1, "m.long": 10}
        shards = run.deal(["m.short", "m.long"], durations, 1)
        self.assertEqual(shards[0][0], "m.long")

    def test_every_test_is_dealt_exactly_once(self):
        tests = ["m.t%d" % n for n in range(37)]
        shards = run.deal(tests, {}, 5)
        dealt = [t for shard in shards for t in shard]
        self.assertEqual(sorted(dealt), sorted(tests))

    def test_no_empty_workers_are_started(self):
        self.assertEqual(len(run.deal(["m.a", "m.b"], {}, 8)), 2)


class EstimateTest(unittest.TestCase):
    def test_a_recorded_duration_is_used(self):
        self.assertEqual(run.estimate("m.C.t", {"m.C.t": 3.5}), 3.5)

    def test_an_unknown_test_takes_its_modules_average(self):
        durations = {"test_x.A.one": 2.0, "test_x.A.two": 4.0}
        self.assertEqual(run.estimate("test_x.A.three", durations), 3.0)

    def test_an_unknown_browser_test_is_assumed_slow(self):
        """Before any run has been recorded, the browser tests must still be
        spread out rather than packed onto one worker as if instant."""
        self.assertGreater(
            run.estimate("test_browser.X.y", {}), run.estimate("test_paths.X.y", {}) * 10
        )

    def test_fast_mode_excludes_every_browser_test_module(self):
        from test_browser import BrowserCase
        from test_modules import ModuleTest

        suite = unittest.defaultTestLoader.discover(run.HERE, pattern="test*.py", top_level_dir=run.HERE)
        browser_modules = {run.module_of(test.id()) for test in run.flatten(suite)
                           if isinstance(test, (BrowserCase, ModuleTest))}
        self.assertEqual(browser_modules - set(run.BROWSER_MODULES), set())


class ResultTest(unittest.TestCase):
    def test_failed_subtests_are_reported_as_failures_instead_of_lost_tests(self):
        class FailingSubtests(unittest.TestCase):
            def runTest(self):
                for label in ("first failure", "second failure"):
                    with self.subTest(label=label):
                        self.fail(label)

        case = FailingSubtests()
        result = run.TimedResult(io.StringIO(), False, 0)
        case.run(result)
        record = result.records[case.id()]
        self.assertEqual(record["outcome"], "fail")
        self.assertIn("first failure", record["detail"])
        self.assertIn("second failure", record["detail"])


if __name__ == "__main__":
    unittest.main()
