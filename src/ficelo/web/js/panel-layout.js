import { store } from "./util.js";
import { wireResize } from "./panel-resize.js";

const WIDTHS_KEY = "ficelo.panelwidths";
const clamp = (value, { min, max }) => Math.max(min, Math.min(max, value));

// Monaco sizes the minimap in miniature character columns. Choose the
// smallest supported scale that can fit the requested width, then let its
// native layout leave enough room for the code and scrollbars.
function minimapSizing(editor, options) {
  const layout = editor.getLayoutInfo();
  const ratio = editor.getOption(options.pixelRatio);
  const character = editor.getOption(options.fontInfo).typicalHalfwidthCharacterWidth;
  const remaining = layout.contentWidth + layout.minimap.minimapWidth;
  const unit = (scale) => (ratio >= 2 ? Math.round(scale * 2) : scale) / ratio;
  const capacity = (scale) => Math.max(0, Math.floor(
    (remaining - layout.verticalScrollbarWidth - 2) * unit(scale) / (character + unit(scale)))) + 8;
  return { unit, capacity };
}

export class PanelLayout {
  constructor(editor, monaco, onLayout = () => {}) {
    this.editor = editor;
    this.options = monaco.editor.EditorOption;
    this.onLayout = onLayout;
    this.layout = document.querySelector(".layout");
    this.sidebar = document.getElementById("sidebar");
    this.contents = document.getElementById("tocPane");
    this.preview = document.getElementById("previewHost");
    this.editorHost = document.getElementById("editorHost");
    this.handles = Object.fromEntries(["tree", "contents", "minimap"].map((name) =>
      [name, document.getElementById(name + "Resize")]));
    const saved = store.get(WIDTHS_KEY, {}) || {};
    this.widths = {};
    this.previewWidths = {};
    const initialMap = editor.getOption(this.options.minimap);
    this.defaultMinimap = { size: initialMap.size, scale: initialMap.scale, maxColumn: initialMap.maxColumn };
    for (const [name, fallback] of Object.entries({ tree: 280, contents: 220, minimap: null })) {
      this.widths[name] = typeof saved[name] === "number" && Number.isFinite(saved[name]) && saved[name] >= 8
        ? Math.min(1000, saved[name]) : fallback;
    }
    this.controls = Object.entries(this.handles).map(([name, handle]) => wireResize(handle, {
      direction: name === "tree" ? 1 : -1,
      getWidth: () => this.width(name),
      getBounds: () => this.bounds(name),
      onResize: (width) => { this.previewWidths[name] = width; this.refresh(); },
      onCancel: () => { delete this.previewWidths[name]; this.refresh(); },
      onEnd: (width) => {
        this.widths[name] = width;
        delete this.previewWidths[name];
        store.set(WIDTHS_KEY, { ...store.get(WIDTHS_KEY, {}), [name]: width });
        this.refresh();
      },
    }));
    this.observer = new ResizeObserver(() => this.refresh());
    for (const node of [this.layout, this.preview, this.editorHost]) this.observer.observe(node);
    this.editorLayout = editor.onDidLayoutChange(() => this.refresh());
    this.refresh();
  }

  width(name) {
    if (name === "minimap") return this.editor.getLayoutInfo().minimap.minimapWidth;
    return (name === "tree" ? this.sidebar : this.contents).getBoundingClientRect().width || this.widths[name];
  }

  bounds(name) {
    if (name === "tree") return { min: 160, max: Math.max(160, Math.min(600, this.layout.clientWidth - 280)) };
    if (name === "contents") {
      const width = this.preview.clientWidth || this.preview.parentElement.clientWidth;
      return { min: 120, max: Math.max(120, Math.min(500, width / 2)) };
    }
    const max = Math.min(300, minimapSizing(this.editor, this.options).capacity(3));
    return { min: Math.min(40, max), max };
  }

  refresh() {
    if (this.refreshing || this.destroyed) return;
    this.refreshing = true;
    try {
      for (const [name, node] of [["tree", this.sidebar], ["contents", this.contents]]) {
        const width = Math.round(clamp(this.previewWidths[name] ?? this.widths[name], this.bounds(name))) + "px";
        if (node.style.width !== width) node.style.width = width;
      }
      const layout = this.editor.getLayoutInfo();
      if (!this.editorHost.hidden && (layout.width !== this.editorHost.clientWidth || layout.height !== this.editorHost.clientHeight)) {
        this.editor.layout();
      }
      const minimap = this.editor.getOption(this.options.minimap);
      if (!this.editorHost.hidden && minimap.enabled) {
        let options = this.defaultMinimap;
        const desired = this.previewWidths.minimap ?? this.widths.minimap;
        if (desired !== null) {
          const sizing = minimapSizing(this.editor, this.options);
          const scale = [1, 2, 3].find((value) => sizing.capacity(value) >= desired) || 3;
          const maxColumn = Math.max(1, Math.min(10000, Math.round(desired / sizing.unit(scale))));
          options = { size: "proportional", scale, maxColumn };
        }
        if (Object.entries(options).some(([key, value]) => minimap[key] !== value)) {
          this.editor.updateOptions({ minimap: options });
        }
      }
      this.handles.tree.hidden = this.sidebar.hidden;
      this.handles.contents.hidden = this.preview.hidden || this.contents.hidden;
      const map = this.editor.getLayoutInfo().minimap;
      this.handles.minimap.hidden = this.editorHost.hidden || !minimap.enabled || map.minimapWidth <= 0;
      this.handles.minimap.style.left = (this.editorHost.offsetLeft + map.minimapLeft - 3) + "px";
      this.controls.forEach((control) => control.sync());
      this.onLayout();
    } finally { this.refreshing = false; }
  }

  cancel() { this.controls.forEach((control) => control.cancel()); }

  destroy() {
    this.controls.forEach((control) => control.destroy());
    this.observer.disconnect();
    this.editorLayout.dispose();
    this.destroyed = true;
  }
}
