"""ficelo's own files: settings the page remembers, and server defaults.

Everything lives under one folder, ~/.ficelo/ unless FICELO_HOME says
otherwise:

    settings.json   what the page remembers — sessions, open tabs,
                    appearance, per-file view and scroll position
    config.json     server defaults, read at startup; command-line flags
                    override it

Settings used to live in the browser's localStorage, which is per browser
and per hostname: opening the same server under a second name started from
nothing. On the server they follow the user instead.

These files are never part of the served tree's write permission. A
read-only server still saves settings — it only ever writes here.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

from . import files

SETTINGS_FILE = "settings.json"
CONFIG_FILE = "config.json"

# Keys the page may store, and nothing else: a fixed namespace with a short,
# filename-safe body, so a request can never turn a key into a path.
KEY = re.compile(r"^ficelo\.[a-z0-9_.-]{1,64}$")

# Generous for scroll positions across a few hundred files, small enough
# that one request cannot make the settings file the size of the disk.
MAX_VALUE_BYTES = 512 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024

# The server defaults config.json may set, with the type each must be.
CONFIG_KEYS = {"root": str, "host": str, "port": int, "write": bool, "hidden": bool, "terminal": bool, "allowed_origins": list}


class BadState(Exception):
    """A request or a file that cannot be accepted, with a reason to show."""


def home():
    """The folder ficelo keeps its own files in."""
    override = os.environ.get("FICELO_HOME")
    return Path(override).expanduser() if override else Path.home() / ".ficelo"


def ensure(folder):
    """Create the folder, private to the user: settings name files and
    folders on the machine, which is nobody else's business."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


