"""Optional review adapter for the public Sapling command line client.

The client is discovered on PATH only when a Sapling checkout is selected.
No extensions, hosting service, or remote connection are required.
"""

import ast
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import files, git, paths

NULL = "0" * 40
_TEMPLATE = "{dict(node, author, desc, p1node, p2node, date, file_adds, file_dels, file_mods, file_copies, diffstat)|json}\\n"


def run(repo, *args, binary=False, limit=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["HGPLAIN"] = "1"
    try:
        # Blob output goes to a temporary file so a large committed file never
        # has to fit in the server's memory. The same timeout covers writing it.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ["sl", "--noninteractive", "--cwd", str(repo), *args],
                stdout=output, stderr=subprocess.PIPE, env=env, timeout=git.TIMEOUT, check=False,
            )
            if result.returncode:
                raise git.GitFailed(result.stderr.decode("utf-8", "replace").strip() or "Sapling failed")
            output.seek(0)
            data = output.read(limit + 1 if limit is not None else -1)
    except FileNotFoundError:
        raise git.GitFailed("Install the public Sapling client to review this repository")
    except subprocess.TimeoutExpired:
        raise git.GitFailed("Sapling took too long and was stopped")
    except OSError as error:
        raise git.GitFailed(str(error))
    return data if binary else data.decode("utf-8", "replace")


def resolve(repo, revision):
    if not revision or revision.startswith("-") or len(revision) > 512 or "\0" in revision:
        raise git.GitFailed("invalid revision")
    value = run(repo, "log", "-r", revision, "-T", "{node}\\n").splitlines()
    if len(value) != 1:
        raise git.GitFailed("Select a base that identifies exactly one commit")
    return value[0]


def base_revision(repo, wanted=None):
    if wanted and wanted != "stack":
        return wanted, resolve(repo, wanted)
    value = run(repo, "log", "-r", "max(public() & ::.)", "-T", "{node}").strip()
    return "stack", value or NULL


def metadata(repo, revision, limit=git.MAX_COMMITS):
    raw = run(repo, "log", "-r", revision, "-l", str(max(1, min(git.MAX_COMMITS, int(limit)))), "-T", _TEMPLATE)
    result = []
    for line in raw.splitlines():
        entry = json.loads(line)
        sha = entry["node"]
        parents = [entry[key] for key in ("p1node", "p2node") if entry[key] != NULL]
        adds, deletes, modifies = entry.get("file_adds", []), entry.get("file_dels", []), entry.get("file_mods", [])
        copies = entry.get("file_copies", {})
        renamed = {old for old in copies.values() if old in deletes}
        changed_files = [{"path": path, "from": copies.get(path),
                          "status": "R" if copies.get(path) in renamed else "C" if path in copies else status}
                         for status, names in (("A", adds), ("D", deletes), ("M", modifies))
                         for path in names if not (status == "D" and path in renamed)]
        result.append({"sha": sha, "short": sha[:7], "parents": parents,
                       "merge": len(parents) > 1, "author": entry["author"],
                       "date": datetime.fromtimestamp(entry["date"][0], timezone.utc).isoformat(),
                       "subject": entry["desc"].split("\n", 1)[0], "files": [] if len(parents) > 1 else changed_files})
    return result


def changed(repo, before, after=None):
    args = ["status", "--copies", "-0", "--rev", before]
    if after:
        args += ["--rev", after]
    raw = run(repo, *args)
    result = []
    for field in raw.split("\0"):
        if not field:
            continue
        if field.startswith("  ") and result:
            result[-1]["from"] = field[2:]
            continue
        status = {"!": "D", "R": "D"}.get(field[0], field[0])
        if status in {"M", "A", "D", "?"}:
            result.append({"status": status, "path": field[2:], "from": None})
    deleted = {entry["path"] for entry in result if entry["status"] == "D"}
    renamed = set()
    for entry in result:
        if entry["from"]:
            entry["status"] = "R" if entry["from"] in deleted else "C"
            if entry["status"] == "R":
                renamed.add(entry["from"])
    return [entry for entry in result if not (entry["status"] == "D" and entry["path"] in renamed)]


