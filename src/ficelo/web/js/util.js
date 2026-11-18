/* Small things used everywhere. No DOM framework: `el` is the whole of it. */

export function el(tag, props, children) {
  const node = Object.assign(document.createElement(tag), props || {});
  (children || []).forEach((child) => child != null && node.append(child));
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

/* Synchronous preferences backed by atomic field changes on the server.
   Failed changes remain queued; a tab-local recovery copy survives reload. */
const cache = new Map();
const pending = new Map();
const sending = new Map();
const timers = new Map();
const failures = new Set();
const lastSent = new Map();
const knownWorkspaces = new Set();
const missingWorkspaces = new Set();
const RECOVERY_KEY = "ficelo.pending";
const SNAPSHOT_PREFIX = "ficelo.recovery.";
const WORKSPACE_PREFIX = "ficelo.workspace.";
const MAX_SNAPSHOTS = 20;
const MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;
let workspace = null;
const writer = globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36);
let sequence = 0;
const scoped = new Set(["ficelo.scroll", "ficelo.mdview", "ficelo.open", "ficelo.treeroot",
  "ficelo.expanded", "ficelo.difflayout", "ficelo.diffmap", "ficelo.sidebar", "ficelo.toc"]);
const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

/* Only navigation metadata belongs in a recovery record. Monaco's view
   state contains positions and folded regions, never an editor buffer. */
function positions(value, depth = 0) {
  if (depth > 16) return undefined;
  if (value === null || typeof value === "boolean" || typeof value === "number") return value;
  if (Array.isArray(value)) return value.map((item) => positions(item, depth + 1));
  if (!object(value)) return undefined;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, positions(item, depth + 1)]).filter(([, item]) => item !== undefined));
}

export function workspaceSnapshot(record) {
  if (!object(record) || typeof record.id !== "string") return null;
  const result = {};
  for (const key of ["id", "name", "group", "root", "active", "mode"]) {
    if (typeof record[key] === "string") result[key] = record[key];
  }
  if (typeof record.starred === "boolean") result.starred = record.starred;
  if (typeof record.at === "number") result.at = record.at;
  for (const key of ["paths", "order", "expanded"]) {
    if (Array.isArray(record[key])) result[key] = record[key].filter((item) => typeof item === "string");
  }
  const tabs = object(record.tabs) ? record.tabs : Object.fromEntries((result.paths || []).map((path) => [path, { id: path, path }]));
  result.tabs = Object.fromEntries(Object.entries(tabs).flatMap(([id, tab]) => {
    if (!object(tab) || typeof tab.path !== "string" || tab.kind === "terminal") return [];
    const saved = { id, path: tab.path };
    for (const key of ["name", "kind", "rev", "repo", "base", "view"]) if (typeof tab[key] === "string") saved[key] = tab[key];
    if (tab.wholeCommit === true) saved.wholeCommit = true;
    if (object(tab.viewState)) saved.viewState = positions(tab.viewState);
    if (object(tab.commitViewState)) {
      saved.commitViewState = positions(tab.commitViewState);
      if (typeof tab.commitViewState.active === "string") saved.commitViewState.active = tab.commitViewState.active;
      if (Array.isArray(tab.commitViewState.folded)) saved.commitViewState.folded = tab.commitViewState.folded.filter((item) => typeof item === "string");
    }
    return [[id, saved]];
  }));
  if (object(record.review)) {
    result.review = {};
    for (const key of ["repo", "base", "tab"]) if (typeof record.review[key] === "string") result.review[key] = record.review[key];
    for (const key of ["collapsed", "foldedCommits"]) if (Array.isArray(record.review[key])) result.review[key] = record.review[key].filter((item) => typeof item === "string");
  }
  result.state = {};
  for (const [key, value] of Object.entries(record.state || {})) {
    if (!scoped.has(key)) continue;
    // These values are preferences and path-indexed view positions, not data.
    if (key === "ficelo.scroll" && object(value)) result.state[key] = Object.fromEntries(Object.entries(value).map(([path, position]) => [path,
      object(position) && typeof position.anchor === "string" ? { ...positions(position), anchor: position.anchor } : positions(position)]));
    else if (key === "ficelo.mdview" && object(value)) result.state[key] = Object.fromEntries(Object.entries(value).filter(([, view]) => ["preview", "edit", "split"].includes(view)));
    else if (["ficelo.sidebar", "ficelo.toc", "ficelo.diffmap"].includes(key)) result.state[key] = positions(value);
    else if (key === "ficelo.difflayout" && ["side", "inline"].includes(value)) result.state[key] = value;
    else if (key === "ficelo.treeroot" && typeof value === "string") result.state[key] = value;
    else if (key === "ficelo.expanded" && Array.isArray(value)) result.state[key] = value.filter((item) => typeof item === "string");
    else if (key === "ficelo.open" && object(value)) result.state[key] = { paths: Array.isArray(value.paths) ? value.paths.filter((item) => typeof item === "string") : [], active: typeof value.active === "string" ? value.active : null };
  }
  return result;
}

