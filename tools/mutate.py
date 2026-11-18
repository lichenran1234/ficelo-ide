#!/usr/bin/env python3
"""Check that the tests would notice if the code were wrong.

A test that passes against any implementation is worse than no test, because
it reports confidence it has not earned. This applies a deliberate break,
runs the tests that should object, and reports whether they did.

    python3 tools/mutate.py tools/cases/v01.py
    python3 tools/mutate.py tools/cases/v01.py --check

Every mutation is reverted with `git checkout`, in a finally and again at
exit, because an earlier harness was killed by a timeout mid-mutation and
left a broken line in the tree. Files must be committed for that to work;
the script refuses to touch a dirty tree.
"""

import argparse
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=True, text=True)


def dirty():
    return [
        line[3:]
        for line in git("status", "--porcelain").stdout.splitlines()
        if line and not line.startswith("??")
    ]


def restore(path):
    git("checkout", "--", path)


def run_tests(module, timeout):
    result = subprocess.run(
        [sys.executable, "-m", "unittest", module, "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    summary = [
        line for line in result.stderr.strip().splitlines() if line.startswith(("FAILED", "OK"))
    ]
    return result.returncode, (summary[-1] if summary else "?")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", help="a python file defining CASES")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--check", action="store_true", help="verify source targets without modifying files or running mutations")
    options = parser.parse_args(argv)

    if not options.check:
        checkout = git("rev-parse", "--show-toplevel")
        if checkout.returncode or Path(checkout.stdout.strip()).resolve() != ROOT:
            print("refusing to run: mutations require this project's Git checkout for restoration", file=sys.stderr)
            return 2

    if not options.check and dirty():
        print("refusing to run: commit first, or the revert will lose work", file=sys.stderr)
        for path in dirty():
            print("  modified:", path, file=sys.stderr)
        return 2

    cases = runpy.run_path(options.cases)["CASES"]

    # Checked before anything runs. A case whose `before` text no longer
    # appears is silently testing nothing, and finding that out at the end
    # of a twenty-minute run — or not at all, because it was edited away —
    # is how a module loses its coverage without anyone noticing.
    stale = []
    for path, name, old, _new, _module in cases:
        if (ROOT / path).read_text().count(old) != 1:
            stale.append(name)
    print("%d cases across %d files" % (cases and len(cases) or 0,
                                        len({case[0] for case in cases})))
    if stale:
        print("\n%d case(s) match nothing and would test nothing:" % len(stale), file=sys.stderr)
        for name in stale:
            print("  " + name, file=sys.stderr)
        return 2

    if options.check:
        print("all mutation targets match the current source")
        return 0

    caught = missed = broken = 0
    try:
        for case in cases:
            path, name, old, new, module = case
            source = (ROOT / path).read_text()
            if source.count(old) != 1:
                print("NO MATCH  %-62s (%d occurrences)" % (name, source.count(old)))
                broken += 1
                continue
            try:
                (ROOT / path).write_text(source.replace(old, new, 1))
                code, summary = run_tests(module, options.timeout)
                # A mutation that hangs is caught too: the behaviour changed in
                # a way the tests noticed, even if they noticed by not finishing.
                print("%-8s  %-62s %s" % ("CAUGHT" if code else "MISSED", name, summary))
                caught += bool(code)
                missed += not code
            except subprocess.TimeoutExpired:
                print("%-8s  %-62s (timed out)" % ("CAUGHT", name))
                caught += 1
            finally:
                restore(path)
    finally:
        for case in cases:
            restore(case[0])

    print("\n%d caught, %d missed, %d unmatched" % (caught, missed, broken))
    if dirty():
        print("WARNING: tree still dirty after restore", file=sys.stderr)
        return 3
    return 1 if (missed or broken) else 0


if __name__ == "__main__":
    sys.exit(main())
