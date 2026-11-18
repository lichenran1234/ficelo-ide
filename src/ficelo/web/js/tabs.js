/* Open files, as a list and as a bar across the top. */

import { el, basename, store } from "./util.js";

const OPEN_KEY = "ficelo.open";

/* How long a tab takes to slide to a new position. Short enough that the
   close button is under the pointer almost at once, long enough that the
   eye follows it there rather than finding it already arrived. */
export const SLIDE_MS = 130;

export function prefersStillness() {
  return !!(
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/* Boundaries, in days, for fading a tab by how recently its file changed.
   Fine-grained inside the first week because that is where the distinction
   is useful — everything older is simply old. */
export const AGE_DAYS = [1, 2, 3, 5, 7, 30];

export function ageClass(modifiedSeconds, nowMs) {
  if (!modifiedSeconds) return "age-unknown";
  const days = (nowMs - modifiedSeconds * 1000) / 86400000;
  /* A file modified in the future — a bad clock, or a copy that preserved a
     timestamp — is treated as brand new rather than as maximally old. */
  if (days < 0) return "age-0";
  for (let i = 0; i < AGE_DAYS.length; i += 1) {
    if (days < AGE_DAYS[i]) return "age-" + i;
  }
  return "age-" + AGE_DAYS.length;
}

export class Tabs {
  constructor(mount, { onSelect, onClose, onContext, onReorder }) {
    this.mount = mount;
    this.onReorder = onReorder || (() => {});
    this.focused = [];
    this.dragged = null;
    this.onSelect = onSelect;
    this.onClose = onClose;
    this.onContext = onContext || (() => {});
    /* Where the pointer was when a close button was last clicked, so the
       next tab's close button can be slid under it. Cleared when the
       pointer leaves the strip. */
    this.closeSlot = null;
    this.pendingClose = null;
    /* Enough to bring a closed tab back, newest last. Kept small: this is
       an undo for a misclick, not a history. */
    this.recentlyClosed = [];
    this.mount.addEventListener("mouseleave", () => this.releaseCloseSlot());
    this.mount.addEventListener("wheel", () => this.releaseCloseSlot(), { passive: true });
    this.mount.addEventListener("pointerdown", (event) => {
      if (!event.target.closest(".close")) this.releaseCloseSlot(!event.target.closest(".tab"));
    });
    if (typeof ResizeObserver !== "undefined") {
      let width = this.mount.clientWidth;
      this.resizeObserver = new ResizeObserver(() => {
        const next = this.mount.clientWidth;
        if (next !== width) { width = next; this.releaseCloseSlot(); }
      });
      this.resizeObserver.observe(this.mount);
    }
    this.items = [];
    this.active = null;
  }

  get active() { return this._active || null; }
  set active(id) {
    this._active = id;
    if (id) this.focused = [...this.focused.filter((value) => value !== id), id];
  }

  reorder(id, position) {
    const at = this.items.findIndex((tab) => tab.id === id);
    if (at < 0) return;
    this.releaseCloseSlot(false);
    const [tab] = this.items.splice(at, 1);
    this.items.splice(Math.max(0, position > at ? position - 1 : position), 0, tab);
    this.onReorder(); this.draw();
  }

  get current() {
    return this.items.find((tab) => tab.id === this.active) || null;
  }

  /* Identified by `id`, not by `path`. For a file tab the two are the same,
     but a diff tab is "this file, at this revision" and two of them can share
     a path — and did, briefly, which made a diff tab impossible to find again
     once it had been opened. */
  find(id) {
    return this.items.find((tab) => tab.id === id) || null;
  }

  add(id, extra) {
    let tab = this.find(id);
    if (!tab) {
      this.releaseCloseSlot(false);
      tab = Object.assign({ id, path: id, name: basename(id), loaded: false }, extra || {});
      this.items.push(tab);
    }
    return tab;
  }

  /* -- reopening, reordering, reaching ------------------------------------ */

  /* The path to reopen and where it sat, or null. */
  takeLastClosed() {
    const last = this.recentlyClosed.pop() || null;
    this.persistClosed();
    return last;
  }

  setWorkspace(id) {
    if (this.closedKey !== "ficelo.closed." + id) this.releaseCloseSlot(false);
    this.closedKey = "ficelo.closed." + id;
    try {
      const saved = JSON.parse(sessionStorage.getItem(this.closedKey) || "[]");
      this.recentlyClosed = Array.isArray(saved) ? saved.filter((item) =>
        item && typeof item.path === "string" && item.path.startsWith("/") &&
        Number.isInteger(item.at) && item.at >= 0 &&
        (!item.kind || ["diff", "commit"].includes(item.kind))).slice(-20) : [];
    } catch (_) { this.recentlyClosed = []; }
  }

  persistClosed() {
    if (!this.closedKey) return;
    try { sessionStorage.setItem(this.closedKey, JSON.stringify(this.recentlyClosed)); } catch (_) { /* Storage may be full or disabled. */ }
  }

  /* Move the active tab one place left or right. Returns whether it moved,
     so a shortcut at the end of the strip does nothing rather than wrapping
     — wrapping loses your place when you are nudging a tab into position. */
  move(id, delta) {
    const at = this.items.findIndex((tab) => tab.id === id);
    if (at < 0) return false;
    const to = at + delta;
    if (to < 0 || to >= this.items.length) return false;
    this.releaseCloseSlot(false);
    const [tab] = this.items.splice(at, 1);
    this.items.splice(to, 0, tab);
    return true;
  }

  /* Step through the tabs, wrapping — here wrapping is what you want, since
     the point is to cycle. */
  neighbour(delta) {
    if (!this.items.length) return null;
    const at = this.items.findIndex((tab) => tab.id === this.active);
    const to = (at + delta + this.items.length) % this.items.length;
    return this.items[to].id;
  }

  /* 1-based, and 9 means the last tab however many there are — which is what
     every browser does and what people reach for. */
  nth(index) {
    if (!this.items.length) return null;
    if (index >= 9) return this.items[this.items.length - 1].id;
    return index <= this.items.length ? this.items[index - 1].id : null;
  }

  close(id) {
    const at = this.items.findIndex((tab) => tab.id === id);
    if (at < 0) return null;
    if (this.pendingClose?.id === id) {
      this.closeSlot = this.pendingClose;
      this.pendingClose = null;
    } else this.releaseCloseSlot(false);
    const [gone] = this.items.splice(at, 1);
    const closed = { path: gone.path, at };
    if (gone.kind) Object.assign(closed, { kind: gone.kind, wholeCommit: gone.wholeCommit,
      rev: gone.rev, repo: gone.repo, base: gone.base });
    if (gone.wholeCommit) closed.name = gone.name;
    if (gone.kind !== "terminal") this.recentlyClosed.push(closed);
    if (this.recentlyClosed.length > 20) this.recentlyClosed.shift();
    this.persistClosed();
    this.focused = this.focused.filter((value) => value !== id);
    if (this.active === id) {
      /* Fall to the right, as every editor does, and left only when the tab
         closed was the last one. */
      const next = this.find(this.focused.at(-1)) || this.items[at] || this.items[at - 1];
      this.active = next ? next.id : null;
    }
    return gone;
  }

  releaseCloseSlot(redraw = true) {
    const held = this.closeSlot !== null || !!this.mount.querySelector(".tab-close-spacer");
    this.closeSlot = null;
    this.pendingClose = null;
    if (redraw && held) this.draw();
  }

  prepareClose(id, button, event) {
    if (event.detail <= 0) { this.releaseCloseSlot(); return null; }
    const box = button.getBoundingClientRect();
    const at = this.items.findIndex((tab) => tab.id === id);
    const next = this.items[at + 1] || this.items[at - 1];
    const pending = {
      id, target: next?.id, last: at === this.items.length - 1,
      x: event.clientX, fraction: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)),
      widths: new Map([...this.mount.querySelectorAll(".tab")].map((node) =>
        [node.dataset.id, node.getBoundingClientRect().width])),
      scroll: this.mount.scrollLeft, extent: this.mount.scrollWidth,
    };
    this.pendingClose = pending;
    return pending;
  }

  holdWidth(node, width) {
    Object.assign(node.style, { width: width + "px", flex: "0 0 " + width + "px",
      minWidth: width + "px", maxWidth: width + "px" });
  }

  remember() {
    /* Only plain file tabs are restored. A diff tab's other side is a
       revision that may no longer exist, or a working tree that has moved
       on, so reopening one from a week ago would show something misleading. */
    const current = this.find(this.active);
    store.set(OPEN_KEY, {
      paths: this.items.filter((tab) => !tab.kind).map((tab) => tab.path),
      active: current && !current.kind ? current.path : null,
    });
  }

  static restored() {
    const saved = store.get(OPEN_KEY, null);
    if (!saved || !Array.isArray(saved.paths)) return { paths: [], active: null };
    return { paths: saved.paths, active: saved.active || null };
  }

  /* Where every tab is now, by id. Taken before the strip is rebuilt so
     the slide can start from wherever things actually were — including
     mid-slide, if one was already running. */
  positions() {
    const found = new Map();
    this.mount.querySelectorAll(".tab").forEach((node) => {
      found.set(node.dataset.id, node.getBoundingClientRect().left);
    });
    return found;
  }

  /* Move each tab back to where it was, then let it travel to where it now
     belongs.

     Tabs shift because an element left the flow, which no CSS transition
     can animate on its own — the layout simply is different on the next
     frame. Measuring before and after and animating the difference is the
     only way to make closing a tab look like movement instead of a jump,
     and it covers every case at once: the neighbour sliding right to meet
     the pointer, the rest sliding left into the gap, the whole strip
     settling back when the pointer leaves, and a tab being reordered. */
  slide(before) {
    if (!before.size || prefersStillness()) return;
    const moved = [];
    this.mount.querySelectorAll(".tab").forEach((node) => {
      const was = before.get(node.dataset.id);
      // A tab that was not there a moment ago has nowhere to travel from.
      // Not covered by a mutation: replacing this with `was = 0` produces a
      // negligible delta in every arrangement tried, so no test can tell
      // the two apart. Kept for its intent, and noted in tools/cases/slide.py.
      if (was === undefined) return;
      const delta = was - node.getBoundingClientRect().left;
      if (Math.abs(delta) < 1) return;
      node.style.transition = "none";
      node.style.transform = "translateX(" + delta + "px)";
      moved.push(node);
    });
    if (!moved.length) return;
    // Read a layout property so the browser commits the start position
    // before the transition is attached; without this the two style
    // changes coalesce and nothing animates.
    void this.mount.offsetWidth;
    for (const node of moved) {
      node.style.transition = "transform " + SLIDE_MS + "ms ease-out";
      node.style.transform = "";
      node.addEventListener(
        "transitionend",
        () => {
          node.style.transition = "";
        },
        { once: true }
      );
    }
  }

  draw() {
    const before = this.positions();
    if (this.closeSlot && !this.find(this.closeSlot.target)) this.releaseCloseSlot(false);
    const slot = this.closeSlot;
    const spacer = slot ? el("div", { className: "tab-close-spacer", ariaHidden: "true" }) : null;
    // Reserve the old extent before the first layout read can clamp scrolling.
    if (spacer) spacer.style.flex = "0 0 " + slot.extent + "px";
    this.mount.replaceChildren(
      ...this.items.map((tab) => {
        const node = el("div", { className: "tab", title: tab.path }, [
          el("span", { className: "label", textContent: tab.name }),
        ]);
        if (slot?.widths.has(tab.id)) this.holdWidth(node, slot.widths.get(tab.id));
        node.draggable = true;
        node.addEventListener("dragstart", (event) => {
          this.releaseCloseSlot(false);
          this.dragged = tab.id;
          event.dataTransfer.setData("application/x-ficelo-tab", tab.id);
          event.dataTransfer.effectAllowed = "move";
          node.classList.add("is-dragging");
        });
        node.addEventListener("dragover", (event) => {
          if (!this.dragged) return;
          event.preventDefault();
          node.classList.toggle("drop-after", event.clientX > node.getBoundingClientRect().left + node.offsetWidth / 2);
          node.classList.add("is-drop-target");
        });
        node.addEventListener("dragleave", () => node.classList.remove("is-drop-target"));
        node.addEventListener("drop", (event) => {
          if (!this.dragged) return;
          event.preventDefault(); event.stopPropagation();
          const to = this.items.indexOf(tab) + (event.clientX > node.getBoundingClientRect().left + node.offsetWidth / 2 ? 1 : 0);
          this.reorder(this.dragged, to); this.dragged = null;
        });
        node.addEventListener("dragend", () => { this.dragged = null; this.draw(); });
        node.dataset.path = tab.path;
        node.dataset.id = tab.id;
        node.setAttribute("role", "tab");
        if (tab.id === this.active) node.setAttribute("aria-selected", "true");
        if (tab.dirty) node.classList.add("is-dirty");
        if (tab.kind) node.classList.add("is-" + tab.kind);
        else node.classList.add(ageClass(tab.modified, Date.now()));
        node.addEventListener("click", () => this.onSelect(tab.id));
        node.addEventListener("contextmenu", (event) => {
          event.preventDefault();
          this.releaseCloseSlot();
          this.onContext(event, tab);
        });
        /* Middle-click closes, which is muscle memory from every browser. */
        node.addEventListener("auxclick", (event) => {
          if (event.button === 1) {
            event.preventDefault();
            this.releaseCloseSlot();
            this.onClose(tab.id);
          }
        });

        const shut = el("button", {
          className: "close",
          textContent: tab.dirty ? "●" : "×",
          title: tab.dirty ? "Unsaved changes" : "Close",
        });
        shut.addEventListener("click", async (event) => {
          event.stopPropagation();
          /* Recorded before the close, because afterwards this button is
             gone and its position with it. */
          const pending = this.prepareClose(tab.id, shut, event);
          try { await this.onClose(tab.id); }
          finally {
            // Canceled closes never activate a layout hold. Do not clear a
            // newer close that happened while this request was awaiting input.
            if (pending && this.pendingClose === pending) this.releaseCloseSlot();
          }
        });
        node.append(shut);
        return node;
      }),
      ...(spacer ? [spacer] : [])
    );
    /* Before the slide, because it changes the final layout and the slide
       has to animate towards the real destination. */
    this.applyCloseSlot(spacer);
    this.slide(before);
  }

  /* Slide exactly one tab, so closing several in a row feels like each one
     stepping up to the pointer rather than the whole strip lurching. */
  applyCloseSlot(spacer) {
    const slot = this.closeSlot;
    if (!slot || !spacer) return;
    const node = [...this.mount.querySelectorAll(".tab")].find((tab) => tab.dataset.id === slot.target);
    const button = node.querySelector(".close");
    this.mount.scrollLeft = slot.scroll;
    const box = button.getBoundingClientRect();
    const delta = slot.x - slot.fraction * box.width - box.left;
    if (slot.last) node.style.marginLeft = Math.max(0, delta) + "px";
    else {
      const width = Math.max(0, node.getBoundingClientRect().width + delta);
      this.holdWidth(node, width);
      slot.widths.set(slot.target, width);
    }
    const last = [...this.mount.querySelectorAll(".tab")].at(-1);
    const end = last.getBoundingClientRect().right - this.mount.getBoundingClientRect().left
      + this.mount.scrollLeft - this.mount.clientLeft;
    spacer.style.flex = "0 0 " + Math.max(0, slot.extent - end) + "px";
    this.mount.scrollLeft = slot.scroll;
  }
}
