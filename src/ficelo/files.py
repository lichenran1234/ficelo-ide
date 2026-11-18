"""Reading the tree: what is in a folder, and what is in a file."""

import collections
import os
import stat as stat_module
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import paths, streaming

# Read no more than this into the browser as text. Above it the editor stops
# being useful and the transfer starts being the slow part; the caller is told
# the file was truncated rather than quietly shown part of it.
MAX_TEXT_BYTES = 4 * 1024 * 1024

# Sniffed rather than trusted from the extension: a .log full of NULs is not
# text and a .bak full of prose is. Only the first block is read, which is
# enough for anything that is not deliberately adversarial.
SNIFF_BYTES = 8192


def describe(entry, root):
    """One entry, as the browser needs it.

    `entry` is an `os.DirEntry` or a `Path`. Anything that cannot be stated
    still gets an answer — a dead mount, a file deleted between the listing
    and the stat — carrying the reason, because a folder that silently loses
    an entry is worse than one that shows a broken one.
    """
    name = entry.name
    lexical = Path(os.path.abspath(entry.path if hasattr(entry, "path") else entry))
    relative = lexical.relative_to(Path(os.path.abspath(root)))
    requested = "/" if relative == Path(".") else "/" + relative.as_posix()
    try:
        # Keep the link's own name in the tree, while checking the target
        # before reading metadata. An outside link is one broken row.
        paths.resolve(requested, root)
        info = entry.stat()
        return {
            "name": name,
            "path": requested,
            "is_dir": paths.is_dir(entry),
            "size": info.st_size,
            "modified": datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
            "mode": stat_module.filemode(info.st_mode),
        }
    except (OSError, paths.OutsideRoot) as err:
        return {
            "name": name,
            "path": requested,
            "is_dir": False,
            "size": 0,
            "modified": None,
            "mode": None,
            "error": "link points outside the served folder" if isinstance(err, paths.OutsideRoot) else str(err),
        }


def listing(directory, root, show_hidden=False, request_path=None):
    """The entries of one folder, folders first and then by name.

    Case-insensitive within each group, so a listing reads the way a person
    would sort it rather than the way ASCII does — `Makefile` belongs beside
    `main.c`, not in a clump of capitals above it.
    """
    entries = []
    with os.scandir(directory) as scan:
        for entry in scan:
            if not show_hidden and entry.name.startswith("."):
                continue
            entries.append(entry)
    result = [describe(e, root) for e in entries]
    result.sort(key=lambda entry: (not entry["is_dir"], entry["name"].lower()))
    if request_path is not None:
        # The router resolves the folder for containment; display paths can
        # still use the alias through which the user opened that folder.
        prefix = paths.normalise(request_path).rstrip("/")
        for entry in result:
            entry["path"] = prefix + "/" + entry["name"]
    return result


def looks_binary(sample):
    """Whether a leading block of bytes is not text.

    A NUL settles it. Failing that, a high proportion of bytes that no text
    encoding would produce — control characters outside tab, newline and
    carriage return.
    """
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    printable = bytes(range(32, 127)) + b"\t\n\r\f\b\x1b"
    odd = sum(1 for byte in sample if byte not in printable and byte < 128)
    return odd / len(sample) > 0.30


def read_text(path):
    """A file's text, with whether it was cut short and whether it is text at all.

    Returns `(text, truncated, binary)`. Decoded as UTF-8 with replacement:
    one bad byte in a large file should cost that character, not the file.
    """
    with streaming.open_regular(path) as (handle, size):
        sample = handle.read(SNIFF_BYTES)
        if looks_binary(sample):
            return None, False, True
        rest = handle.read(max(0, MAX_TEXT_BYTES - len(sample)))
    raw = sample + rest
    return raw.decode("utf-8-sig", errors="replace"), size > MAX_TEXT_BYTES, False


class Conflict(Exception):
    """The file changed underneath the editor."""


def stamp(path):
    """A value that changes when the file does, for conflict detection.

    Modification time and size together: mtime alone has one-second
    granularity on some filesystems, and two saves inside the same second
    are exactly the case this is meant to catch.
    """
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return f"{info.st_mtime_ns}:{info.st_size}"


