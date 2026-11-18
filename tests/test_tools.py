"""Development checks remain read-only and match the shipped source."""

import contextlib
import hashlib
import importlib.util
import io
import json
import runpy
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("ficelo_vendor_tool", ROOT / "tools/vendor.py")
if SPEC is None or SPEC.loader is None:
    raise ImportError("cannot load the vendoring tool")
vendor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vendor)
MUTATE_SPEC = importlib.util.spec_from_file_location("ficelo_mutation_tool", ROOT / "tools/mutate.py")
if MUTATE_SPEC is None or MUTATE_SPEC.loader is None:
    raise ImportError("cannot load the mutation tool")
mutate = importlib.util.module_from_spec(MUTATE_SPEC)
MUTATE_SPEC.loader.exec_module(mutate)


def write_notice_manifest(root, packages):
    notice = root / "licenses" / "third-party.txt"
    notice.parent.mkdir(parents=True)
    notice.write_text("A supplemental upstream copyright and license notice.\n")
    manifest = {
        "packages": {package["name"]: package["version"] for package in packages},
        "files": {"licenses/third-party.txt": hashlib.sha256(notice.read_bytes()).hexdigest()},
    }
    (notice.parent / "manifest.json").write_text(json.dumps(manifest))
    return notice


class VendorCheckTest(unittest.TestCase):
    def check_manifest(self, content):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_notice_manifest(root, [])
            manifest = root / "VERSIONS"
            if content is not None:
                manifest.write_text(content)
            before = manifest.read_bytes() if manifest.exists() else None
            with patch.object(vendor, "VENDOR", root), patch.object(vendor, "PACKAGES", []), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                status = vendor.main(["--check"])
            after = manifest.read_bytes() if manifest.exists() else None
            self.assertEqual(after, before, "checking must never repair the manifest")
            return status

    def test_a_matching_manifest_passes_without_writing(self):
        self.assertEqual(self.check_manifest(""), 0)

    def test_a_stale_manifest_fails_without_rewriting_it(self):
        self.assertEqual(self.check_manifest("outdated 1.0\n"), 1)

    def test_a_missing_manifest_fails_without_creating_it(self):
        self.assertEqual(self.check_manifest(None), 1)

    def test_archive_paths_cannot_escape_the_destination(self):
        class Archive:
            def getmembers(self):
                return [tarfile.TarInfo("package/dist/../../outside.js")]

        with self.assertRaisesRegex(ValueError, "escapes its destination"):
            list(vendor.members(Archive(), {"dist": "assets"}))


class VendorNoticesTest(unittest.TestCase):
    PACKAGE: ClassVar[dict] = {"name": "example", "version": "1.0", "into": "example",
                              "take": {"LICENSE": "LICENSE", "entry.js": "entry.js"}}

    def test_incomplete_notice_review_blocks_check_and_refresh_without_writes(self):
        for args in ([], ["--check"]):
            for damage in ("missing", "changed", "version", "manifest"):
                with self.subTest(args=args, damage=damage), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    notice = write_notice_manifest(root, [self.PACKAGE])
                    packages = [dict(self.PACKAGE)]
                    if damage == "missing":
                        notice.unlink()
                    elif damage == "changed":
                        notice.write_text("Truncated notice.\n")
                    elif damage == "version":
                        packages[0]["version"] = "2.0"
                    else:
                        (notice.parent / "manifest.json").unlink()
                    before = vendor.digest(root)
                    with patch.object(vendor, "VENDOR", root), patch.object(vendor, "PACKAGES", packages), \
                            patch.object(vendor, "fetch") as fetch, \
                            contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(vendor.main(args), 1)
                        fetch.assert_not_called()
                    self.assertEqual(vendor.digest(root), before)

    def test_notices_cannot_live_in_replaced_or_staging_directories(self):
        for directory in ("example", "example.incoming", "licenses"):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                packages = [dict(self.PACKAGE)]
                notice = write_notice_manifest(root, packages)
                manifest = notice.parent / "manifest.json"
                if directory == "licenses":
                    packages[0]["into"] = "licenses"
                else:
                    destination = root / directory / notice.name
                    destination.parent.mkdir()
                    notice.rename(destination)
                    data = json.loads(manifest.read_text())
                    expected = next(iter(data["files"].values()))
                    data["files"] = {str(destination.relative_to(root)): expected}
                    manifest.write_text(json.dumps(data))
                before = vendor.digest(root)
                with patch.object(vendor, "VENDOR", root), patch.object(vendor, "PACKAGES", packages), \
                        patch.object(vendor, "fetch") as fetch, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(vendor.main([]), 1)
                    fetch.assert_not_called()
                self.assertEqual(vendor.digest(root), before)

    def test_refetch_and_check_preserve_supplemental_notices(self):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for name, content in {"LICENSE": b"Upstream license", "entry.js": b"// New bundle"}.items():
                member = tarfile.TarInfo("package/" + name)
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            notice = write_notice_manifest(root, [self.PACKAGE])
            before = vendor.digest(notice.parent)
            (root / "example").mkdir()
            (root / "example" / "entry.js").write_text("// Old bundle")
            with patch.object(vendor, "VENDOR", root), patch.object(vendor, "PACKAGES", [self.PACKAGE]), \
                    patch.object(vendor, "fetch", return_value=archive.getvalue()), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(vendor.main([]), 0)
                self.assertEqual(vendor.main(["--check"]), 0)
            self.assertEqual((root / "example" / "entry.js").read_bytes(), b"// New bundle")
            self.assertEqual(vendor.digest(notice.parent), before)


class MutationTargetsTest(unittest.TestCase):
    def test_a_source_archive_is_rejected_before_loading_or_mutating_cases(self):
        with patch.object(mutate, "git", return_value=Mock(returncode=128, stdout="")), \
                patch.object(mutate.runpy, "run_path") as load, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mutate.main(["tools/cases/v01.py"]), 2)
            load.assert_not_called()

    def test_every_case_targets_one_current_source_fragment(self):
        for path in sorted((ROOT / "tools/cases").glob("*.py")):
            for source, name, before, _after, _module in runpy.run_path(str(path))["CASES"]:
                with self.subTest(case_file=path.name, mutation=name):
                    self.assertEqual((ROOT / source).read_text().count(before), 1)


if __name__ == "__main__":
    unittest.main()
