/* Named sets of open tabs.

   A working day tends to be two or three unrelated pieces of work, each
   with its own handful of files. Sessions keep those apart: start a new
   one, and the old set is still there to come back to.

   Settings live in ~/.ficelo/settings.json; failed saves can also be recovered
   from temporary metadata kept in this browser. The functions that decide anything are
   pure and take the whole store, so they can be tested without a browser
   pretending to have one. */

import { store, el, recoverySnapshots } from "./util.js";

const SESSIONS_KEY = "ficelo.sessions"; // migration from the original list
const PREFIX = "ficelo.workspace.";
const CURRENT_KEY = "ficelo.current";
let current = null;
const issuedIds = new Set();
const ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

export const MAX_SESSIONS = 50;
export function newId() {
  // Keep deleted and redirected workspaces reserved, along with unsaved IDs.
  const used = new Set(allRecords().map((record) => record.id));
  const bytes = new Uint8Array(4);
  let id;
  do {
    if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    // Reject the four leftover byte values so each character is equally likely.
    id = Array.from(bytes).filter((byte) => byte < 252).map((byte) => ID_ALPHABET[byte % 36]).join("");
  } while (id.length !== 4 || used.has(id) || issuedIds.has(id) || id === current);
  issuedIds.add(id);
  return id;
}
export function canonical(id) {
  const seen = new Set();
  while (id && !seen.has(id)) {
    seen.add(id);
    const next = store.get(PREFIX + id, null)?.redirect;
    if (!next) return id;
    id = next;
  }
  return null;
}
export function allRecords() {
  const legacy = store.get(SESSIONS_KEY, []);
  const entries = new Map((Array.isArray(legacy) ? legacy : [])
    .filter((s) => s && typeof s.id === "string").map((s) => [s.id, s]));
  for (const [key, value] of store.entries()) {
    if (key.startsWith(PREFIX) && value) entries.set(key.slice(PREFIX.length), { ...value, id: key.slice(PREFIX.length) });
  }
  return [...entries.values()];
}
export function readAll() { return allRecords().filter((s) => !s.redirect && !s.deletedAt); }
function withLegacyState(record) {
  const legacy = store.get(SESSIONS_KEY, []);
  if (!store.get(PREFIX + record.id, null) && Array.isArray(legacy) && legacy.some((s) => s?.id === record.id)) {
    const tabs = tabRecords(record);
    return { ...record, tabs: Object.fromEntries(tabs.map((tab) => [tab.id, tab])), order: record.order || tabs.map((tab) => tab.id),
      root: record.root || store.legacyState()["ficelo.treeroot"] || null, state: { ...store.legacyState(), ...record.state } };
  }
  return record;
}
export function migrate(id) {
  const record = readAll().find((entry) => entry.id === id);
  if (record && !store.get(PREFIX + id, null)) save(record, { activity: false });
  return readAll().find((entry) => entry.id === id);
}
export function save(record, { activity = true } = {}) {
  record = withLegacyState(record);
  if (activity && JSON.stringify(record) !== JSON.stringify(store.get(PREFIX + record.id, null))) {
    record = { ...record, at: Math.max(Date.now(), record.at || 0) };
  }
  store.set(PREFIX + record.id, record);
  const excess = ordered(readAll()).filter((entry) => !entry.starred).slice(MAX_SESSIONS);
  for (const entry of excess) store.set(PREFIX + entry.id, { ...entry, deletedAt: Date.now() });
}
// A browser tab becoming visible changes only activity, never its saved tabs.
export function markActive(id = currentId(), at = Date.now()) {
  const record = store.get(PREFIX + id, null);
  if (!record || record.deletedAt || record.redirect || at <= (record.at || 0)) return;
  store.set(PREFIX + id, { ...record, at });
}
export function writeAll(records) {
  // Compare against one snapshot: saving a changed record may prune another.
  // Replaying that unchanged record afterward would restore it as new activity.
  const previous = new Map(readAll().map((record) => [record.id, record]));
  for (const record of records) {
    if (JSON.stringify(record) !== JSON.stringify(previous.get(record.id))) save(record);
  }
  for (const old of readAll()) if (!records.some((r) => r.id === old.id)) remove(old.id);
}
export function currentId() { return canonical(current); }
export function setCurrentId(id) {
  current = canonical(id) || id;
  try { sessionStorage.setItem(CURRENT_KEY, current); } catch (_) {}
  store.scope(current);
}
export function initialize() {
  const url = new URL(location.href);
  let prior;
  try { prior = sessionStorage.getItem(CURRENT_KEY); } catch (_) {}
  const requested = url.searchParams.get("session") || url.searchParams.get("copy");
  const candidate = canonical(requested || prior || store.get("ficelo.session", null));
  const found = migrate(candidate);
  const id = found && !url.searchParams.has("copy") ? candidate : newId();
  if (found && id !== candidate) save({ ...found, id, name: found.name ? found.name + " (copy)" : "", at: Date.now() });
  if (found && !store.get(PREFIX + id, null)) save({ ...found, id, root: found.root || store.get("ficelo.treeroot", null), state: { ...store.legacyState(), ...found.state } });
  setCurrentId(id);
  url.searchParams.delete("copy");
  url.searchParams.set("session", id);
  history.replaceState(null, "", url);
  return found ? readAll().find((entry) => entry.id === id) : null;
}
export function remove(id) {
  const record = readAll().find((s) => s.id === id);
  if (record) save({ ...record, deletedAt: Date.now() });
}
export function restore(id) {
  const record = allRecords().find((s) => s.id === id);
  if (!record) return;
  delete record.deletedAt;
  save(record);
}
export function tabRecords(session) {
  const paths = Array.isArray(session.paths) ? session.paths.filter((path) => typeof path === "string") : [];
  const tabs = session.tabs && typeof session.tabs === "object" ? session.tabs : Object.fromEntries(paths.map((path) => [path, { id: path, path }]));
  const ids = [...new Set([...(Array.isArray(session.order) ? session.order : []), ...Object.keys(tabs)])];
  return ids.map((id) => tabs[id]).filter((tab) => tab && typeof tab.id === "string" && typeof tab.path === "string");
}
/* Copying missing tabs leaves both workspace identities intact. Matching
   logical views also handles older sessions that assigned different IDs. */
