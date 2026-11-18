#!/usr/bin/env python3
"""Re-fetch the vendored frontend libraries.

The libraries are committed to the repository, so nobody installing or running
ficelo needs this — it exists so that "where did these bytes come from?" has an
executable answer, and so upgrading is one command rather than an afternoon.

    python3 tools/vendor.py            # fetch the pinned versions
    python3 tools/vendor.py --check    # verify what is committed, change nothing

Downloads from registry.npmjs.org, which serves package tarballs directly, so
no npm, node or network tooling beyond the standard library is involved.
"""

import argparse
import hashlib
import io
import json
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "src" / "ficelo" / "web" / "vendor"
REGISTRY = "https://registry.npmjs.org"

# Pinned deliberately. Upgrading is editing a version here and re-running, so
# that the diff shows both the version bump and every byte it changed.
PACKAGES: list[dict] = [
    {
        "name": "monaco-editor",
        "version": "0.56.0",
        "into": "monaco",
        "take": {"min/vs": "vs", "LICENSE": "LICENSE", "ThirdPartyNotices.txt": "ThirdPartyNotices.txt"},
        # Dropped after extraction, both verified unused by the 0.56 AMD
        # bundle — see docs/vendoring.md. `vs/language/**` is a second copy
        # of the language services that the hashed chunks never load, and
        # `vs/nls/lang/**` is the translations, which only a non-default
        # locale would ask for. Together, 9 MB of 24.
        #
        # The worker payloads under vs/assets/ are NOT dropped, though they
        # look equally removable: without them getTypeScriptWorker() never
        # settles — no error, no rejection, just a promise that hangs.
        "drop": ["vs/language", "vs/nls/lang"],
    },
    {
        # Only the UMD bundle, not the 123 MB dist: the chunked ESM build and
        # its source maps are for bundlers, and ficelo has none. Loaded lazily
        # by the page, so a document with no diagram in it never fetches 5 MB.
        "name": "mermaid",
        "version": "12.0.0",
        "into": "mermaid",
        "take": {"dist/mermaid.min.js": "mermaid.min.js", "LICENSE": "LICENSE"},
    },
    {
        "name": "marked",
        "version": "9.1.0",
        "into": "marked",
        "take": {"lib/marked.umd.js": "marked.umd.js", "LICENSE.md": "LICENSE"},
    },
    {
        "name": "@highlightjs/cdn-assets", "version": "11.9.0", "into": "highlight",
        "take": {"highlight.min.js": "highlight.min.js",
                 "languages/dockerfile.min.js": "dockerfile.min.js", "LICENSE": "LICENSE"},
    },
    {
        "name": "katex",
        "version": "0.19.1",
        "into": "katex",
        "take": {"dist/katex.mjs": "katex.mjs", "dist/katex.min.css": "katex.min.css",
                 "dist/fonts": "fonts", "LICENSE": "LICENSE"},
        "exclude_suffixes": [".ttf", ".woff"],
    },
    {
        "name": "@xterm/xterm", "version": "6.0.0", "into": "xterm",
        "take": {"lib/xterm.mjs": "xterm.mjs", "css/xterm.css": "xterm.css", "LICENSE": "LICENSE"},
    },
    {
        "name": "@xterm/addon-fit", "version": "0.11.0", "into": "xterm-fit",
        "take": {"lib/addon-fit.mjs": "addon-fit.mjs", "LICENSE": "LICENSE"},
    },
]


def fetch(name, version):
    # The scoped-package tarball path drops the scope from the filename.
    stem = name.split("/")[-1]
    url = "%s/%s/-/%s-%s.tgz" % (REGISTRY, name, stem, version)
    print("  fetching %s" % url)
    with urllib.request.urlopen(url, timeout=300) as response:
        return response.read()


def members(archive, wanted):
    """Map each wanted source path to the tar members under it.

    npm tarballs put everything under a top-level `package/`, which is
    stripped here so the pins read as the paths the project documents.
    """
    for member in archive.getmembers():
        if not member.isfile():
            continue
        rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
        for source, dest in wanted.items():
            if rel == source:
                relative = Path(dest)
            elif rel.startswith(source + "/"):
                relative = Path(dest) / rel[len(source) + 1:]
            else:
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("archive member escapes its destination: %s" % member.name)
            yield member, relative


