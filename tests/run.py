#!/usr/bin/env python3
"""Run the test suite in parallel, balanced by how long each test takes.

    python3 tests/run.py               everything
    python3 tests/run.py --fast        skip the browser tests (a few seconds)
    python3 tests/run.py -k split      only tests whose id contains "split"
    python3 tests/run.py --failed      only what failed last time
    python3 tests/run.py -j 8          eight workers instead of the default
    python3 tests/run.py --strict      fail if anything was skipped (CI)

The plain `python3 -m unittest discover -s tests` still works and runs the
same tests one at a time. This exists because that takes minutes: almost
all of it is browser tests, each launching Chromium and loading the page,
waiting on each other while the rest of the machine sits idle.

How it divides the work: every test's last duration is kept in
tests/.durations.json (not committed). Tests are dealt out longest first,
each to whichever worker has the least work so far, so the workers finish
at about the same time rather than one straggling with all the slow ones.
A test with no recorded time is estimated from others in its module.

Each worker is a separate Python process running its share through unittest,
so a test that patches module state, starts a server or changes the
environment cannot affect another. Every test server already listens on a
port the OS picks, and every browser on one it picks itself, so parallel
workers never collide.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from typing import cast

HERE = os.path.dirname(os.path.abspath(__file__))
DURATIONS = os.path.join(HERE, ".durations.json")
LAST_FAILED = os.path.join(HERE, ".last-failed.json")

# Modules that drive a real browser: slow, and skipped by --fast.
BROWSER_MODULES = ("test_browser", "test_modules", "test_panel_resize", "test_panel_layout_browser", "test_terminal_browser", "test_navigation_browser",
                   "test_review_browser", "test_workspace_recovery", "test_reader_browser", "test_home_browser", "test_context_menu_browser", "test_markdown_parity", "test_markdown_interactions", "test_reading_background", "test_reading_contrast", "test_interface_background", "test_shortcuts", "test_file_actions_browser", "test_commit_scroll_browser", "test_review_auto_refresh_browser")

# Estimates for a test never timed before, until one run records it.
DEFAULT_SLOW = 2.0
DEFAULT_FAST = 0.05


# -- choosing what to run ------------------------------------------------------


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def discover():
    """Every test id, as `module.Class.method`."""
    sys.path.insert(0, HERE)
    loader = unittest.TestLoader()
    found = []
    for test in flatten(loader.discover(HERE, pattern="test*.py", top_level_dir=HERE)):
        test_id = test.id()
        # A module that failed to import shows up as a synthetic test; keep
        # it, so the failure is reported rather than the module vanishing.
        found.append(test_id)
    return found


def module_of(test_id):
    return test_id.split(".", 1)[0]


def read_json(path, fallback):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def estimate(test_id, durations):
    if test_id in durations:
        return durations[test_id]
    module = module_of(test_id)
    known = [t for k, t in durations.items() if module_of(k) == module]
    if known:
        return sum(known) / len(known)
    return DEFAULT_SLOW if module in BROWSER_MODULES else DEFAULT_FAST


def deal(test_ids, durations, workers):
    """Longest-first onto the least-loaded worker. Returns shard lists."""
    shards: list[list[str]] = [[] for _ in range(workers)]
    loads = [0.0] * workers
    for test_id in sorted(test_ids, key=lambda t: -estimate(t, durations)):
        least = loads.index(min(loads))
        shards[least].append(test_id)
        loads[least] += estimate(test_id, durations)
    return [shard for shard in shards if shard]


# -- one worker ----------------------------------------------------------------


class TimedResult(unittest.TextTestResult):
    """Records how long each test took and how it ended."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.perf_counter()
        super().startTest(test)

    def _finish(self, test, outcome, detail=None):
        began = self.started.get(test.id(), time.perf_counter())
        self.records[test.id()] = {
            "outcome": outcome,
            "seconds": round(time.perf_counter() - began, 3),
            "detail": detail,
        }

    def stopTest(self, test):
        began = self.started.pop(test.id(), None)
        if began is not None and test.id() in self.records:
            self.records[test.id()]["seconds"] = round(time.perf_counter() - began, 3)
        super().stopTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._finish(test, "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._finish(test, "fail", "".join(traceback.format_exception(*err)))

    def addError(self, test, err):
        super().addError(test, err)
        self._finish(test, "error", "".join(traceback.format_exception(*err)))

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            previous = self.records.get(test.id(), {})
            outcome = "fail" if issubclass(err[0], test.failureException) else "error"
            detail = "".join(traceback.format_exception(*err))
            if previous.get("detail"):
                detail = previous["detail"] + "\n" + detail
            self._finish(test, "error" if previous.get("outcome") == "error" else outcome, detail)

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._finish(test, "skip", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._finish(test, "ok")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._finish(test, "fail", "unexpected success")


def work(ids_file, out_file):
    """Run the ids listed in one file and write the records to another."""
    sys.path.insert(0, HERE)
    with open(ids_file) as handle:
        ids = json.load(handle)
    suite = unittest.TestLoader().loadTestsFromNames(ids)
    with open(os.devnull, "w") as quiet:
        runner = unittest.TextTestRunner(stream=quiet, resultclass=TimedResult, verbosity=0)
        result = cast(TimedResult, runner.run(suite))
    with open(out_file, "w") as handle:
        json.dump(result.records, handle)
    return 0


# -- the coordinator -----------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", action="append", default=[], metavar="TEXT",
                        help="only tests whose id contains TEXT (repeatable: any match)")
    parser.add_argument("--fast", action="store_true", help="skip the browser tests")
    parser.add_argument("--failed", action="store_true", help="only what failed last run")
    parser.add_argument("-j", "--jobs", type=int, default=0,
                        help="worker processes (default: one per CPU, at most 32)")
    parser.add_argument("--list", action="store_true", help="print the chosen tests and stop")
    parser.add_argument("--strict", action="store_true",
                        help="fail if any test was skipped (CI uses this, so a machine "
                        "without Chromium cannot pass by quietly running fewer tests)")
    parser.add_argument("--worker", nargs=2, metavar=("IDS", "OUT"), help=argparse.SUPPRESS)
    options = parser.parse_args(argv)

    if options.worker:
        return work(*options.worker)

    chosen = discover()
    if options.failed:
        previous = set(read_json(LAST_FAILED, []))
        chosen = [t for t in chosen if t in previous]
    if options.fast:
        chosen = [t for t in chosen if module_of(t) not in BROWSER_MODULES]
    if options.k:
        chosen = [t for t in chosen if any(text in t for text in options.k)]
    if not chosen:
        print("no tests selected", file=sys.stderr)
        return 1
    if options.list:
        print("\n".join(chosen))
        return 0

    durations = read_json(DURATIONS, {})
    jobs = options.jobs or min(32, os.cpu_count() or 1)
    shards = deal(chosen, durations, min(jobs, len(chosen)))
    expected = max(sum(estimate(t, durations) for t in shard) for shard in shards)

    print("%d tests across %d workers (longest share ~%.0fs by past timings)"
          % (len(chosen), len(shards), expected))
    began = time.perf_counter()
    scratch = tempfile.mkdtemp(prefix="ficelo-tests-")
    running = []
    for index, shard in enumerate(shards):
        ids_file = os.path.join(scratch, "ids-%d.json" % index)
        out_file = os.path.join(scratch, "out-%d.json" % index)
        with open(ids_file, "w") as handle:
            json.dump(shard, handle)
        log = open(os.path.join(scratch, "log-%d.txt" % index), "w")
        process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--worker", ids_file, out_file],
            stdout=log, stderr=subprocess.STDOUT,
        )
        running.append((process, shard, out_file, log))

    records = {}
    lost = []
    for process, shard, out_file, log in running:
        process.wait()
        log.close()
        found = read_json(out_file, None)
        if found is None:
            # The worker died before reporting (a crash, a kill). Say which
            # tests that took with it rather than reporting them as passed.
            lost.extend(shard)
            continue
        records.update(found)
        missing = [t for t in shard if t not in found]
        lost.extend(missing)
    elapsed = time.perf_counter() - began

    failures = {t: r for t, r in records.items() if r["outcome"] in ("fail", "error")}
    skipped = {t: r for t, r in records.items() if r["outcome"] == "skip"}
    for test_id, record in sorted(failures.items()):
        print("\n" + "=" * 70)
        print("%s: %s" % (record["outcome"].upper(), test_id))
        print("-" * 70)
        print(record["detail"])
    for test_id in lost:
        print("LOST (its worker died; see %s): %s" % (scratch, test_id))

    # Durations from this run replace the old ones; tests not run keep theirs.
    durations.update({t: r["seconds"] for t, r in records.items() if r["outcome"] != "skip"})
    with open(DURATIONS, "w") as handle:
        json.dump(durations, handle, indent=0, sort_keys=True)
    with open(LAST_FAILED, "w") as handle:
        json.dump(sorted(list(failures) + lost), handle)

    serial = sum(r["seconds"] for r in records.values())
    slowest = sorted(records.items(), key=lambda item: -item[1]["seconds"])[:3]
    print("\n%d ran, %d failed, %d skipped, %d lost — %.1fs wall clock (%.0fs of work, %.0fx)"
          % (len(records), len(failures), len(skipped), len(lost), elapsed, serial,
             serial / elapsed if elapsed else 0))
    print("slowest: " + ", ".join("%s %.1fs" % (t.rsplit(".", 1)[-1], r["seconds"]) for t, r in slowest))
    if skipped:
        print("skipped: %d (e.g. %s)" % (len(skipped), next(iter(skipped.values()))["detail"]))
    if options.strict and skipped:
        print("--strict: %d test(s) skipped; failing" % len(skipped), file=sys.stderr)
        return 1
    return 1 if failures or lost else 0


if __name__ == "__main__":
    sys.exit(main())
