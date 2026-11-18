"""Finding a file by typing part of its name.

Filename search, not content search: it is the one people reach for dozens of
times an hour, and it can be answered without reading a single file. Content
search is a different feature with a different cost and is not this.
"""

import collections
import os
import threading
import time
from pathlib import Path

from . import paths
from .config import SKIP_NAMES

# A ceiling on how much tree one query may look at. A home directory with a
# few large checkouts in it runs to millions of entries, and a search box that
# hangs the server on every keystroke is worse than one that occasionally
# misses something deep. The response says when this was hit.
MAX_ENTRIES = 40000

# Beyond this a path is almost certainly generated rather than written, and
# scoring it is wasted work.
MAX_PATH_LENGTH = 400

# How long a walk may be reused. Search runs on every keystroke, and walking
# a large checkout takes seconds — measured at 4.4s for a real one — so
# without this the box is unusable on exactly the trees where it matters
# most. Short enough that a file created a moment ago is findable by the
# time anyone types its name.
CACHE_SECONDS = 15

_cache: dict[tuple[str, str, bool], tuple[float, list[str], bool]] = {}
_cache_lock = threading.Lock()


def clear_cache():
    with _cache_lock:
        _cache.clear()
    INDEXES.clear()


def as_request(absolute, root_str):
    """Express an already contained scan result without resolving every file."""
    root_str = str(root_str).rstrip("/")
    absolute = str(absolute)
    if absolute == root_str or (not root_str and absolute == "/"):
        return "/"
    if not absolute.startswith(root_str + os.sep):
        return None
    return "/" + absolute[len(root_str) + 1:].lstrip("/")


def _walk_cached(scope, root, show_hidden, now):
    """Request paths for everything under `scope`, walked at most once per
    CACHE_SECONDS. Returns (paths, complete)."""
    scope_str = str(scope).rstrip("/")
    root_str = str(root).rstrip("/")
    key = (scope_str, root_str, bool(show_hidden))
    with _cache_lock:
        found = _cache.get(key)
        if found and now - found[0] < CACHE_SECONDS:
            return found[1], found[2]
    # Deliberately outside the lock: two requests arriving together will
    # both walk, which wastes work once but never blocks a reader behind a
    # multi-second scan holding a mutex.
    entries, complete = walk(scope, show_hidden=show_hidden)
    requests = []
    for absolute in entries:
        request = as_request(absolute, root_str)
        if request is not None and len(request) <= MAX_PATH_LENGTH:
            requests.append(request)
    with _cache_lock:
        _cache[key] = (now, requests, complete)
    return requests, complete


# These are kernel/process interfaces, not directory trees to index. The
# absolute path check does not hide an ordinary project folder named "proc".
VIRTUAL_ROOTS = frozenset({"/proc", "/sys", "/dev", "/run"})
SCAN_SECONDS = 3.0
BACKGROUND_SECONDS = 8.0
MAX_INDEXES = 16
MAX_BUILDERS = 2


def excluded_root(directory):
    text = os.path.normpath(str(directory))
    return any(text == base or text.startswith(base + os.sep) for base in VIRTUAL_ROOTS)


def scan(scope, root, show_hidden=False, limit=MAX_ENTRIES, seconds=SCAN_SECONDS,
         cancel=None, publish=None, max_depth=None):
    """Read path metadata breadth-first, bounded between filesystem calls.

    Count every directory entry, including skipped names. Symlinks are listed
    but never traversed, so loops and aliases cannot consume the walk budget.
    No file contents are opened. ``publish`` receives immutable snapshots.
    """
    root = Path(root).resolve()
    scope = Path(scope).resolve()
    if scope != root and root not in scope.parents:
        raise paths.OutsideRoot(str(scope))
    found = []
    queue = collections.deque([(str(scope), 0)])
    seen = 0
    complete = True
    deadline = time.monotonic() + max(0.0, seconds)
    last_publish = time.monotonic()
    while queue:
        current, depth = queue.popleft()
        if excluded_root(current):
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if seen >= limit or time.monotonic() >= deadline or (cancel and cancel.is_set()):
                        return tuple(found), False, seen
                    seen += 1
                    if not show_hidden and entry.name.startswith("."):
                        continue
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        symlink = entry.is_symlink()
                        if symlink:
                            target = Path(os.path.realpath(entry.path))
                            if target != root and root not in target.parents:
                                continue
                            is_dir = entry.is_dir()
                    except OSError:
                        complete = False
                        continue
                    request = as_request(entry.path, str(root))
                    if request is None or len(request) > MAX_PATH_LENGTH or excluded_root(entry.path):
                        continue
                    found.append((request, is_dir))
                    if is_dir and not symlink and entry.name not in SKIP_NAMES:
                        if max_depth is None or depth < max_depth:
                            queue.append((entry.path, depth + 1))
                        else:
                            complete = False
                    if publish and len(found) % 256 == 0 and time.monotonic() - last_publish >= 0.1:
                        publish(tuple(found), seen)
                        last_publish = time.monotonic()
        except OSError:
            complete = False
    return tuple(found), complete, seen