def _patch_path(value):
    if value.startswith('"'):
        try:
            value = ast.literal_eval(value)
            try:
                value = value.encode("latin-1").decode("utf-8")
            except UnicodeError:
                pass
        except (ValueError, SyntaxError):
            return None
    return value[2:] if value.startswith(("a/", "b/")) else None


def counts(repo, before, after=None):
    args = ["diff", "--git", "--nodates", "--rev", before]
    if after:
        args += ["--rev", after]
    raw = run(repo, *args, limit=git.MAX_BLOB_BYTES)
    if len(raw.encode("utf-8")) > git.MAX_BLOB_BYTES:
        return {}
    return _patch_counts(raw)


def _patch_counts(raw):
    result, current, old, in_hunk = {}, None, None, False
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            current, old, in_hunk = None, None, False
        elif line.startswith("@@ "):
            in_hunk = True
        elif not in_hunk and line.startswith("--- "):
            old = _patch_path(line[4:])
        elif not in_hunk and line.startswith("+++ "):
            current = _patch_path(line[4:]) or old
            if current:
                result[current] = {"added": 0, "removed": 0}
        elif current and line.startswith(("+", "-")):
            result[current]["added" if line[0] == "+" else "removed"] += 1
    return result


def blob(repo, revision, relative):
    if not revision or revision == NULL:
        return {"text": None, "binary": False, "truncated": False}
    try:
        raw = run(repo, "cat", "-r", revision, "--", relative, binary=True, limit=git.MAX_BLOB_BYTES)
    except git.GitFailed as error:
        if "no such file" in str(error) or "not found in manifest" in str(error):
            return {"text": None, "binary": False, "truncated": False}
        raise
    binary = b"\0" in raw[:8192]
    truncated = len(raw) > git.MAX_BLOB_BYTES
    return {"text": None if binary or truncated else raw.decode("utf-8-sig", "replace"),
            "binary": binary, "truncated": truncated}


def review_status(root, repo, base=None):
    label, before = base_revision(repo, base)
    entries, numbers = changed(repo, before), counts(repo, before)
    for entry in entries:
        entry.update(numbers.get(entry["path"], {"added": None, "removed": None}))
        if entry["status"] == "?":
            request = git.within(root, repo, entry["path"])
            if request:
                try:
                    text, truncated, binary = files.read_text(paths.resolve(request, root))
                    entry.update(added=None if truncated or binary else len(text.splitlines()), removed=0)
                except OSError:
                    pass
    shown = git._shown(root, repo, entries)
    identity = metadata(repo, ".", 1)
    head = identity[0]["short"] if identity else None
    branch = run(repo, "log", "-r", ".", "-T", "{activebookmark}").strip() or head
    marks = run(repo, "bookmarks", "-T", "{bookmark}\\n").splitlines()
    version = repr((head, before, [(entry, git._working_stamp(root, repo, entry["path"])) for entry in shown]))
    return {"repo": True, "root": git._anchor(root, repo), "name": repo.name, "vcs": "sapling",
            "branch": branch, "head": head, "base": label, "base_sha": before, "against": before,
            "files": shown, "hidden": len(entries) - len(shown), "remote": None,
            "added": sum(entry.get("added") or 0 for entry in shown),
            "removed": sum(entry.get("removed") or 0 for entry in shown),
            "branches": ["stack", *marks], "worktrees": git.worktrees(root, repo),
            "version": hashlib.sha256(version.encode()).hexdigest()}