export function missingTabs(target, source) {
  const identity = (tab) => JSON.stringify([tab.kind || "file", tab.path,
    tab.kind ? tab.repo || "" : "", tab.base || "", tab.rev || "", !!tab.wholeCommit]);
  const currentTabs = tabRecords(target);
  const seen = new Set(currentTabs.map(identity));
  const ids = new Set(currentTabs.map((tab) => tab.id));
  const result = [];
  for (const tab of tabRecords(source)) {
    const key = identity(tab);
    if (seen.has(key)) continue;
    seen.add(key);
    let id = tab.id;
    while (ids.has(id)) id = newId();
    ids.add(id); result.push({ ...tab, id });
  }
  return result;
}
export function restoreRecovery(key) {
  const entry = recoverySnapshots().find((entry) => entry.key === key);
  if (!entry) return null;
  let id = newId();
  while (allRecords().some((record) => record.id === id)) id = newId();
  save({ ...entry.record, id, name: describe(entry.record) + " (recovered)", at: Date.now() });
  return id;
}
export function link(id, copy = false) {
  const url = new URL(location.href);
  if (id !== currentId()) {
    url.hash = "";
    for (const key of ["path", "review", "base", "sha"]) url.searchParams.delete(key);
  }
  url.searchParams.delete(copy ? "session" : "copy");
  url.searchParams.set(copy ? "copy" : "session", canonical(id) || id);
  return url.href;
}

/* Starred first, then most recently touched. The ordering the list is shown
   in, and the ordering pruning walks backwards through. */
export function ordered(sessions) {
  return sessions.slice().sort((a, b) => {
    if (!!b.starred !== !!a.starred) return b.starred ? 1 : -1;
    return (b.at || 0) - (a.at || 0);
  });
}

