# Contributing with coding agents

ficelo is a file browser, Markdown reader, code editor, and repository review tool for Linux and macOS. Keep changes portable, focused, and easy to verify.

## Project boundaries

- Keep the Python runtime standard-library-only and respect the minimum Python version in `pyproject.toml`.
- Use the existing plain JavaScript and CSS. Frontend libraries are bundled; running ficelo must not require Node.js, npm, a frontend build, or network downloads.
- Keep configuration under `~/.ficelo/` by default and respect `FICELO_HOME`. The served root and the user's Home preference are separate concepts.
- Read-only mode is enforced by the server for its entire lifetime. It may save application preferences, but must reject changes to served files and terminal creation.
- Preserve unsaved edits during navigation, session updates, and automatic refresh. Explicitly discarding them requires confirmation.
- Preserve path confinement, origin checks, HTML sanitization, and write-conflict protection. Treat paths, rendered content, and repository output as untrusted.

Read the relevant implementation and neighboring tests before editing. Reuse existing helpers for paths, settings, dialogs, rendering, and repository operations. Do not revert unrelated work in a shared checkout.

## Local development

Run from the repository root without installing anything:

```sh
PYTHONPATH=src python3 -m ficelo
```

Use a disposable folder and separate state for manual testing:

```sh
ficelo_demo=$(mktemp -d)
mkdir "$ficelo_demo/files"
FICELO_HOME="$ficelo_demo/state" PYTHONPATH=src python3 -m ficelo "$ficelo_demo/files" --port 9249
```

Choose an unused port and use the address printed at startup. Keep test servers on loopback and stop them when finished. Do not use personal files as fixtures. Installing an editable package in a venv is optional when testing packaging or the `ficelo` entry point.

## Verification

Run checks locally; do not add GitHub Actions automation unless requested.

```sh
python3 tests/run.py -k navigation  # focused checks
python3 tests/run.py --fast         # checks without a browser
python3 tests/run.py --failed       # retry failures
python3 tests/run.py --strict       # full suite; skipped tests fail
```

Browser tests use Chrome or Chromium through `tests/cdp.py`; set `FICELO_CHROME` if discovery fails. Git integration tests require Git. No third-party Python test runner or browser automation package is needed.

- Start with checks relevant to the change. Run the full strict suite for changes affecting shared behavior or several components. Report missing prerequisites and skipped checks honestly.
- Add regression coverage for bugs. Prefer observable behavior over tests that repeat the implementation. Documentation and small styling changes usually need only focused inspection or an existing check.
- Keep tests isolated with temporary files, separate state, and dynamically allocated ports. Wait for observable state instead of fixed sleeps. Preserve the parallel runner's speed.
- For static assets or packaging changes, verify that an installed wheel contains and serves the updated files. For dependency changes, follow [the vendoring guide](docs/vendoring.md).
- Review the final diff and run `git diff --check` before committing. Report what changed, relevant verification, and any remaining limitation.

## Documentation and public contributions

Keep documentation focused on setup, workflows, and constraints that are not obvious from the UI. Code and command help are the source of truth for defaults, option lists, limits, and dependency versions; avoid duplicating them in prose.

Use generic fixtures and public links. Never commit credentials, private hostnames, internal URLs, personal machine paths, user state, build output, or private source material. Preserve third-party licenses and attribution in `NOTICE` and the bundled license files.