function snapshots() {
  const result = [];
  try {
    for (let n = 0; n < localStorage.length; n++) {
      const key = localStorage.key(n);
      if (!key?.startsWith(SNAPSHOT_PREFIX)) continue;
      try {
        const text = localStorage.getItem(key), entry = JSON.parse(text);
        const record = workspaceSnapshot(entry.record);
        if (record && typeof entry.at === "number") result.push({ ...entry, key, record, bytes: new TextEncoder().encode(text).length });
      } catch (_) { /* Ignore malformed browser data. */ }
    }
  } catch (_) { /* Browser storage may be disabled. */ }
  return result.sort((a, b) => b.at - a.at || a.key.localeCompare(b.key));
}

export function recoverySnapshots() {
  return snapshots().filter((entry) => entry.reason !== "Saving" || entry.owner !== writer);
}

export function dismissRecovery(key) {
  if (!key?.startsWith(SNAPSHOT_PREFIX)) return;
  try { localStorage.removeItem(key); } catch (_) {}
}

function snapshot(key, record, reason = "Saving") {
  if (!key.startsWith(WORKSPACE_PREFIX) || record?.deletedAt || record?.redirect) return;
  const saved = workspaceSnapshot(record);
  if (!saved) return;
  const storageKey = SNAPSHOT_PREFIX + writer + ":" + encodeURIComponent(key);
  try {
    const prior = JSON.parse(localStorage.getItem(storageKey) || "null");
    const entry = { owner: writer, workspace: key, record: saved, at: Date.now(),
      existed: knownWorkspaces.has(key) || prior?.existed === true,
      reason: reason === "Saving" ? prior?.reason || reason : reason };
    const text = JSON.stringify(entry);
    if (new TextEncoder().encode(text).length > MAX_SNAPSHOT_BYTES) return;
    // Prune before writing as well, so a nearly full quota can make room.
    let bytes = new TextEncoder().encode(text).length, count = 1;
    for (const old of snapshots().filter((item) => item.key !== storageKey)) {
      bytes += old.bytes;
      if (++count > MAX_SNAPSHOTS || bytes > MAX_SNAPSHOT_BYTES) dismissRecovery(old.key);
    }
    localStorage.setItem(storageKey, text);
    return storageKey;
  } catch (_) { /* Pending server changes still work without browser storage. */ }
}

function preserveRetired(settings) {
  for (const [key, record] of cache) {
    if (!key.startsWith(WORKSPACE_PREFIX) || !record || record.deletedAt || record.redirect) continue;
    const remote = settings[key];
    if (remote?.redirect) snapshot(key, record, "Merged elsewhere");
    else if (remote?.deletedAt || (!remote && (!pending.has(key) || knownWorkspaces.has(key)))) snapshot(key, record, "Removed elsewhere");
    if (!remote && knownWorkspaces.has(key)) retireMissing(key);
  }
  for (const key of Object.keys(settings)) if (key.startsWith(WORKSPACE_PREFIX)) {
    knownWorkspaces.add(key); missingWorkspaces.delete(key);
  }
}

function retireMissing(key) {
  missingWorkspaces.add(key);
  pending.delete(key);
  failures.delete(key);
  clearTimeout(timers.get(key)); timers.delete(key);
}

/* Map leaves are independent fields; arrays express a deliberate order. */
export function changesBetween(before, after, path = []) {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (object(after) && (before === undefined || object(before)) && !(before === undefined && path.length === 2 && path[0] === "tabs")) {
    return [...new Set([...Object.keys(before || {}), ...Object.keys(after)])]
      .flatMap((key) => changesBetween(before && before[key], after[key], [...path, key]));
  }
  return [after === undefined ? { path, remove: true } : { path, value: after }];
}

function applyChanges(before, changes) {
  let value = clone(before);
  for (const change of changes) {
    if (!change.path.length) { value = change.remove ? undefined : clone(change.value); continue; }
    if (!object(value)) value = {};
    let node = value;
    for (const part of change.path.slice(0, -1)) {
      if (!object(node[part])) node[part] = {};
      node = node[part];
    }
    const part = change.path.at(-1);
    if (change.remove) delete node[part];
    else Object.defineProperty(node, part, { value: clone(change.value), writable: true, enumerable: true, configurable: true });
  }
  return value;
}

function recovery() {
  try { sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(Object.fromEntries(pending))); }
  catch (_) { /* The server remains authoritative when browser storage is disabled. */ }
}

