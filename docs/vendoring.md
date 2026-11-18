# Maintaining bundled libraries

ficelo ships frontend libraries so normal use needs no downloads or build tools. Package versions, extracted files, and exclusions are defined in [tools/vendor.py](../tools/vendor.py); third-party attribution lives in [NOTICE](../NOTICE) and the bundled license files.

## Updating a dependency

1. Review the release and the licenses of code and fonts embedded in its bundles.
2. Change the pin in `tools/vendor.py`, update supplemental notices, and update the reviewed versions and notice hashes in `src/ficelo/web/vendor/licenses/manifest.json`.
3. Fetch the updated files, then verify them:

```sh
python3 tools/vendor.py
python3 tools/vendor.py --check
```

Both commands download upstream tarballs and require network access. The check compares them with the committed tree without replacing it. Notice validation runs before downloading; the manifest records reviewed files, but does not establish that a license review is complete.

Do not hand-edit generated bundles. Adapted styles in `src/ficelo/web/markdown.css` are maintained separately and retain their upstream notices under `vendor/md-reader-theme/`.

## Licenses and source availability

A package's top-level license may not cover everything in its generated bundle. Keep supplemental notices under `vendor/licenses/` and `vendor/md-reader-theme/`, outside directories replaced by the vendoring script.

- Preserve copyright and license notices for adapted Markdown styles.
- Keep the KaTeX font notice and respect its reserved font names.
- Review Mermaid's embedded dependencies, including code reached through source maps of prebuilt dependencies.
- Preserve ELK's notices and source-access statement; check its Java/GWT sources when upgrading.
- Pin source URLs to the release or commit actually distributed.

## Verification

Run focused rendering and language-service checks after dependency changes, then the full suite:

```sh
python3 tests/run.py -k markdown -k typescript_worker
python3 tests/run.py --strict
```

The Markdown compatibility corpus is in `tests/fixtures/markdown_rendering.json`. Monaco workers need a real language-service check: syntax highlighting can work even when a worker is missing. Keep required assets unless the matching behavior has been verified; an editor merely appearing is not sufficient.

For a release, build a source archive and wheel with `python3 -m build` in a build environment with the `build` package installed. Install the wheel in a separate venv and launch it outside the checkout. Verify that it serves its own frontend, including fonts and workers, and that every manifest-listed notice is included unchanged. Build tools are only needed for packaging, not for running ficelo.
