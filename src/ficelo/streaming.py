"""Bounded transfers and single HTTP byte ranges for native media viewers."""

import os
import re
import stat
from contextlib import contextmanager

CHUNK_BYTES = 128 * 1024


class UnsatisfiableRange(ValueError):
    """The requested byte interval does not intersect the resource."""


def byte_range(value, size):
    """Return an inclusive interval, or None when Range should be ignored.

    Multiple ranges and unfamiliar units fall back to the complete resource;
    multipart responses offer no benefit to the browser's sequential player.
    """
    if not value or not value.startswith("bytes=") or "," in value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not any(match.groups()):
        raise UnsatisfiableRange("invalid byte range")
    first, last = match.groups()
    try:
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            length = int(last)
            start, end = max(0, size - length), size - 1
    except ValueError as error:
        raise UnsatisfiableRange("invalid byte range") from error
    if start >= size or start > end:
        raise UnsatisfiableRange("byte range is outside the file")
    return start, end


@contextmanager
def open_regular(target):
    """Refuse devices and pipes without waiting for a writer on a FIFO."""
    descriptor = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if stat.S_ISDIR(info.st_mode):
            raise IsADirectoryError(str(target))
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("only regular files can be opened")
        yield source, info.st_size


def copy_bytes(source, destination, start, length):
    source.seek(start)
    while length:
        chunk = source.read(min(CHUNK_BYTES, length))
        if not chunk:
            return False  # A concurrent truncation must close the connection.
        destination.write(chunk)
        length -= len(chunk)
    return True