class Settings:
    """settings.json, read whole and written one key at a time.

    Writes are serialised in-process and land atomically, so two browser
    tabs saving at once cannot interleave into a half-written file. Each
    change is applied to what is on disk at that moment, so tabs saving
    different keys do not undo each other.
    """

    def __init__(self, folder):
        self.path = Path(folder) / SETTINGS_FILE
        # Re-entrant is not needed: put() calls the unlocked reader.
        self.lock = threading.Lock()
        self.applied = {}

    def read(self):
        """Everything saved, including every write that arrived before this.

        Taken under the write lock. Without it, a read arriving while a write
        was still flushing to disk got the previous file: under load a page
        saved a setting, reloaded half a second later, read the old value,
        and then saved that back over the new one.
        """
        with self.lock:
            return self._read_unlocked()

    def _read_unlocked(self):
        self.applied = {}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            found = json.loads(raw)
        except ValueError:
            # A hand-edit gone wrong should cost the settings, not the page.
            return {}
        if not isinstance(found, dict):
            return {}
        acknowledgements = found.get("_writers", {})
        self.applied = {key: value for key, value in acknowledgements.items()
                        if isinstance(key, str) and isinstance(value, int) and value > 0} if isinstance(acknowledgements, dict) else {}
        return {k: v for k, v in found.items() if KEY.match(k)}

    def put(self, key, value):
        """Store one key, or remove it when value is None."""
        if not isinstance(key, str) or not KEY.match(key):
            raise BadState("not a settings key: %r" % (key,))
        encoded = json.dumps(value)
        if len(encoded) > MAX_VALUE_BYTES:
            raise BadState("that setting is too large to store")
        with self.lock:
            current = self._read_unlocked()
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
            self._write(current)

    def patch(self, key, changes):
        """Apply only fields this reader changed, against the latest disk state.

        Tab identities are map keys. An unchanged tab, position or preference
        is never submitted, so independent browser views can share a workspace.
        """
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise BadState("invalid settings key")
        if not isinstance(changes, list) or len(changes) > 2000:
            raise BadState("invalid settings changes")
        if len(json.dumps(changes)) > MAX_VALUE_BYTES:
            raise BadState("settings changes are too large")
        for change in changes:
            if not isinstance(change, dict):
                raise BadState("invalid settings change")
            parts = change.get("path")
            if (not isinstance(parts, list) or len(parts) > 16
                    or any(not isinstance(part, str) or part in ("__proto__", "constructor", "prototype") for part in parts)):
                raise BadState("invalid settings field")
            if "writer" in change and (not isinstance(change["writer"], str)
                    or len(change["writer"]) > 80 or not isinstance(change.get("sequence"), int)
                    or change["sequence"] < 1):
                raise BadState("invalid change sequence")
        with self.lock:
            requested_key = key
            acknowledgements: dict[str, int] = {}
            current = self._read_unlocked()
            original = json.loads(json.dumps(current.get(key)))
            forwarded = False
            seen = set()
            while key.startswith("ficelo.workspace.") and isinstance(current.get(key), dict) and current[key].get("redirect"):
                if key in seen:
                    raise BadState("workspace redirect loop")
                seen.add(key)
                key = "ficelo.workspace." + current[key]["redirect"]
                forwarded = True
            value = current.get(key)
            for change in changes:
                if "writer" in change:
                    identity = change["writer"] + ":" + requested_key
                    sequence = change["sequence"]
                    if sequence <= max(self.applied.get(identity, 0), acknowledgements.get(identity, 0)):
                        continue
                    acknowledgements[identity] = sequence
                parts = change["path"]
                # A retired workspace cannot erase its survivor. A deleted
                # workspace stays deleted until the explicit restore action.
                if isinstance(value, dict) and value.get("deletedAt") and parts != ["deletedAt"]:
                    continue
                if not parts:
                    if not forwarded:
                        value = None if change.get("remove") else change.get("value")
                    continue
                if (len(parts) > 2 and parts[0] == "tabs"
                        and (not isinstance(value, dict) or parts[1] not in value.get("tabs", {}))):
                    continue
                if not isinstance(value, dict):
                    value = {}
                node = value
                for part in parts[:-1]:
                    if not isinstance(node.get(part), dict):
                        node[part] = {}
                    node = node[part]
                field = parts[-1]
                if forwarded and (field in node or change.get("remove")):
                    continue
                if change.get("remove"):
                    node.pop(field, None)
                elif (key.startswith("ficelo.workspace.") and parts == ["at"]
                        and isinstance(node.get(field), (int, float))
                        and isinstance(change.get("value"), (int, float))):
                    # Browser tabs can deliver activity out of order after
                    # reconnecting. Always retain the most recent activity.
                    node[field] = max(node[field], change["value"])
                else:
                    node[field] = change.get("value")
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
            self.applied.update(acknowledgements)
            # Only live/recent page writers need transport acknowledgements.
            if len(self.applied) > 4096:
                self.applied = dict(list(self.applied.items())[-2048:])
            if value != original or forwarded or acknowledgements:
                self._write(current)
            return {"key": key, "value": value}

    def _write(self, current):
        if any(len(json.dumps(value)) > MAX_VALUE_BYTES for value in current.values()):
            raise BadState("that setting is too large to store")
        document = dict(current)
        if self.applied:
            document["_writers"] = self.applied
        text = json.dumps(document, indent=1, sort_keys=True)
        if len(text) > MAX_FILE_BYTES:
            raise BadState("settings have grown too large to store")
        ensure(self.path.parent)
        files.write_text(self.path, text + "\n")

    def merge_workspaces(self, expected):
        """Merge an exact preview, retaining the originals for recovery."""
        if not isinstance(expected, dict) or not 2 <= len(expected) <= 50:
            raise BadState("select between two and fifty workspaces")
        ids = list(expected)
        if any(not isinstance(sid, str) or not KEY.fullmatch("ficelo.workspace." + sid) for sid in ids):
            raise BadState("invalid workspace id")
        with self.lock:
            current = self._read_unlocked()
            for sid, entry in expected.items():
                if (not isinstance(entry, dict) or entry.get("deletedAt") or entry.get("redirect")
                        or current.get("ficelo.workspace." + sid) != entry):
                    raise BadState("workspaces changed; refresh the merge preview")
            merged = json.loads(json.dumps(expected[ids[0]]))

            def add_missing(target, incoming):
                for field, value in incoming.items():
                    if field not in target:
                        target[field] = value
                    elif isinstance(value, dict) and isinstance(target[field], dict):
                        add_missing(target[field], value)
            for sid in ids[1:]:
                entry = expected[sid]
                add_missing(merged.setdefault("tabs", {}), entry.get("tabs", {}))
                add_missing(merged.setdefault("state", {}), entry.get("state", {}))
                order = merged.setdefault("order", [])
                order.extend(tab for tab in entry.get("order", []) if tab not in order)
                merged["starred"] = bool(merged.get("starred") or entry.get("starred"))
                current["ficelo.workspace." + sid] = {"id": sid, "redirect": ids[0]}
            merged["at"] = int(time.time() * 1000)
            current["ficelo.workspace." + ids[0]] = merged
            ensure(self.path.parent)
            backup = self.path.parent / f"workspaces-before-merge-{time.time_ns()}.json"
            with backup.open("x", encoding="utf-8") as handle:
                json.dump(expected, handle, indent=1)
            self._write(current)
            return {"id": ids[0], "settings": current, "backup": backup.name}


def read_config(folder):
    """Server defaults from config.json, checked. Missing file: no defaults."""
    path = Path(folder) / CONFIG_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        found = json.loads(raw)
    except ValueError as err:
        raise BadState("%s is not valid JSON: %s" % (path, err))
    if not isinstance(found, dict):
        raise BadState("%s should hold a JSON object" % path)
    checked = {}
    for key, value in found.items():
        if key not in CONFIG_KEYS:
            raise BadState(
                "%s: unknown setting %r (known: %s)" % (path, key, ", ".join(sorted(CONFIG_KEYS)))
            )
        wanted = CONFIG_KEYS[key]
        # bool is a subclass of int, so a port of `true` has to be refused
        # explicitly rather than read as port 1.
        if not isinstance(value, wanted) or (wanted is int and isinstance(value, bool)):
            raise BadState("%s: %r should be a %s" % (path, key, wanted.__name__))
        if key == "allowed_origins" and not all(isinstance(origin, str) for origin in value):
            raise BadState(f"{path}: allowed_origins should contain only URL strings")
        checked[key] = value
    return checked