def walk(root, show_hidden=False, limit=MAX_ENTRIES):
    """Legacy synchronous file iterator, with the same bounded scanner."""
    root = Path(root)
    found, complete, _ = scan(root, root, show_hidden, limit=limit)
    prefix = str(root).rstrip("/")
    return [prefix + request for request, is_dir in found if not is_dir], complete


class _Snapshot:
    def __init__(self):
        self.entries = ()
        self.complete = False
        self.scanned = 0
        self.updated = 0.0
        self.duration = 0.0
        self.building = False
        self.cancel = threading.Event()


class BackgroundIndex:
    """A small cache with bounded worker concurrency and stale-while-refresh reads."""

    def __init__(self, scanner=scan):
        self.scanner = scanner
        self.cache = collections.OrderedDict()
        self.waiting = collections.OrderedDict()
        self.lock = threading.Lock()
        self.running = 0

    def clear(self):
        with self.lock:
            for entry in self.cache.values():
                entry.cancel.set()
            self.cache.clear()
            self.waiting.clear()

    def get(self, scope, root, show_hidden=False):
        key = (str(scope), str(root), bool(show_hidden))
        with self.lock:
            record = self.cache.get(key)
            if record is None:
                while len(self.cache) >= MAX_INDEXES:
                    old_key, old = self.cache.popitem(last=False)
                    old.cancel.set()
                    self.waiting.pop(old_key, None)
                record = _Snapshot()
                self.cache[key] = record
            self.cache.move_to_end(key)
            interval = max(CACHE_SECONDS, record.duration * 5)
            if not record.building and (not record.updated or time.monotonic() - record.updated >= interval):
                record.building = True
                self.waiting[key] = record
                self._start_locked()
            return {"entries": record.entries, "complete": record.complete,
                    "scanned": record.scanned, "indexing": record.building}

    def _start_locked(self):
        while self.waiting and self.running < MAX_BUILDERS:
            key, record = self.waiting.popitem(last=False)
            self.running += 1
            threading.Thread(target=self._build, args=(key, record), daemon=True).start()

    def _build(self, key, record):
        started = time.monotonic()
        fresh = not record.updated

        def publish(entries, scanned):
            with self.lock:
                if fresh and self.cache.get(key) is record:
                    record.entries, record.scanned = entries, scanned
        try:
            entries, complete, scanned = self.scanner(
                key[0], key[1], key[2], seconds=BACKGROUND_SECONDS,
                cancel=record.cancel, publish=publish)
            with self.lock:
                if self.cache.get(key) is record:
                    record.entries, record.complete, record.scanned = entries, complete, scanned
                    record.duration = time.monotonic() - started
                    record.updated = time.monotonic()
        except (OSError, paths.OutsideRoot):
            with self.lock:
                record.complete = False
                record.updated = time.monotonic()
        finally:
            with self.lock:
                record.building = False
                self.running -= 1
                self._start_locked()


INDEXES = BackgroundIndex()


def fuzzy(text, query):
    """Score `text` against `query`, or None if the letters are not all there.

    A subsequence match, the same shape every editor's file finder uses: the
    query's characters must appear in order but need not be adjacent, so
    `apjs` finds `app/index.js`. Higher is better.
    """
    if not query:
        return 0
    haystack = text.lower()
    needle = query.lower()
    score = 0
    at = 0
    previous = -2
    for character in needle:
        at = haystack.find(character, at)
        if at < 0:
            return None
        # Adjacent characters are the strongest signal that this is the word
        # being typed, rather than letters scattered through a long path.
        if at == previous + 1:
            score += 8
        # A match at a word boundary is nearly as good: `mp` should find
        # `main.py` ahead of `component.py`.
        elif at == 0 or haystack[at - 1] in "/-_. ":
            score += 6
        else:
            score += 1
        previous = at
        at += 1
    return score