def review_commits(root, repo, base=None, limit=git.MAX_COMMITS):
    _, before = base_revision(repo, base)
    revision = f"reverse(::.) - ::{before}"
    result = metadata(repo, revision, limit)
    if result:
        # One patch stream supplies all per-file statistics. A fixed process
        # count keeps opening a long stack fast; oversized streams simply
        # omit counts for the remaining commits.
        maximum = 2 * git.MAX_BLOB_BYTES
        raw = run(repo, "log", "-r", revision, "-l", str(max(1, min(git.MAX_COMMITS, int(limit)))),
                  "--patch", "--git", "-T", "\\0{node}\\0", limit=maximum)
        pieces = raw.split("\0")
        numbers = {pieces[index]: _patch_counts(pieces[index + 1])
                   for index in range(1, len(pieces) - 1, 2)}
        if len(raw.encode("utf-8")) > maximum and len(pieces) > 2:
            numbers.pop(pieces[-2], None)
    else:
        numbers = {}
    for commit in result:
        count = numbers.get(commit["sha"], {})
        commit["files"] = git._shown(root, repo, [dict(entry, **count.get(entry["path"], {})) for entry in commit["files"]])
        commit["added"] = sum(entry.get("added") or 0 for entry in commit["files"])
        commit["removed"] = sum(entry.get("removed") or 0 for entry in commit["files"])
    return {"commits": result, "repo": git._anchor(root, repo)}


def review_diff(root, repo, requested, base=None, rev=None):
    target = paths.resolve(requested, root)
    try:
        relative = str((Path(root).resolve() / paths.normalise(requested).lstrip("/")).relative_to(repo.resolve()))
    except ValueError:
        raise git.GitFailed("that file is not in the selected repository")
    if rev:
        sha = resolve(repo, rev)
        commit = metadata(repo, sha, 1)[0]
        if commit["merge"]:
            raise git.GitFailed("A merge has multiple parents; inspect the commits it merged")
        before = commit["parents"][0] if commit["parents"] else NULL
        label = base
    else:
        label, before = base_revision(repo, base)
        sha = None
    entries = changed(repo, before, sha)
    entry = next((entry for entry in entries if entry["path"] == relative), {})
    previous = entry.get("from") or relative
    old = blob(repo, before, previous) if git.within(root, repo, previous) else {"text": None, "binary": False, "truncated": False}
    stamp = None if rev else files.stamp(target)
    if rev:
        new = blob(repo, sha, relative)
    elif stamp:
        text, truncated, binary = files.read_text(target)
        new = {"text": None if binary else text, "binary": binary, "truncated": truncated}
    else:
        new = {"text": None, "binary": False, "truncated": False}
    binary, truncated = old["binary"] or new["binary"], old["truncated"] or new["truncated"]
    return {"path": requested, "relative": relative, "repo": git._anchor(root, repo), "base": label, "rev": sha,
            "old": old["text"], "new": new["text"], "stamp": stamp, "status": entry.get("status"),
            "from": entry.get("from"), "binary": binary, "truncated": truncated,
            "editable": bool(not rev and stamp and not binary and not truncated)}


def review_commit(root, repo, sha, full=False):
    sha = resolve(repo, sha)
    commit = metadata(repo, sha, 1)[0]
    parent = commit["parents"][0] if commit["parents"] else NULL
    entries = [] if commit["merge"] else git._shown(root, repo, changed(repo, parent, sha))
    numbers = counts(repo, parent, sha) if entries else {}
    total = 0
    for entry in entries:
        entry.update(numbers.get(entry["path"], {}))
        if not full:
            continue
        if total > 32 * 1024 * 1024:
            entry.update(old=None, new=None, binary=False, truncated=True, editable=False)
            continue
        previous = entry.get("from") or entry["path"]
        old = blob(repo, parent, previous) if git.within(root, repo, previous) else {"text": None, "binary": False, "truncated": False}
        new = blob(repo, sha, entry["path"])
        total += len(old["text"] or "") + len(new["text"] or "")
        limited = total > 32 * 1024 * 1024
        entry.update(old=None if limited else old["text"], new=None if limited else new["text"],
                     binary=old["binary"] or new["binary"], truncated=limited or old["truncated"] or new["truncated"], editable=False)
    return {"sha": sha, "repo": git._anchor(root, repo), "commit": commit, "files": entries,
            "merge": commit["merge"], "wholeCommit": bool(full)}
