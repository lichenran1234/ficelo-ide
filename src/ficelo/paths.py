"""Turning a path from a request into a path on disk, safely.

Every route that touches the filesystem goes through :func:`resolve`. It is the
only place that decides what is reachable, so there is one thing to get right
and one thing to read when asking whether a request could escape the root.
"""

import os
from pathlib import Path, PurePosixPath


class OutsideRoot(Exception):
    """A request named something the root does not contain."""


def normalise(requested):
    """Collapse a request to an absolute, dot-free POSIX path.

    Done textually, before the filesystem is consulted at all. Asking the OS to
    resolve `..` first would follow symlinks on the way, so a link inside the
    root pointing outward could be walked through before anyone checked — the
    check has to come first to mean anything.
    """
    text = str(requested or "/").replace("\\", "/")
    if not text.startswith("/"):
        text = "/" + text
    parts = []
    for part in PurePosixPath(text).parts[1:]:
        if part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def resolve(requested, root):
    """The on-disk path for a request, or raise :class:`OutsideRoot`.

    `requested` is root-relative and always treated as such: a leading slash
    means the root, not the filesystem root, so a request can never name
    anything above it however it is spelled.

    Symlinks are resolved and the result checked again, so a link inside the
    root pointing outside it is refused. That costs a `realpath` per request
    and is the only way the answer means anything on a tree the user controls.
    """
    root_real = Path(os.path.realpath(root))
    relative = normalise(requested).lstrip("/")
    target = root_real / relative if relative else root_real

    # A path that does not exist yet is still answerable — creating a file
    # asks about one. Resolve as far as the filesystem goes and judge that.
    real = Path(os.path.realpath(target))
    if real != root_real and root_real not in real.parents:
        raise OutsideRoot(normalise(requested))
    return real


def to_request(path, root):
    """The root-relative path for something on disk: the inverse of resolve.

    What the browser is given, so nothing in the interface reveals where the
    root sits on the machine.
    """
    root_real = Path(os.path.realpath(root))
    real = Path(os.path.realpath(path))
    if real == root_real:
        return "/"
    return "/" + str(real.relative_to(root_real))


def is_dir(entry):
    """Whether this is a directory, for callers that must not stop at one bad entry.

    A dead mount answers stat with an error rather than an answer — a
    disconnected network share, a stale FUSE mount — and one of those sitting
    in a folder must not cost the whole listing.

    Takes anything with an `is_dir`, not a path: the hot caller is an
    `os.DirEntry` from `scandir`, which already holds the answer and would
    have to stat all over again if it were turned back into a Path.
    """
    try:
        return entry.is_dir()
    except OSError:
        return False
