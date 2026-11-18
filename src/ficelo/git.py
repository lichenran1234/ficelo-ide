"""Talking to git.

A thin wrapper over the plumbing, not a git library. Every call is a
subprocess with the repository as its working directory, no shell, and a
timeout — a corrupt repository or a hung filter should fail one request, not
wedge the server.

Only what the review view needs: which files changed, what a file looked
like before, and the recent commits.
"""

import hashlib
import os
import re
import subprocess
import time
from collections import deque
from pathlib import Path

from . import files, paths

# Long enough for a big repository's first `git status`, short enough that a
# wedged credential prompt or a hanging filter does not hold a request open.
TIMEOUT = 30

# The bases tried, in order, when nobody says which to compare against.
# origin/HEAD is what the remote calls its default branch, and is right far
# more often than guessing `main`.
BASE_CANDIDATES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")

MAX_COMMITS = 200

# Longer than any reasonable source file and short enough that a stray
# multi-gigabyte blob cannot be pulled into memory by asking for its diff.
MAX_BLOB_BYTES = 8 * 1024 * 1024


class GitFailed(Exception):
    pass


def run(repo, *args, **kwargs):
    """One git command. Returns stdout as text, or raises GitFailed."""
    binary = kwargs.pop("binary", False)
    try:
        result = subprocess.run(
            ["git", "--no-pager"] + list(args),
            cwd=str(repo),
            capture_output=True,
            check=False,
            timeout=TIMEOUT,
            # A terminal prompt from a credential helper would hang forever.
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
                | {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
        )
    except FileNotFoundError:
        raise GitFailed("git is not installed or the repository is unavailable")
    except OSError as error:
        raise GitFailed(str(error))
    except subprocess.TimeoutExpired:
        raise GitFailed("git took too long and was stopped")
    if result.returncode != 0:
        raise GitFailed(result.stderr.decode("utf-8", "replace").strip() or "git failed")
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def top_level(start):
    """The repository containing `start`, or None if there is not one."""
    try:
        start = Path(start)
        out = run(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    except GitFailed:
        return None
    line = out.strip()
    return Path(line) if line else None


def resolve_revision(repo, wanted):
    """Resolve a revision before it can become an option or path argument."""
    if not isinstance(wanted, str) or not wanted or wanted.startswith("-") or len(wanted) > 512 or "\0" in wanted:
        raise GitFailed("invalid revision")
    return run(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", wanted + "^{commit}").strip()


def resolve_base(repo, wanted=None):
    """The revision to compare against, and how it was chosen."""
    candidates = [wanted] if wanted else list(BASE_CANDIDATES)
    for candidate in candidates:
        try:
            sha = resolve_revision(repo, candidate)
        except GitFailed:
            continue
        if sha:
            return candidate, sha
    if wanted:
        raise GitFailed(f"no such revision: {wanted}")
    # A repository with no remote and no main branch is normal enough — a
    # scratch repo with one branch called something else. The first commit is
    # a defensible base, and beats refusing to show anything.
    try:
        root_commit = run(repo, "rev-list", "--max-parents=0", "HEAD").split()
    except GitFailed:
        raise GitFailed("this repository has no commits yet")
    if not root_commit:
        raise GitFailed("this repository has no commits yet")
    return "(first commit)", root_commit[0]


def merge_base(repo, base):
    """Where the current work diverged from the base.

    Comparing against the base branch's tip shows every change made on the
    base since you branched as though you had reverted it. The merge base is
    what "my changes" actually means.
    """
    try:
        return run(repo, "merge-base", "HEAD", base).strip()
    except GitFailed:
        # Unrelated histories, or HEAD is the base. Comparing directly is
        # still more useful than an error.
        return base


def changed_files(repo, base=None):
    """Files differing between the merge base and the working tree.

    Includes untracked files, which are usually the most interesting thing in
    a review and which `git diff` alone never mentions.
    """
    name, base_sha = resolve_base(repo, base)
    against = merge_base(repo, base_sha)

    entries = []
    # -z and --name-status together give NUL-separated status/path pairs,
    # which is the only form that survives a filename with a newline in it.
    # --find-renames is not redundant: rename detection is on by default,
    # but `diff.renames = false` in a user's config turns it off, and a
    # rename then shows up as an unrelated delete and add.
    raw = run(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--find-renames", against, "--")
    fields = raw.split("\0")
    at = 0
    while at < len(fields):
        status = fields[at]
        if not status:
            break
        # A rename status is R<score> and carries two paths.
        if status[0] in ("R", "C"):
            entries.append({"status": status[0], "path": fields[at + 2], "from": fields[at + 1]})
            at += 3
        else:
            entries.append({"status": status[0], "path": fields[at + 1], "from": None})
            at += 2

    untracked = run(repo, "ls-files", "--others", "--exclude-standard", "-z")
    for path in untracked.split("\0"):
        if path:
            entries.append({"status": "?", "path": path, "from": None})

    entries.sort(key=lambda entry: entry["path"])
    return {"base": name, "base_sha": base_sha, "against": against, "files": entries}


def numstat(repo, against):
    """Added and removed line counts per path, from one `git diff` call.

    Counting by reading both blobs would be one subprocess per file; this is
    one for the whole change set. Binary files report "-" for both, which
    becomes None rather than zero — they did change, the count is just not
    meaningful.
    """
    try:
        return _counts(run(repo, "diff", "--no-ext-diff", "--no-textconv", "--numstat", "-z", "--find-renames", against, "--"))
    except GitFailed:
        return {}


def remote_name(repo):
    """owner/repo from the origin URL, for the review header. None if there
    is no origin or it is not in a shape worth guessing at."""
    try:
        url = run(repo, "remote", "get-url", "origin").strip()
    except GitFailed:
        return None
    if not url:
        return None
    cleaned = url.rstrip("/")
    cleaned = cleaned.removesuffix(".git")
    parts = [part for part in cleaned.replace(":", "/").split("/") if part]
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def blob_info(repo, rev, path):
    """Read a bounded blob, distinguishing missing, binary and oversized files."""
    if not rev:
        return {"text": None, "binary": False, "truncated": False, "exists": False}
    spec = f"{rev}:{path}"
    try:
        kind = run(repo, "cat-file", "-t", spec).strip()
        if kind != "blob":
            return {"text": None, "binary": False, "truncated": False, "exists": True, "type": kind}
        size = int(run(repo, "cat-file", "-s", spec).strip())
    except (GitFailed, ValueError):
        return {"text": None, "binary": False, "truncated": False, "exists": False}
    if size > MAX_BLOB_BYTES:
        return {"text": None, "binary": False, "truncated": True, "exists": True}
    raw = run(repo, "cat-file", "blob", spec, binary=True)
    binary = b"\0" in raw[:8192]
    return {"text": None if binary else raw.decode("utf-8-sig", "replace"),
            "binary": binary, "truncated": False, "exists": True}


def blob(repo, rev, path):
    return blob_info(repo, rev, path)["text"]


def _names(raw):
    fields = raw.strip("\n").split("\0")
    result = []
    at = 0
    while at < len(fields):
        status = fields[at].lstrip("\n")
        at += 1
        if not status:
            continue
        size = 2 if status[0] in ("R", "C") else 1
        if at + size > len(fields):
            break
        result.append({"status": status[0], "path": fields[at + size - 1],
                       "from": fields[at] if size == 2 else None})
        at += size
    return result


def _counts(raw):
    fields = raw.strip("\n").split("\0")
    result = {}
    at = 0
    while at < len(fields):
        parts = fields[at].lstrip("\n").split("\t", 2)
        at += 1
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if not path:
            if at + 1 >= len(fields):
                break
            path = fields[at + 1]
            at += 2
        try:
            result[path] = {"added": None if added == "-" else int(added),
                            "removed": None if removed == "-" else int(removed)}
        except ValueError:
            continue
    return result


def commits(repo, limit=50, path=None, rev=None, base=None):
    """History, optionally limited to commits beyond a selected base."""
    limit = max(1, min(MAX_COMMITS, int(limit)))
    fmt = "%H%x00%P%x00%an%x00%aI%x00%s"
    args = ["log", "--no-show-signature", "-z", f"--max-count={limit}", "--format=" + fmt]
    if rev:
        args.append(resolve_revision(repo, rev))
    elif base is not None:
        args.append(resolve_base(repo, base)[1] + "..HEAD")
    if path:
        args += ["--", path]
    fields = run(repo, *args).split("\0")
    result = []
    for at in range(0, len(fields) - 4, 5):
        sha, parents, author, when, subject = fields[at:at + 5]
        sha = sha.strip("\n")
        if not sha:
            continue
        parent_list = parents.split()
        result.append({"sha": sha, "short": sha[:7], "parents": parent_list,
                       "merge": len(parent_list) > 1, "author": author, "date": when,
                       "subject": subject})
    return result


def commit_files(repo, sha):
    sha = resolve_revision(repo, sha)
    raw = run(repo, "show", "--no-show-signature", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--format=", "--find-renames", sha, "--")
    counts = _counts(run(repo, "show", "--no-show-signature", "--no-ext-diff", "--no-textconv", "--numstat", "-z", "--format=", "--find-renames", sha, "--"))
    return [dict(entry, **counts.get(entry["path"], {})) for entry in _names(raw)]


def branch_commits(repo, base=None, limit=MAX_COMMITS):
    """Metadata and file lists in three processes, independent of commit count."""
    label, base_sha = resolve_base(repo, base)
    history = commits(repo, limit=limit, base=label if base else base_sha)
    if not history:
        return []
    # The marker is followed by an object id; a filename cannot contain NUL
    # and the field following a filename is a status, never an object id.
    marker = "\0ficelo-commit\0"
    common = ["log", "--no-show-signature", "--root", "--no-ext-diff", "--no-textconv", "--find-renames", "-z",
              f"--max-count={max(1, min(MAX_COMMITS, int(limit)))}", "--format=%x00ficelo-commit%x00%H%x00"]
    def by_commit(flag):
        raw = run(repo, *common, flag, base_sha + "..HEAD", "--")
        pieces = re.split(re.escape(marker) + r"([0-9a-f]{40,64})\x00", raw)
        return {pieces[n]: pieces[n + 1] for n in range(1, len(pieces) - 1, 2)}
    names, numbers = by_commit("--name-status"), by_commit("--numstat")
    for commit in history:
        counts = _counts(numbers.get(commit["sha"], ""))
        commit["files"] = [] if commit["merge"] else [dict(entry, **counts.get(entry["path"], {})) for entry in _names(names.get(commit["sha"], ""))]
        commit["added"] = sum(entry.get("added") or 0 for entry in commit["files"])
        commit["removed"] = sum(entry.get("removed") or 0 for entry in commit["files"])
    return history


def describe(repo):
    """Branch, head and dirtiness, for the review panel's header."""
    try:
        branch = run(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except GitFailed:
        branch = None
    try:
        head = run(repo, "rev-parse", "--short", "HEAD").strip()
    except GitFailed:
        head = None
    return {"branch": branch, "head": head}


def within(root, repo, path):
    """Retain the tracked spelling while enforcing the served-root boundary."""
    root = Path(root).resolve()
    absolute = Path(repo).resolve() / path
    try:
        relative = absolute.relative_to(root)
        requested = "/" + relative.as_posix() if str(relative) != "." else "/"
        paths.resolve(requested, root)
        return requested
    except (paths.OutsideRoot, ValueError, OSError):
        return None


def vcs_kind(repo):
    repo = Path(repo)
    if (repo / ".git").exists():
        return "git"
    if (repo / ".sl").exists() or (repo / ".hg").exists():
        return "sapling"
    return None


def enclosing(start):
    """Find the nearest checkout without running one command per ancestor."""
    start = Path(start)
    initial = start if start.is_dir() else start.parent
    for current in (initial, *initial.parents):
        if vcs_kind(current):
            return current
    return None


def repository(root, start="/"):
    target = paths.resolve(start or "/", root)
    repo = enclosing(target)
    if repo is None:
        raise GitFailed("this folder is not in a supported repository")
    return repo


def _anchor(root, repo):
    return within(root, repo, "") or "/"


def _git_directory(repo):
    pointer = Path(repo) / ".git"
    try:
        if pointer.is_dir():
            return pointer
        text = pointer.read_text(encoding="utf-8", errors="replace")[:4096]
        if text.startswith("gitdir:"):
            return (Path(repo) / text[7:].strip()).resolve()
    except OSError:
        pass
    return None


def _discovery_entry(root, repo):
    request = within(root, repo, "")
    if request is None:
        return None
    kind = vcs_kind(repo)
    branch, changed, name = "", 0, repo.name
    candidates = [repo]
    directory = _git_directory(repo) if kind == "git" else None
    if directory:
        candidates += [directory / "HEAD", directory / "index", directory / "logs" / "HEAD"]
        try:
            head = (directory / "HEAD").read_text(encoding="utf-8").strip()
            branch = head.removeprefix("ref: refs/heads/") if head.startswith("ref:") else head[:7]
            common = (directory / "commondir").read_text(encoding="utf-8").strip()
            name = (directory / common).resolve().parent.name
        except OSError:
            pass
    elif kind == "sapling":
        candidates += [repo / ".sl" / "dirstate", repo / ".hg" / "dirstate"]
    for candidate in candidates:
        try:
            changed = max(changed, candidate.stat().st_mtime)
        except OSError:
            pass
    return {"path": request, "name": name, "folder": repo.name, "branch": branch,
            "modified": changed, "vcs": kind}


def worktrees(root, repo):
    if vcs_kind(repo) != "git":
        entry = _discovery_entry(root, repo)
        return [entry] if entry else []
    try:
        raw = run(repo, "worktree", "list", "--porcelain", "-z")
    except GitFailed:
        entry = _discovery_entry(root, repo)
        return [entry] if entry else []
    result = []
    for field in raw.split("\0"):
        if not field.startswith("worktree "):
            continue
        candidate = Path(field[9:])
        if candidate.is_dir():
            entry = _discovery_entry(root, candidate)
            if entry:
                result.append(entry)
    return result


_DISCOVERY_SKIP = frozenset({".git", ".hg", ".sl", ".venv", "venv", "node_modules", "__pycache__", ".cache", "vendor"})
_VIRTUAL_ROOTS = frozenset({Path("/proc"), Path("/sys"), Path("/dev")})


def discover(root, scopes=(), query="", limit=40, max_dirs=800, seconds=0.25):
    """Look near selected/recent folders, never recursively scan the machine.

    Direct paths and the enclosing checkout are always checked. The remaining
    bounded breadth-first scan skips symlinks and generated dependency trees.
    """
    root = Path(root).resolve()
    seeds = []
    for scope in list(scopes)[:30]:
        try:
            candidate = paths.resolve(scope, root)
            seeds.append(candidate if candidate.is_dir() else candidate.parent)
        except (paths.OutsideRoot, OSError):
            continue
    if not seeds or seeds == [Path("/")]:
        home = Path.home().resolve()
        seeds = [home if root == Path("/") else root]
    if query.startswith(("/", "~")):
        try:
            requested = paths.to_request(Path(query).expanduser(), root) if query.startswith("~") else query
            direct = paths.resolve(requested, root)
            seeds.insert(0, direct if direct.is_dir() else direct.parent)
        except (paths.OutsideRoot, ValueError, OSError):
            pass
    queue = deque((seed, 0) for seed in seeds if seed not in _VIRTUAL_ROOTS and seed != Path("/"))
    found, visited = {}, set()
    for seed in seeds:
        try:
            repo = enclosing(seed)
            if repo:
                entry = _discovery_entry(root, repo)
                if entry:
                    found[entry["path"]] = entry
        except OSError:
            continue
    deadline = time.monotonic() + max(0.01, seconds)
    examined = 0
    while queue and examined < max_dirs and time.monotonic() < deadline:
        folder, depth = queue.popleft()
        if folder in visited:
            continue
        visited.add(folder)
        examined += 1
        try:
            # A disconnected mount can fail even a marker-file stat. Keep
            # discovery useful when one nearby folder becomes inaccessible.
            if vcs_kind(folder):
                entry = _discovery_entry(root, folder)
                if entry:
                    found[entry["path"]] = entry
                continue
            if depth >= 5:
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline or len(queue) >= max_dirs:
                        break
                    if entry.name in _DISCOVERY_SKIP or entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    candidate = Path(entry.path)
                    if candidate not in _VIRTUAL_ROOTS:
                        queue.append((candidate, depth + 1))
        except OSError:
            continue
    terms = query.lower().split() if not query.startswith(("/", "~")) else []
    matches = [entry for entry in found.values() if all(term in (entry["path"] + " " + entry["name"] + " " + entry["branch"]).lower() for term in terms)]
    matches.sort(key=lambda entry: (-entry["modified"], entry["path"]))
    return {"repositories": matches[:max(1, min(100, limit))], "partial": bool(queue), "examined": examined}


def branches(repo):
    raw = run(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
    return [name for name in raw.splitlines() if not name.endswith("/HEAD")][:500]


def _shown(root, repo, entries):
    result = []
    for entry in entries:
        requested = within(root, repo, entry["path"])
        if requested is not None:
            result.append(dict(entry, request=requested, repo=_anchor(root, repo)))
    return result


def review_status(root, start="/", base=None):
    try:
        repo = repository(root, start)
    except GitFailed:
        return {"repo": False, "files": [], "hidden": 0, "root": start}
    if vcs_kind(repo) == "sapling":
        from . import sapling
        return sapling.review_status(root, repo, base)
    found = changed_files(repo, base)
    counts = numstat(repo, found["against"])
    entries = [dict(entry, **counts.get(entry["path"], {})) for entry in found["files"]]
    for entry in entries:
        if entry["status"] == "?":
            try:
                text, truncated, binary = files.read_text(paths.resolve(within(root, repo, entry["path"]), root))
                entry.update(added=None if binary or truncated else len(text.splitlines()), removed=0)
            except (OSError, paths.OutsideRoot, TypeError):
                entry.update(added=None, removed=None)
    shown = _shown(root, repo, entries)
    identity = describe(repo)
    stamp_text = repr((identity, found["against"], found["base_sha"], [(entry["path"], entry["status"], _working_stamp(root, repo, entry["path"])) for entry in entries]))
    return dict(identity, repo=True, root=_anchor(root, repo), name=repo.name, vcs="git",
                base=found["base"], base_sha=found["base_sha"], against=found["against"],
                files=shown, hidden=len(entries) - len(shown), remote=remote_name(repo),
                added=sum(entry.get("added") or 0 for entry in shown),
                removed=sum(entry.get("removed") or 0 for entry in shown),
                branches=branches(repo), worktrees=worktrees(root, repo),
                version=hashlib.sha256(stamp_text.encode()).hexdigest())


def _working_stamp(root, repo, relative):
    request = within(root, repo, relative)
    if request is None:
        return None
    try:
        return files.stamp(paths.resolve(request, root))
    except OSError:
        return None


def review_diff(root, start, requested_path, base=None, rev=None):
    repo = repository(root, start)
    if vcs_kind(repo) == "sapling":
        from . import sapling
        return sapling.review_diff(root, repo, requested_path, base, rev)
    target = paths.resolve(requested_path, root)
    try:
        relative = str((Path(root).resolve() / paths.normalise(requested_path).lstrip("/")).relative_to(repo.resolve()))
    except ValueError:
        raise GitFailed("that file is not in the selected repository")
    if rev:
        sha = resolve_revision(repo, rev)
        detail = commits(repo, limit=1, rev=sha)[0]
        if detail["merge"]:
            raise GitFailed("A merge has multiple parents; inspect the commits it merged")
        entries = commit_files(repo, sha)
        before = detail["parents"][0] if detail["parents"] else None
        base_name = base
    else:
        status = changed_files(repo, base)
        entries = status["files"]
        before, base_name, sha = status["against"], status["base"], None
    entry = next((entry for entry in entries if entry["path"] == relative), {})
    old_path = entry.get("from") or relative
    old = blob_info(repo, before, old_path) if within(root, repo, old_path) is not None else {"text": None, "binary": False, "truncated": False}
    if rev:
        new = blob_info(repo, sha, relative)
        stamp = None
    else:
        stamp = files.stamp(target)
        if stamp is None or not target.is_file():
            new = {"text": None, "binary": False, "truncated": False, "type": "directory" if target.is_dir() else None}
            stamp = None
        else:
            text, truncated, binary = files.read_text(target)
            new = {"text": None if binary else text, "binary": binary, "truncated": truncated}
    unsupported = bool(old.get("type") or new.get("type"))
    binary = old["binary"] or new["binary"] or unsupported
    truncated = old["truncated"] or new["truncated"]
    return {"path": requested_path, "relative": relative, "repo": _anchor(root, repo), "base": base_name,
            "rev": sha, "old": old["text"], "new": new["text"], "binary": binary,
            "truncated": truncated, "stamp": stamp, "status": entry.get("status"),
            "notice": "Directory or submodule entry changed; its contents are reviewed in its own repository." if unsupported else None,
            "from": entry.get("from"), "editable": bool(not rev and stamp is not None and not binary and not truncated)}


def review_commits(root, start="/", base=None, limit=MAX_COMMITS):
    repo = repository(root, start)
    if vcs_kind(repo) == "sapling":
        from . import sapling
        return sapling.review_commits(root, repo, base, limit)
    result = branch_commits(repo, base, limit)
    for commit in result:
        commit["files"] = _shown(root, repo, commit["files"])
        commit["added"] = sum(entry.get("added") or 0 for entry in commit["files"])
        commit["removed"] = sum(entry.get("removed") or 0 for entry in commit["files"])
    return {"commits": result, "repo": _anchor(root, repo)}


def review_commit(root, start, sha, full=False):
    repo = repository(root, start)
    if vcs_kind(repo) == "sapling":
        from . import sapling
        return sapling.review_commit(root, repo, sha, full)
    sha = resolve_revision(repo, sha)
    detail = commits(repo, limit=1, rev=sha)[0]
    entries = [] if detail["merge"] else _shown(root, repo, commit_files(repo, sha))
    if full:
        before = detail["parents"][0] if detail["parents"] else None
        total = 0
        for entry in entries:
            if total > 32 * 1024 * 1024:
                entry.update(old=None, new=None, binary=False, truncated=True, editable=False)
                continue
            previous = entry.get("from") or entry["path"]
            old = blob_info(repo, before, previous) if within(root, repo, previous) is not None else {"text": None, "binary": False, "truncated": False}
            new = blob_info(repo, sha, entry["path"])
            total += len(old["text"] or "") + len(new["text"] or "")
            limited = total > 32 * 1024 * 1024
            entry.update(old=None if limited else old["text"], new=None if limited else new["text"],
                         binary=old["binary"] or new["binary"] or bool(old.get("type") or new.get("type")),
                         notice="Submodule entry changed; its contents are reviewed in its own repository." if old.get("type") or new.get("type") else None,
                         truncated=limited or old["truncated"] or new["truncated"], editable=False)
    return {"sha": sha, "repo": _anchor(root, repo), "commit": detail, "files": entries,
            "merge": detail["merge"], "wholeCommit": bool(full)}