def size(path):
    """Bytes on disk, or None. What the status bar reports — note this is
    the file's size, not the length of the text handed over, which differs
    whenever a read was truncated."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def modified(path):
    """Seconds since the epoch, or None. Used to fade older tabs."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def write_text(path, text, expect=None):
    """Replace a file's contents, all at once. Returns the bytes written.

    Written to a temporary neighbour and renamed over the target, so a save
    that is interrupted — the process killed, the disk filling, the laptop
    closing — leaves the previous version intact rather than a half-written
    file. The rename is atomic only within a filesystem, which is why the
    temporary sits in the same directory rather than in /tmp.

    An existing file's permissions are kept: saving from a browser should not
    quietly make a private file world-readable, or strip the execute bit off
    a script.

    `expect` is the stamp the caller last saw. If the file has changed since
    then — another editor, a git checkout, a second browser tab — the write
    is refused rather than silently discarding whatever arrived in between.
    Passing None skips the check, which is what creating a new file does.
    """
    if expect is not None and stamp(path) != expect:
        raise Conflict(
            "this file changed on disk since it was opened; reload it before saving"
        )
    raw = text.encode("utf-8")
    try:
        with streaming.open_regular(path) as (original, _):
            if original.read(3) == b"\xef\xbb\xbf" and not raw.startswith(b"\xef\xbb\xbf"):
                raw = b"\xef\xbb\xbf" + raw
    except FileNotFoundError:
        pass
    directory = path.parent
    handle, temp = tempfile.mkstemp(dir=str(directory), prefix="." + path.name + ".")
    temp = Path(temp)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(raw)
            out.flush()
            # The rename is atomic, but only orders against data that has
            # reached the disk. Without this a crash can leave the new name
            # pointing at empty blocks.
            os.fsync(out.fileno())
        try:
            os.chmod(str(temp), stat_module.S_IMODE(path.stat().st_mode))
        except FileNotFoundError:
            pass  # A new file keeps mkstemp's private permissions.
        os.replace(str(temp), str(path))
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    return len(raw)


def create(path, kind):
    """Make a new empty file or folder. Refuses to replace anything."""
    if path.exists():
        raise FileExistsError("something is already there")
    if kind == "folder":
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        # x mode so two requests racing cannot both believe they created it.
        with open(path, "x"):
            pass
    return stamp(path)


def rename(source, target):
    """Move a file or folder. Refuses to overwrite."""
    if not source.exists() and not source.is_symlink():
        raise FileNotFoundError("there is nothing there to rename")
    if target.exists() or target.is_symlink():
        raise FileExistsError("something is already at the new name")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(source), str(target))


def remove(path):
    """Delete a file, or an empty folder.

    Deliberately not recursive. A single mis-click in a file browser should
    not be able to destroy a directory tree, and anyone who means it has a
    shell. The error says so rather than appearing to be a bug.
    """
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        try:
            path.rmdir()
        except OSError:
            raise IsADirectoryError(
                "this folder is not empty; ficelo only deletes empty folders"
            )
    else:
        path.unlink()


MAX_WATCH_PATHS = 512


def fingerprints(requested, root):
    """Stat a bounded set of files/folders without opening their contents.

    Missing and inaccessible paths are explicit nulls, so callers can tell a
    disappearance from an omitted request. Values match the read/save stamp.
    """
    if not isinstance(requested, list) or len(requested) > MAX_WATCH_PATHS:
        raise ValueError("watch paths must be a list of at most 512 paths")
    result = {}
    for item in requested:
        if not isinstance(item, str) or len(item) > 4096:
            raise ValueError("invalid watch path")
        try:
            target = paths.resolve(item, root)
            result[item] = stamp(target)
        except (OSError, paths.OutsideRoot):
            result[item] = None
    return result


def folder_usage(directory, seconds=0.2, max_entries=20000):
    """Count content bytes and entries without following directory symlinks."""
    pending = collections.deque([Path(directory)])
    total = regular = folders = links = scanned = 0
    complete = True
    deadline = time.monotonic() + max(0.0, seconds)
    while pending:
        current = pending.popleft()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if scanned >= max_entries or time.monotonic() >= deadline:
                        return {"bytes": total, "files": regular, "folders": folders,
                                "links": links, "scanned": scanned, "complete": False}
                    scanned += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat_module.S_ISLNK(info.st_mode):
                            links += 1
                        elif stat_module.S_ISDIR(info.st_mode):
                            folders += 1
                            pending.append(Path(entry.path))
                        elif stat_module.S_ISREG(info.st_mode):
                            regular += 1
                            total += info.st_size
                    except OSError:
                        complete = False
        except OSError:
            complete = False
    return {"bytes": total, "files": regular, "folders": folders,
            "links": links, "scanned": scanned, "complete": complete}


def information(requested, root, seconds=0.2, max_entries=20000):
    """Metadata for a request path, with a budgeted directory total."""
    logical = paths.normalise(requested)
    target = paths.resolve(logical, root)
    info = target.stat()
    lexical = Path(root) / logical.lstrip("/")
    result = {"path": logical, "name": Path(logical).name or "/",
              "is_dir": stat_module.S_ISDIR(info.st_mode), "is_symlink": lexical.is_symlink(),
              "size": info.st_size, "mode": stat_module.filemode(info.st_mode),
              "modified": datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
              "stamp": stamp(target)}
    if result["is_dir"]:
        result["usage"] = folder_usage(target, seconds=seconds, max_entries=max_entries)
    return result