function batch(key, limit = 450 * 1024) {
  const selected = [];
  let bytes = 0;
  for (const change of pending.get(key) || []) {
    const length = new TextEncoder().encode(JSON.stringify(change)).length;
    if (selected.length && (selected.length >= 1500 || bytes + length > limit)) break;
    selected.push(change); bytes += length;
  }
  return selected;
}

function enqueue(key, changes) {
  let queue = pending.get(key) || [];
  for (const change of changes) {
    // A later assignment subsumes older updates to the same subtree.
    queue = queue.filter((old) => !change.path.every((part, index) => old.path[index] === part));
    queue.push(change);
  }
  pending.set(key, queue);
}

export async function refreshStore() {
  const response = await fetch("/api/state");
  if (!response.ok) throw new Error("Sessions could not be refreshed");
  const settings = (await response.json()).settings || {};
  preserveRetired(settings);
  recovery();
  for (const key of cache.keys()) {
    if (key !== workspace && !pending.has(key) && !(key in settings)) cache.delete(key);
  }
  for (const [key, value] of Object.entries(settings)) {
    if (key !== workspace && !pending.has(key)) cache.set(key, value);
  }
  return settings;
}

async function send(key) {
  clearTimeout(timers.get(key)); timers.delete(key);
  if (sending.has(key)) return sending.get(key);
  if (!pending.get(key)?.length) return;
  lastSent.set(key, Date.now());
  const changes = batch(key);
  const body = JSON.stringify({ key, changes });
  const job = (async () => {
    try {
      const response = await fetch("/api/state", { method: "POST",
        headers: { "Content-Type": "application/json" }, body,
        keepalive: body.length < 60 * 1024 });
      const reply = await response.json();
      if (!response.ok) throw new Error(reply.error || "Preferences could not be saved");
      if (key.startsWith(WORKSPACE_PREFIX) && reply.value) knownWorkspaces.add(key);
      if (reply.key && reply.key !== key) snapshot(key, cache.get(key), "Merged elsewhere");
      else if (reply.value?.deletedAt) snapshot(key, cache.get(key), "Removed elsewhere");
      failures.delete(key);
      const acknowledged = new Set(changes.map((change) => change.writer + ":" + change.sequence));
      pending.set(key, (pending.get(key) || []).filter((change) => !acknowledged.has(change.writer + ":" + change.sequence)));
      if (!pending.get(key).length) pending.delete(key);
      if (!pending.has(key)) {
        const owners = new Set(changes.map((change) => change.writer));
        owners.add(writer);
        for (const entry of snapshots()) if (entry.workspace === key && entry.reason === "Saving" && owners.has(entry.owner)) dismissRecovery(entry.key);
      }
      // Keep the local view as its own baseline. Importing unseen remote
      // fields here would turn the next local save into a request to delete them.
      if (reply.key && reply.key !== key) {
        window.dispatchEvent(new CustomEvent("ficelo:redirect", { detail: { ...reply, from: key } }));
      }
      recovery();
      window.dispatchEvent(new Event("ficelo:saved"));
    } catch (error) {
      failures.add(key);
      snapshot(key, cache.get(key), "Save failed");
      recovery();
      window.dispatchEvent(new CustomEvent("ficelo:save-error", { detail: error.message }));
      timers.set(key, setTimeout(() => send(key), 5000));
    } finally {
      sending.delete(key);
      if (pending.has(key) && !timers.has(key)) timers.set(key, setTimeout(() => send(key), Math.max(0, 100 - (Date.now() - lastSent.get(key)))));
    }
  })();
  sending.set(key, job);
  return job;
}

export async function loadStore() {
  try {
    const response = await fetch("/api/state");
    if (!response.ok) return false;
    const body = await response.json();
    const settings = body.settings || {};
    preserveRetired(settings);
    // A prior page may have closed before its keepalive request completed.
    // Preserve its full view before replaying only the outstanding deltas.
    for (const entry of snapshots()) {
      const remote = settings[entry.workspace];
      const missing = !remote && entry.existed !== false;
      if (remote?.redirect || remote?.deletedAt || missing) {
        if (missing) { knownWorkspaces.add(entry.workspace); retireMissing(entry.workspace); }
        const preserved = snapshot(entry.workspace, entry.record, remote?.redirect ? "Merged elsewhere" : "Removed elsewhere");
        if (preserved && preserved !== entry.key && entry.reason === "Saving") dismissRecovery(entry.key);
      }
    }
    cache.clear();
    Object.entries(settings).forEach(([key, value]) => cache.set(key, value));
    let saved = {};
    try { saved = JSON.parse(sessionStorage.getItem(RECOVERY_KEY) || "{}"); } catch (_) {}
    for (const [key, changes] of Object.entries(saved)) {
      if (!Array.isArray(changes)) continue;
      if (missingWorkspaces.has(key)) continue;
      enqueue(key, changes);
      // A tombstone/redirect remains visible to session selection. Applying
      // old local fields here would turn it back into a live-looking record.
      if (!cache.get(key)?.deletedAt && !cache.get(key)?.redirect) cache.set(key, applyChanges(cache.get(key), changes));
      send(key);
    }
    recovery();
    return true;
  } catch (_) { return false; }
}