# Four tiers, in the order a person means them. A contiguous run of the
# typed characters is a different kind of evidence from the same characters
# scattered across a word, and ranking them together is how a search for
# "ficelo" answers with file_icon_selector_old.py — f, i, c, e, l, o, all
# present, all in order, and entirely coincidental.
TIER_NAME_SUBSTRING = 4000
TIER_PATH_SUBSTRING = 3000
TIER_NAME_FUZZY = 2000
TIER_PATH_FUZZY = 1000


def rank(candidate, query):
    """Score a whole request path. Higher is better, None means no match."""
    lower = candidate.lower()
    name = candidate.rsplit("/", 1)[-1]
    lower_name = name.lower()
    needle = query.lower()

    # Shorter names win ties, and shallower paths win after that: `test.py`
    # over `test_of_the_thing_that_does_the_other_thing.py`, and the copy
    # near the top over the one six folders down.
    tidiness = -min(len(name), 60) / 4.0 - candidate.count("/")

    if needle in lower_name:
        # Earlier in the name is better: `ficelo.py` over `my_ficelo.py`.
        return TIER_NAME_SUBSTRING - lower_name.index(needle) + tidiness
    if needle in lower:
        return TIER_PATH_SUBSTRING - lower.index(needle) / 10.0 + tidiness
    on_name = fuzzy(name, query)
    if on_name is not None:
        return TIER_NAME_FUZZY + on_name + tidiness
    on_path = fuzzy(candidate, query)
    if on_path is None:
        return None
    return TIER_PATH_FUZZY + on_path + tidiness


def search(scope, query, limit=50, show_hidden=False, now=None, root=None):
    """The best `limit` matches for `query` under `scope`.

    `root` is the served folder, used only to express results as request
    paths — the walk itself covers `scope`, which may be a subtree of it.
    """
    query = (query or "").strip()
    if not query:
        return {"matches": [], "complete": True}
    found, complete = _walk_cached(
        scope, root if root is not None else scope, show_hidden,
        now if now is not None else time.monotonic()
    )
    scored = []
    for request in found:
        points = rank(request, query)
        if points is not None:
            scored.append((points, request))
    # Sorted by score, then by path so equal scores come out in a stable order
    # rather than in whatever order the filesystem handed them over.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return {
        "matches": [{"path": request, "score": points} for points, request in scored[:limit]],
        "complete": complete,
    }


def query_scope(scope, query, root, home=None):
    """Return the existing directory and fragment named by a typed path.

    Absolute names are served-root-relative, with the on-disk root accepted
    for pasted paths. ``~`` always means the server account's actual home.
    """
    root, scope = Path(root).resolve(), Path(scope).resolve()
    text = (query or "").strip()
    if text == "~" or text.startswith("~/"):
        account_home = Path(home or Path.home()).resolve()
        if account_home != root and root not in account_home.parents:
            return scope, text, False
        text = (as_request(str(account_home), str(root)) or "/").rstrip("/") + text[1:]
        if query == "~":
            text += "/"
    if not text.startswith("/"):
        return scope, text, False
    root_text = str(root).rstrip("/")
    if root_text and (text == root_text or text.startswith(root_text + "/")):
        text = text[len(root_text):] or "/"
    normalized = paths.normalise(text)
    if text.endswith("/."):
        # A final dot is the beginning of a hidden name, not a completed
        # directory component to normalize away while the user is typing.
        fragments, candidate = ["."], paths.normalise(text[:-2] or "/")
    else:
        fragments = [] if text.endswith("/") else [Path(normalized).name]
        candidate = normalized if text.endswith("/") else str(Path(normalized).parent)
    for _ in range(256):
        try:
            directory = paths.resolve(candidate, root)
            if directory.is_dir():
                return directory, "/".join(fragments), True
        except (OSError, paths.OutsideRoot):
            return scope, text, False
        if candidate == "/":
            break
        fragments.insert(0, Path(candidate).name)
        candidate = str(Path(candidate).parent)
    return scope, text, False