def digest(root):
    """A stable hash of a tree, so --check can compare without re-downloading
    into the working copy."""
    sha = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        sha.update(str(path.relative_to(root)).encode())
        sha.update(path.read_bytes())
    return sha.hexdigest()


def notice_problems(root, packages):
    """Require reviewed supplemental notices before checking or replacing bundles.

    These files live outside the package directories that install() replaces.
    Changing a package version also requires reviewing its embedded licenses.
    """
    manifest = root / "licenses" / "manifest.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as error:
        return [f"cannot read licenses/manifest.json: {error}"]
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict) or not data["files"]:
        return ["licenses/manifest.json must list the reviewed notice files"]

    problems = []
    versions = {package["name"]: package["version"] for package in packages}
    if data.get("packages") != versions:
        problems.append("package versions changed; review the supplemental licenses")
    replaced = [Path(package["into"]) for package in packages]
    replaced += [path.with_name(path.name + ".incoming") for path in replaced]
    if any(path in Path("licenses/manifest.json").parents for path in replaced):
        problems.append("a package would replace licenses/manifest.json")
    for relative, expected in data["files"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            problems.append(f"notice path must stay inside vendor/: {relative}")
            continue
        if any(path == directory or directory in path.parents for directory in replaced):
            problems.append(f"notice would be replaced by vendoring: {relative}")
            continue
        try:
            content = (root / path).read_bytes()
        except OSError:
            problems.append(f"missing notice: {relative}")
            continue
        if not content.strip() or hashlib.sha256(content).hexdigest() != expected:
            problems.append(f"notice differs from its reviewed hash: {relative}")
    return problems


def install(package, target, check):
    archive = tarfile.open(fileobj=io.BytesIO(fetch(package["name"], package["version"])))
    staged = target.parent / (target.name + ".incoming")
    shutil.rmtree(staged, ignore_errors=True)
    for member, relative in members(archive, package["take"]):
        if relative.suffix in package.get("exclude_suffixes", []):
            continue
        out = staged / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        if extracted is None:
            continue
        out.write_bytes(extracted.read())

    for relative in package.get("drop", []):
        shutil.rmtree(staged / relative, ignore_errors=True)

    licence = staged / "LICENSE"
    if not licence.is_file() or not licence.read_bytes().strip():
        # Caught a real mistake once: extracting two packages into one staging
        # directory silently overwrote the first one's LICENSE with the
        # second's, and both shipped under the wrong terms.
        shutil.rmtree(staged, ignore_errors=True)
        raise SystemExit("%s: no LICENSE in the tarball; refusing to vendor it" % package["name"])

    if check:
        same = target.is_dir() and digest(target) == digest(staged)
        shutil.rmtree(staged, ignore_errors=True)
        return same

    shutil.rmtree(target, ignore_errors=True)
    staged.rename(target)
    return True


def main(argv=None):
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--check", action="store_true", help="verify, change nothing")
    options = args.parse_args(argv)

    problems = notice_problems(VENDOR, PACKAGES)
    if problems:
        print("Supplemental license review is incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("see docs/vendoring.md before refreshing bundles", file=sys.stderr)
        return 1

    VENDOR.mkdir(parents=True, exist_ok=True)
    drift = []
    for package in PACKAGES:
        print("%s %s" % (package["name"], package["version"]))
        if not install(package, VENDOR / package["into"], options.check):
            drift.append(package["name"])

    versions = "".join("%s %s\n" % (p["name"], p["version"]) for p in PACKAGES)
    manifest = VENDOR / "VERSIONS"
    if options.check:
        if not manifest.is_file() or manifest.read_text() != versions:
            drift.append("VERSIONS")
    else:
        manifest.write_text(versions)

    if drift:
        print("\ndiffers from the pinned tarball: %s" % ", ".join(drift), file=sys.stderr)
        print("run without --check to refresh", file=sys.stderr)
        return 1
    print("\n%s" % ("committed tree matches the pins" if options.check else "vendored"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
