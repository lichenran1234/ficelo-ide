/* One scrollable document per commit, with lazy Monaco sections. */
import { el } from "./util.js";
import { editorTheme } from "./monaco.js";
import { createDiffEditor, orderedFiles, readLayout, readMap, setDiffModel } from "./review.js";

export class CommitView {
  constructor(monaco, host, { dark = false, onSection, onChanged, onCopyPath } = {}) {
    this.monaco = monaco;
    this.host = host;
    this.onSection = onSection || (() => {});
    this.onChanged = onChanged || (() => {});
    this.onCopyPath = onCopyPath || (() => {});
    this.options = { dark, layout: readLayout(), map: readMap(), fontSize: 13 };
    this.sections = [];
    this.cancelEdge = (event) => {
      if (event.type !== "keydown" || ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) this.edge = null;
    };
    this.scroll = () => {
      if (this.edge && Math.abs(this.host.scrollTop - this.edgeTop) > 1) {
        const limit = Math.max(0, this.host.scrollHeight - this.host.clientHeight);
        if (Math.abs(limit - this.edgeLimit) > 1) this.alignEdge();
        else this.edge = null;
      }
      if (this.layoutAnchor && Math.abs(this.host.scrollTop - this.alignedTop) > 1) this.layoutAnchor = null;
      if (this.frame) return;
      this.frame = requestAnimationFrame(() => { this.frame = null; this.spy();
        clearTimeout(this.changedTimer);
        this.changedTimer = setTimeout(() => this.onChanged(this.snapshot()), 160); });
    };
    host.addEventListener("scroll", this.scroll, { passive: true });
    for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) host.addEventListener(type, this.cancelEdge, { passive: true });
    this.resizeObserver = new ResizeObserver(() => this.alignEdge());
  }

  show(tab) {
    if (this.tab === tab && this.sections.length) return;
    this.clear();
    this.tab = tab;
    const data = tab.commitData;
    if (!data) throw new Error("Commit contents have not been loaded");
    const commit = data.commit;
    this.state = tab.commitViewState || {};
    this.folded = new Set(this.state.folded || []);
    this.host.classList.add("commit-document");
    this.header = el("header", { className: "commit-banner" }, [
      el("strong", { textContent: commit.subject }),
      el("span", { textContent: `${commit.short} · ${commit.author} · ${commit.date ? new Date(commit.date).toLocaleString() : ""}` }),
    ]);
    this.host.replaceChildren(this.header);
    this.resizeObserver.observe(this.host);
    this.resizeObserver.observe(this.header);
    if (data.merge) {
      this.host.append(el("p", { className: "notice", textContent: "This merge has multiple parents. Review the commits it merged to inspect their individual changes." }));
      return;
    }
    const entries = orderedFiles(data.files || []);
    if (!entries.length) this.host.append(el("p", { className: "notice", textContent: "This commit has no changed files in the folder being served." }));
    for (const entry of entries) {
      const key = entry.request || entry.path;
      const heading = el("button", { className: "commit-file-heading", title: entry.from ? `${entry.from} → ${entry.path}` : entry.path }, [
        el("span", { className: "twisty" }),
        el("span", { className: "change-status", textContent: entry.status }),
        el("span", { className: "commit-file-name", textContent: entry.path }),
        el("span", { className: "stat-add", textContent: entry.added == null ? "" : "+" + entry.added }),
        el("span", { className: "stat-del", textContent: entry.removed == null ? "" : "−" + entry.removed }),
      ]);
      const copy = el("button", { className: "mini review-copy-path", textContent: "Copy", title: "Copy path in repository", ariaLabel: "Copy path in repository" });
      copy.addEventListener("click", () => this.onCopyPath(entry));
      const headingRow = el("div", { className: "commit-file-heading-row" }, [heading, copy]);
      const content = el("div", { className: "commit-file-content" });
      const section = el("section", { className: "commit-file" }, [headingRow, content]);
      section.dataset.path = key;
      const item = { entry, key, section, heading, content, editor: null, models: null, disposables: [] };
      if (entry.binary || entry.truncated) content.append(el("p", { className: "notice", textContent: entry.notice || (entry.binary ? "Binary file changed." : "This file is too large to display in this commit.") }));
      else content.style.height = "180px";
      this.sections.push(item);
      heading.addEventListener("click", () => this.toggle(item));
      this.fold(item);
      this.host.append(section);
      this.resizeObserver.observe(section);
    }
    this.tail = el("div", { className: "commit-end-space" });
    this.host.append(this.tail);
    this.observer = new IntersectionObserver((entries) => {
      /* An already queued delivery can outlive this view's visible tab.
         Creating Monaco editors here would also reset the global theme. */
      if (!this.host.isConnected || !this.host.getClientRects().length || this.host.closest("[inert]")) return;
      for (const entry of entries) if (entry.isIntersecting) {
        const item = this.sections.find((item) => item.section === entry.target);
        if (item) this.ensureEditor(item);
      }
    }, { root: this.host, rootMargin: "600px" });
    for (const item of this.sections) this.observer.observe(item.section);
    this.host.scrollTop = Number(this.state.scrollTop) || 0;
    if (this.state.active) this.jump(this.state.active, false, this.state.offset || 0);
    else this.spy();
  }

  editorOptions() {
    return { renderSideBySide: this.options.layout !== "inline", minimap: { enabled: this.options.map },
      renderOverviewRuler: this.options.map, fontSize: this.options.fontSize,
      theme: editorTheme(this.options.dark), readOnly: true, padding: { top: 6, bottom: 6 },
      scrollbar: { alwaysConsumeMouseWheel: false }, scrollBeyondLastLine: false };
  }

  resume() {
    // Visibility preserves layout, so intersections need a fresh delivery
    // when this document becomes active after an earlier delivery was ignored.
    for (const item of this.sections) {
      this.observer?.unobserve(item.section);
      this.observer?.observe(item.section);
    }
  }

  ensureEditor(item) {
    if (item.editor || item.entry.binary || item.entry.truncated || this.folded.has(item.key)) return;
    const editor = createDiffEditor(this.monaco, item.content, this.options.dark);
    item.editor = editor;
    editor.updateOptions(this.editorOptions());
    item.models = setDiffModel(this.monaco, editor, {
      ...item.entry, name: item.entry.path.split("/").pop(), path: item.key,
      repo: this.tab.repo, base: this.tab.base, rev: this.tab.rev,
      modelScope: "document:" + (this.tab.id || this.tab.rev),
    });
    const resize = () => {
      const before = item.content.style.height;
      const height = Math.max(100, Math.min(24000, Math.max(editor.getOriginalEditor().getContentHeight(), editor.getModifiedEditor().getContentHeight())));
      item.content.style.height = height + "px";
      if (before !== item.content.style.height) editor.layout();
      this.reserveTail();
      if (this.edge) this.alignEdge();
      else if (this.layoutAnchor) this.align(this.layoutAnchor.item, this.layoutAnchor.offset);
    };
    item.disposables.push(editor.onDidUpdateDiff(() => {
      resize();
      if (!item.restored && this.state.editors?.[item.key]) {
        editor.restoreViewState(this.state.editors[item.key]);
        item.restored = true;
      }
      if (this.pendingJump?.key === item.key) { const offset = this.pendingJump.offset; this.pendingJump = null; this.align(item, offset); }
      if (this.edge) this.alignEdge();
    }));
    item.disposables.push(editor.getModifiedEditor().onDidContentSizeChange(resize));
    item.disposables.push(editor.getOriginalEditor().onDidContentSizeChange(resize));
    resize();
  }

  fold(item) {
    const folded = this.folded.has(item.key);
    item.content.hidden = folded;
    item.heading.setAttribute("aria-expanded", String(!folded));
    item.heading.firstChild.textContent = folded ? "▸" : "▾";
  }

  toggle(item) {
    this.edge = null;
    this.layoutAnchor = null;
    const top = item.heading.getBoundingClientRect().top;
    if (this.folded.has(item.key)) this.folded.delete(item.key); else this.folded.add(item.key);
    this.fold(item);
    this.ensureEditor(item);
    this.host.scrollTop += item.heading.getBoundingClientRect().top - top;
    this.spy();
    this.onChanged(this.snapshot());
  }

  reserveTail() {
    if (!this.tail || !this.header || !this.sections.length) return;
    const last = this.sections[this.sections.length - 1].section;
    this.tail.style.height = Math.max(0, this.host.clientHeight - this.header.getBoundingClientRect().height - last.getBoundingClientRect().height) + "px";
  }

  align(item, within = 0) {
    this.reserveTail();
    const first = this.sections[0] === item;
    const offset = item.section.getBoundingClientRect().top - this.host.getBoundingClientRect().top;
    this.host.scrollTop = (first ? 0 : this.host.scrollTop + offset - this.header.getBoundingClientRect().height) + within;
    this.alignedTop = this.host.scrollTop;
    this.spy();
  }

  scrollToEdge(edge) {
    if (edge !== "top" && edge !== "bottom") return;
    this.pendingJump = null;
    this.layoutAnchor = null;
    cancelAnimationFrame(this.jumpFrame);
    this.jumpFrame = null;
    this.edge = edge;
    this.alignEdge();
    this.onChanged(this.snapshot());
  }

  alignEdge() {
    if (!this.edge || !this.host.isConnected || !this.host.getClientRects().length || this.host.closest("[inert]")) return;
    this.reserveTail();
    const bottom = this.edge === "bottom";
    const item = this.sections[bottom ? this.sections.length - 1 : 0];
    // Only the boundary editor needs its inner scrollbar moved. Its normal
    // intersection callback creates it; intermediate sections stay lazy.
    if (item?.editor && !this.folded.has(item.key)) {
      for (const editor of [item.editor.getOriginalEditor(), item.editor.getModifiedEditor()]) {
        editor.setScrollTop(bottom ? editor.getScrollHeight() : 0, this.monaco.editor.ScrollType.Immediate);
      }
    }
    this.host.scrollTop = bottom ? this.host.scrollHeight : 0;
    this.edgeTop = this.host.scrollTop;
    this.edgeLimit = Math.max(0, this.host.scrollHeight - this.host.clientHeight);
    this.spy();
  }

  jump(path, unfold = true, offset = 0) {
    this.edge = null;
    this.layoutAnchor = null;
    cancelAnimationFrame(this.jumpFrame);
    const item = this.sections.find((item) => item.key === path || item.entry.path === path);
    if (!item) return;
    if (unfold) { this.folded.delete(item.key); this.fold(item); }
    // Never rebuild a document to move to another file: selection, expanded
    // context, and the editors' view states remain intact.
    this.pendingJump = item.editor ? null : { key: item.key, offset };
    this.ensureEditor(item);
    this.align(item, offset);
    this.jumpFrame = requestAnimationFrame(() => {
      this.jumpFrame = null;
      if (this.tab && item.section.isConnected) this.align(item, offset);
    });
  }

  spy() {
    if (!this.header || !this.sections.length) return;
    const edge = this.header.getBoundingClientRect().bottom + 3;
    let active = this.sections[0];
    for (const item of this.sections) {
      if (item.heading.getBoundingClientRect().top <= edge) active = item; else break;
    }
    if (active.key !== this.active) {
      this.active = active.key;
      this.onSection(active.key, this.tab.rev);
    }
  }

  snapshot() {
    const editors = { ...(this.state?.editors || {}) };
    for (const item of this.sections) if (item.editor) editors[item.key] = item.editor.saveViewState();
    const active = this.sections.find((item) => item.key === this.active);
    const offset = !active || active === this.sections[0] ? this.host.scrollTop
      : this.header.getBoundingClientRect().height - (active.section.getBoundingClientRect().top - this.host.getBoundingClientRect().top);
    return { scrollTop: this.host.scrollTop, active: this.active, offset, folded: [...(this.folded || [])], editors };
  }

  setOptions(options) {
    if (!this.edge && ["layout", "fontSize"].some((key) => options[key] !== undefined && options[key] !== this.options[key])) {
      const saved = this.snapshot();
      const item = this.sections.find((section) => section.key === saved.active);
      if (item) this.layoutAnchor = { item, offset: saved.offset };
    }
    Object.assign(this.options, options);
    for (const item of this.sections) if (item.editor) item.editor.updateOptions(this.editorOptions());
    if (this.edge) this.alignEdge();
    else if (this.layoutAnchor) this.align(this.layoutAnchor.item, this.layoutAnchor.offset);
  }

  clear() {
    this.observer?.disconnect();
    this.resizeObserver.disconnect();
    cancelAnimationFrame(this.jumpFrame);
    this.jumpFrame = null;
    for (const item of this.sections) {
      item.disposables.forEach((disposable) => disposable.dispose());
      item.editor?.dispose();
      item.models?.original.dispose();
      item.models?.modified.dispose();
    }
    this.sections = [];
    this.active = null;
    this.pendingJump = null;
    this.layoutAnchor = null;
    this.edge = null;
  }

  dispose() {
    this.clear();
    cancelAnimationFrame(this.frame);
    clearTimeout(this.changedTimer);
    this.host.removeEventListener("scroll", this.scroll);
    for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) this.host.removeEventListener(type, this.cancelEdge);
    this.host.classList.remove("commit-document");
    this.tab = null;
  }
}