export async function flushStore(closing = false) {
  if (closing) {
    recovery();
    // Include unacknowledged changes as well as the trailing edits. The
    // server deduplicates their sequence numbers if requests arrive twice
    // or out of order while the page is closing.
    for (const key of pending.keys()) {
      const changes = batch(key, 55 * 1024);
      const body = JSON.stringify({ key, changes });
      if (body.length >= 60 * 1024) continue;
      fetch("/api/state", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
    }
    return;
  }
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear();
  recovery();
  await Promise.all([...pending.keys()].map(async (key) => {
    await send(key);
    if (pending.has(key) && !failures.has(key)) await send(key);
  }));
}

function setRaw(key, value) {
  const changes = changesBetween(cache.get(key), value == null ? undefined : value).map((change) => ({ ...change, writer, sequence: ++sequence }));
  if (!changes.length) return true;
  if (value == null) cache.delete(key); else cache.set(key, clone(value));
  snapshot(key, cache.get(key), missingWorkspaces.has(key) ? "Removed elsewhere" : "Saving");
  if (missingWorkspaces.has(key)) return true;
  enqueue(key, changes);
  recovery();
  if (!timers.has(key) && !sending.has(key)) {
    const delay = Math.max(0, 100 - (Date.now() - (lastSent.get(key) || 0)));
    if (delay) timers.set(key, setTimeout(() => send(key), delay));
    else send(key);
  }
  return true;
}

export const store = {
  legacyState() { return Object.fromEntries([...cache.entries()].filter(([key]) => scoped.has(key))); },
  scope(id) { workspace = id ? "ficelo.workspace." + id : null; },
  entries() { return [...cache.entries()].map(([key, value]) => [key, clone(value)]); },
  get(key, fallback) {
    const found = workspace && scoped.has(key) ? cache.get(workspace)?.state?.[key] : cache.get(key);
    return found === undefined ? fallback : clone(found);
  },
  set(key, value) {
    if (workspace && scoped.has(key)) {
      const record = clone(cache.get(workspace) || {});
      record.state ||= {};
      if (value == null) delete record.state[key]; else record.state[key] = value;
      return setRaw(workspace, record);
    }
    return setRaw(key, value);
  },
};

export function debounce(fn, ms) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  /* Needed when a tab closes mid-debounce: without it a pending render fires
     against a model that has already been disposed. */
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export function basename(path) {
  return path === "/" ? "/" : path.slice(path.lastIndexOf("/") + 1);
}

export function parentOf(path) {
  const cut = path.lastIndexOf("/");
  return cut <= 0 ? "/" : path.slice(0, cut);
}

/* Every ancestor of a path, root first, excluding the path itself. Used to
   open the tree down to a file that was restored from a previous session. */
export function ancestors(path) {
  const out = ["/"];
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  let at = "";
  for (const part of parts) {
    at += "/" + part;
    out.push(at);
  }
  return out;
}

export function formatSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  /* One decimal below 10 so 1.4 MB does not round to 1 MB, none above it
     where the extra digit is noise. */
  return (value < 10 ? value.toFixed(1) : Math.round(value)) + " " + units[unit];
}

/* -- paths typed by hand --------------------------------------------------

   Accepts what someone is likely to paste: a path relative to the served
   folder, an absolute one that happens to be inside it, or a ~ path. The
   absolute and ~ forms are a convenience for pasting out of a terminal —
   anything landing outside the root still gets refused by the server.

   `~` is the user's home folder as the server reports it (a request path),
   not the served root: they were the same while the root defaulted to the
   home folder, and stopped being the same once it became "/". */
export function interpretPath(typed, rootAbsolute, homeRequest) {
  let text = (typed || "").trim();
  if (!text) return null;
  if (text === "~" || text.startsWith("~/")) {
    const home = (homeRequest || "/").replace(/\/+$/, "");
    text = home + text.slice(1);
  }
  const root = (rootAbsolute || "").replace(/\/+$/, "");
  if (root && text === root) return "/";
  if (root && text.startsWith(root + "/")) text = text.slice(root.length);
  if (!text.startsWith("/")) text = "/" + text;
  return text.replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/";
}