export function activityAge(at, now = Date.now()) {
  const minutes = Math.floor(Math.max(0, now - (Number(at) || 0)) / 60000);
  if (minutes < 1) return "less than 1 min ago";
  if (minutes < 60) return minutes + " min ago";
  if (minutes < 1440) return Math.floor(minutes / 60) + " h ago";
  if (minutes < 365 * 1440) return Math.floor(minutes / 1440) + " day ago";
  return "> 1 year ago";
}

export function updateActivity(mount, records = allRecords(), now = Date.now()) {
  const activity = new Map(records.map((record) => [record.id, record]));
  for (const label of mount.querySelectorAll(".session-activity")) {
    const record = activity.get(label.dataset.sessionId);
    label.textContent = activityAge(label.classList.contains("session-deleted-age") ? record?.deletedAt : record?.at, now);
  }
}

export function prune(sessions) {
  const keep = [];
  let unstarred = 0;
  for (const session of ordered(sessions)) {
    if (session.starred) {
      keep.push(session);
      continue;
    }
    unstarred += 1;
    if (unstarred <= MAX_SESSIONS) keep.push(session);
  }
  return keep;
}

/* A name for a session that has not been given one: the files in it, which
   is what someone actually recognises it by. */
export function describe(session) {
  if (session.name && session.name.trim()) return session.name.trim();
  const names = tabRecords(session).map((tab) => tab.name || tab.path.split("/").pop());
  if (!names.length) return "Empty session";
  if (names.length <= 2) return names.join(", ");
  return names[0] + " and " + (names.length - 1) + " more";
}

export function upsert(sessions, session) {
  const at = sessions.findIndex((other) => other.id === session.id);
  const next = sessions.slice();
  if (at < 0) next.push(session);
  else next[at] = session;
  return next;
}

/* -- the dropdown ---------------------------------------------------------- */

function sessionIcon(name) {
  const shapes = {
    star: '<path d="m8 1.8 1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6Z"/>',
    add: '<path d="M8 3v10M3 8h10"/>',
    more: '<g fill="currentColor" stroke="none"><circle cx="3" cy="8" r="1.2"/><circle cx="8" cy="8" r="1.2"/><circle cx="13" cy="8" r="1.2"/></g>',
    remove: '<path d="m4 4 8 8M12 4l-8 8"/>',
  };
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 16 16", fill: "none", stroke: "currentColor",
    "stroke-width": "1.5", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" })) {
    icon.setAttribute(key, value);
  }
  icon.innerHTML = shapes[name];
  return icon;
}