def complete_children(scope, fragment, root, limit=50, show_hidden=False):
    """Offer immediate entries before deeper matches, with predictable bounds."""
    found = []
    complete = True
    deadline = time.monotonic() + 0.05
    needle = fragment.lower()
    try:
        with os.scandir(scope) as entries:
            for index, entry in enumerate(entries):
                if index >= 4000 or time.monotonic() >= deadline:
                    complete = False
                    break
                if entry.name.startswith(".") and not show_hidden and not needle.startswith("."):
                    continue
                if needle.startswith(".") and not entry.name.startswith("."):
                    continue
                lower = entry.name.lower()
                if needle not in lower:
                    continue
                try:
                    target = Path(os.path.realpath(entry.path)) if entry.is_symlink() else Path(entry.path)
                    if target != root and root not in target.parents:
                        continue
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                request = as_request(entry.path, str(root))
                found.append((not lower.startswith(needle), entry.name.startswith("."), not is_dir,
                              lower, request, is_dir))
    except OSError:
        complete = False
    found.sort(key=lambda row: row[:4])
    return [(row[4], row[5]) for row in found[:limit]], complete


def recent_paths(recent, scope, root, limit, show_hidden=False):
    """Only existing, contained paths in this scope are offered as recents."""
    entries = list(recent) if isinstance(recent, (list, dict)) else []
    if isinstance(recent, dict):
        entries.sort(key=lambda key: recent[key] if isinstance(recent[key], (int, float)) else 0, reverse=True)
    found = []
    for requested in entries[:200]:
        if not isinstance(requested, str):
            continue
        if not show_hidden and any(part.startswith(".") for part in requested.split("/") if part):
            continue
        try:
            target = paths.resolve(requested, root)
            if target != scope and scope not in target.parents:
                continue
            if not target.exists():
                continue
            found.append((requested, target.is_dir()))
        except (OSError, paths.OutsideRoot):
            continue
        if len(found) >= limit:
            break
    return found


def find(scope, query, limit=50, show_hidden=False, root=None, recent=None, home=None):
    """Interactive search: immediate completions and a nonblocking deep index."""
    root = Path(root if root is not None else scope).resolve()
    scope = Path(scope).resolve()
    if scope != root and root not in scope.parents:
        raise paths.OutsideRoot(str(scope))
    limit = max(1, min(int(limit), 200))
    query = (query or "").strip()[:4096]
    recent = recent or {}
    effective, fragment, completing = query_scope(scope, query, root, home)
    scope_request = as_request(str(effective), str(root))
    if not query:
        chosen = recent_paths(recent, scope, root, limit, show_hidden)
        return {"matches": [{"path": path, "name": path.rsplit("/", 1)[-1], "is_dir": is_dir}
                            for path, is_dir in chosen], "complete": True, "indexing": False,
                "recent": True, "scope": scope_request}
    direct, direct_complete = complete_children(effective, fragment, root, limit, show_hidden) if completing else ([], True)
    if completing and not fragment:
        return {"matches": [{"path": path, "name": path.rsplit("/", 1)[-1], "is_dir": is_dir}
                            for path, is_dir in direct], "complete": direct_complete,
                "indexing": False, "scope": scope_request, "completing": True,
                "widened": effective != scope}
    snapshot = INDEXES.get(effective, root, show_hidden)
    entries = snapshot["entries"]
    # A small cold-start seed keeps ordinary searches useful immediately.
    # The full scan always runs on the background worker.
    if not entries and not completing:
        entries, _, _ = scan(effective, root, show_hidden, limit=512, seconds=0.02, max_depth=1)
    recent_order = {path: index for index, (path, _) in enumerate(recent_paths(recent, effective, root, 200, show_hidden))}
    scored = []
    for request, is_dir in entries:
        points = rank(request, fragment)
        if points is not None:
            if completing and request.rsplit("/", 1)[-1].lower().startswith(fragment.lower()):
                points += 10000
            scored.append((points, recent_order.get(request, 1000), request, is_dir))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen = [(path, is_dir, None) for path, is_dir in direct]
    seen = {path for path, _ in direct}
    for points, _, request, is_dir in scored:
        if request not in seen:
            chosen.append((request, is_dir, points))
            seen.add(request)
        if len(chosen) >= limit:
            break
    complete = direct_complete and (snapshot["complete"] or (completing and not fragment))
    return {"matches": [{"path": path, "name": path.rsplit("/", 1)[-1], "is_dir": is_dir,
                          "score": points} for path, is_dir, points in chosen[:limit]],
            "complete": complete, "indexing": snapshot["indexing"],
            "scanned": snapshot["scanned"], "scope": scope_request,
            "completing": completing, "widened": effective != scope}