export function buildList(mount, records, activeId, actions) {
  const query = mount.querySelector(".session-search")?.value || "";
  const search = el("input", { className: "session-search", placeholder: "Search sessions and paths", value: query });
  const list = el("div", { className: "session-rows" });
  const toolbar = el("div", { className: "session-tools" });
  const selected = new Set();
  const merge = el("button", { textContent: "Merge selected", disabled: true });
  merge.addEventListener("click", () => actions.onMerge([...selected]));
  const groupChoice = el("select", { className: "session-group-choice", ariaLabel: "Group selected sessions", disabled: true }, [
    el("option", { value: "", textContent: "Group selected…", disabled: true, selected: true }),
    el("option", { value: " ", textContent: "No group" }),
    ...[...new Set(records.map((record) => record.group).filter(Boolean))].sort().map((name) => el("option", { value: name, textContent: name })),
  ]);
  groupChoice.addEventListener("change", () => {
    for (const id of selected) {
      const record = readAll().find((entry) => entry.id === id);
      if (record) save({ ...record, group: groupChoice.value.trim() });
    }
    actions.onRefresh();
  });
  toolbar.append(groupChoice, merge);
  const hint = el("small", { className: "session-hint", textContent: "Star sessions to keep them. The 50 most recent unstarred sessions are retained." });
  let trashOpen = mount.querySelector(".session-trash")?.open || false;
  const draw = () => {
    const words = search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const matches = (record) => {
      const text = [record.name, record.group, record.root, ...tabRecords(record).map((tab) => tab.path)].join(" ").toLowerCase();
      return words.every((word) => text.includes(word));
    };
    const matching = ordered(records).filter((record) => record.id === activeId || matches(record));
    const groups = new Map();
    const current = matching.find((record) => record.id === activeId);
    if (current) groups.set("Current session", [current]);
    for (const record of matching) {
      if (record.id === activeId) continue;
      const name = record.group || (record.starred ? "Starred" : "Sessions");
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(record);
    }
    list.replaceChildren();
    for (const [group, entries] of groups) {
      list.append(el("div", { className: "set-title", textContent: group }));
      for (const session of entries) {
        const row = el("div", { className: "session" + (session.id === activeId ? " is-current" : "") });
        const check = el("input", { type: "checkbox", checked: selected.has(session.id), title: "Select for merge" });
        check.addEventListener("change", () => {
          if (check.checked) selected.add(session.id); else selected.delete(session.id);
          merge.disabled = selected.size < 2;
          groupChoice.disabled = !selected.size;
        });
        const star = el("button", { type: "button", className: "session-icon session-star" + (session.starred ? " is-on" : ""),
          title: session.starred ? "Unstar session" : "Star session",
          ariaLabel: (session.starred ? "Unstar " : "Star ") + describe(session) }, [sessionIcon("star")]);
        star.addEventListener("click", () => actions.onStar(session.id));
        const open = el("button", { className: "session-open", title: tabRecords(session).map((t) => t.path).join("\n") }, [
          el("span", { className: "session-name", textContent: describe(session) }),
          el("span", { className: "session-count", textContent: tabRecords(session).length + " tabs" + (session.id === activeId ? " · current" : "") }),
        ]);
        open.addEventListener("click", () => actions.onPick(session.id));
        const more = el("button", { type: "button", className: "session-icon session-more", title: "Session actions",
          ariaLabel: "Actions for " + describe(session) }, [sessionIcon("more")]);
        more.addEventListener("click", (event) => actions.onMenu(event, session));
        const remove = el("button", { type: "button", className: "session-icon session-remove", title: "Delete session",
          ariaLabel: "Delete " + describe(session), disabled: session.id === activeId }, [sessionIcon("remove")]);
        remove.addEventListener("click", () => actions.onDelete(session.id));
        const add = el("button", { type: "button", className: "session-icon session-add", title: "Add missing tabs to the current session",
          ariaLabel: "Add tabs from " + describe(session), disabled: session.id === activeId || !actions.onAdd }, [sessionIcon("add")]);
        add.addEventListener("click", () => actions.onAdd?.(session.id));
        row.append(check, star);
        if (session.id !== activeId) {
          const age = el("span", { className: "session-activity", textContent: activityAge(session.at), title: "Last active" });
          age.dataset.sessionId = session.id;
          row.append(age);
        }
        row.append(open, add, more, remove); list.append(row);
      }
    }
    if (!matching.length || (words.length && !matching.some(matches))) {
      list.append(el("div", { className: "notice", textContent: matching.length ? "No other matching sessions." : "No matching sessions." }));
    }
    const deleted = allRecords().filter((record) => record.deletedAt && matches(record)).sort((a, b) => b.deletedAt - a.deletedAt);
    const trash = el("details", { className: "session-trash", open: trashOpen || (!!words.length && !!deleted.length) }, [el("summary", { textContent: "Recently deleted (" + deleted.length + ")" })]);
    trash.addEventListener("toggle", () => { if (!words.length) trashOpen = trash.open; });
    for (const record of deleted) {
      const name = describe(record);
      const count = tabRecords(record).length;
      const age = el("span", { className: "session-activity session-deleted-age", textContent: activityAge(record.deletedAt), title: "Deleted" });
      age.dataset.sessionId = record.id;
      const button = el("button", { type: "button", className: "session-restore", textContent: "Restore session", ariaLabel: "Restore session " + name });
      button.addEventListener("click", () => { restore(record.id); actions.onRefresh(); });
      trash.append(el("div", { className: "session-deleted" }, [
        age,
        el("span", { className: "session-name", textContent: name, title: name }),
        el("span", { className: "session-count", textContent: count + (count === 1 ? " tab" : " tabs") }),
        button,
      ]));
    }
    list.append(trash);
  };
  search.addEventListener("input", draw);
  mount.replaceChildren(search, toolbar, hint, list); draw();
  return mount;
}
