/* Appearance, and where you were in each file.

   Both are preferences rather than state, so both live in ~/.ficelo/ and
   neither is worth an error if they cannot be saved.

   Prose and code want different sizes — 17px reading text next to 13px
   monospace is normal, and one shared number makes one of them wrong. So
   the sizes are separate, and grouped the way they are used. */

import { store, el } from "./util.js";
import { grayColor, parseGrayColor } from "./reading-background.js";
import { applyInterfaceBackground } from "./interface-background.js";

const APPEARANCE_KEY = "ficelo.appearance";
const SCROLL_KEY = "ficelo.scroll";
const TOC_KEY = "ficelo.toc";
const MAX_REMEMBERED_SCROLLS = 300;

/* A content width that means "as wide as the window". Stored as a number so
   everything else can treat it as one. */
export const FULL_WIDTH = 4000;

export const DEFAULTS = {
  theme: "light",
  interfaceBackground: 242,
  showHidden: true,
  manualRefresh: true,
  showScrollTopButton: true,
  showReloadButton: true,
  showScrollBottomButton: true,
  fileControlTransparency: 40,
  mdBackground: 242,
  mdFontSize: 17,
  mdLineHeight: 1.5,
  mdWidth: FULL_WIDTH,
  codeBackground: 242,
  codeFontSize: 13,
  minimap: true,
  diffFontSize: 13,
};

const SWITCHES = ["minimap", "showHidden", "manualRefresh", "showScrollTopButton", "showReloadButton", "showScrollBottomButton"];
const THEMES = ["light", "dark"];
const BACKGROUNDS = ["interfaceBackground", "mdBackground", "codeBackground"];

const LIMITS = {
  fileControlTransparency: [0, 100],
  interfaceBackground: [0, 255],
  mdBackground: [0, 255],
  codeBackground: [0, 255],
  mdFontSize: [10, 26],
  mdLineHeight: [1.1, 2.4],
  mdWidth: [520, FULL_WIDTH],
  codeFontSize: [9, 24],
  diffFontSize: [9, 24],
};

function clamp(key, value) {
  if (SWITCHES.includes(key)) return value === true;
  if (key === "theme") return resolveTheme(value);
  if (BACKGROUNDS.includes(key) && typeof value !== "number") return DEFAULTS[key];
  const [low, high] = LIMITS[key];
  const number = Number(value);
  /* A stored setting is user-editable in devtools and survives upgrades, so
     a nonsense value should fall back rather than produce a 1px font. */
  if (!Number.isFinite(number)) return DEFAULTS[key];
  const bounded = Math.min(high, Math.max(low, number));
  return BACKGROUNDS.includes(key) ? Math.round(bounded) : bounded;
}

export function readAppearance() {
  const saved = store.get(APPEARANCE_KEY, {}) || {};
  const out = {};
  for (const key of Object.keys(DEFAULTS)) {
    out[key] = key in saved ? clamp(key, saved[key]) : DEFAULTS[key];
  }
  return out;
}

export function writeAppearance(values) {
  store.set(APPEARANCE_KEY, values);
}

/* Older Auto preferences fall back to Light, like any unset theme. */
export function resolveTheme(choice) {
  return THEMES.includes(choice) ? choice : DEFAULTS.theme;
}

export function applyAppearance(values, editors) {
  values = Object.assign({}, DEFAULTS, values);
  applyInterfaceBackground(values.interfaceBackground);
  const root = document.documentElement;
  root.style.setProperty("--file-control-opacity", String((100 - values.fileControlTransparency) / 100));
  root.style.setProperty("--content-font-size", values.mdFontSize + "px");
  root.style.setProperty("--content-line-height", String(values.mdLineHeight));
  root.style.setProperty(
    "--content-width",
    values.mdWidth >= FULL_WIDTH ? "none" : values.mdWidth + "px"
  );
  const { editor, diffEditor } = editors || {};
  if (editor) {
    editor.updateOptions({
      minimap: { enabled: values.minimap !== false },
      fontSize: values.codeFontSize,
      /* Monaco reads lineHeight as pixels when it is above 8 and as a
         multiplier below — passing 1.65 straight through gives 1px lines. */
      lineHeight: Math.round(values.codeFontSize * 1.5),
    });
  }
  if (diffEditor) {
    diffEditor.updateOptions({
      minimap: { enabled: values.minimap !== false },
      fontSize: values.diffFontSize,
      lineHeight: Math.round(values.diffFontSize * 1.5),
    });
  }
}

/* -- where you were ------------------------------------------------------- */

export function readScroll(path) {
  const all = store.get(SCROLL_KEY, {}) || {};
  return all[path] || null;
}

export function writeScroll(path, position) {
  const all = store.get(SCROLL_KEY, {}) || {};
  delete all[path];
  all[path] = position;
  const keys = Object.keys(all);
  if (keys.length > MAX_REMEMBERED_SCROLLS) {
    /* Oldest-inserted first. Unbounded growth would eventually hit the
       server's size limit on a setting and the write would be refused. */
    keys.slice(0, keys.length - MAX_REMEMBERED_SCROLLS).forEach((key) => delete all[key]);
  }
  store.set(SCROLL_KEY, all);
}

/* -- the panel ------------------------------------------------------------- */

const SECTIONS = [
  {
    title: "General",
    rows: [
      { key: "theme", label: "Theme", kind: "choice", options: [["light", "Light"], ["dark", "Dark"]] },
      { key: "interfaceBackground", label: "Interface background", kind: "gray", accessibleLabel: "Interface background" },
      { key: "showHidden", label: "Show hidden files", kind: "switch" },
      { id: "homePath", label: "Configure Home folder (default folder to display when you open ficelo)", kind: "home" },
    ],
  },
  {
    title: "File controls",
    rows: [
      { key: "manualRefresh", label: "Manual refresh", kind: "switch", title: "Turn off to refresh unedited files automatically." },
      { label: "Show/hide buttons", kind: "switch-group", rows: [
        { key: "showScrollTopButton", label: "Go to top" },
        { key: "showReloadButton", label: "Refresh" },
        { key: "showScrollBottomButton", label: "Go to bottom" },
      ] },
      { key: "fileControlTransparency", label: "Button transparency", kind: "slider", step: 5, unit: "%",
        title: "0% is solid; 100% is transparent. Buttons become fully visible on hover or keyboard focus." },
    ],
  },
  {
    title: "Markdown",
    rows: [
      { label: "Table of Contents", kind: "toc" },
      { key: "mdBackground", label: "Background", kind: "gray", accessibleLabel: "Markdown background" },
      { key: "mdFontSize", label: "Font size", kind: "slider", step: 1, unit: "px" },
      { key: "mdLineHeight", label: "Line height", kind: "slider", step: 0.05, unit: "" },
      { key: "mdWidth", label: "Content width", kind: "slider", step: 20, unit: "px", full: true },
    ],
  },
  {
    title: "Code files",
    rows: [
      { key: "codeBackground", label: "Background", kind: "gray", accessibleLabel: "Code background" },
      { key: "codeFontSize", label: "Font size", kind: "slider", step: 1, unit: "px" },
      { key: "minimap", label: "Minimap", kind: "switch" },
    ],
  },
  {
    title: "Diffs in review",
    rows: [{ key: "diffFontSize", label: "Font size", kind: "slider", step: 1, unit: "px" }],
  },
];

function sliderRow(row, values, onChange) {
  const [low, high] = LIMITS[row.key];
  const shown = () =>
    row.full && values[row.key] >= FULL_WIDTH ? "Full" : values[row.key] + row.unit;
  const output = el("span", { className: "set-value", textContent: shown() });
  const slider = el("input", {
    type: "range",
    className: "set-slider",
    name: row.key,
    min: String(low),
    max: String(high),
    step: String(row.step),
    value: String(values[row.key]),
  });
  slider.addEventListener("input", () => {
    values[row.key] = Number(slider.value);
    output.textContent = shown();
    onChange(values);
  });
  return el("label", { className: "set-row", title: row.title || "" }, [
    el("span", { className: "set-label", textContent: row.label }),
    slider,
    output,
  ]);
}

function switchRow(row, values, onChange) {
  const box = el("input", { type: "checkbox", className: "set-check" });
  box.checked = values[row.key] === true;
  box.addEventListener("change", () => {
    values[row.key] = box.checked;
    onChange(values);
  });
  return el("label", { className: "set-row is-switch", title: row.title || "" }, [
    el("span", { className: "set-label", textContent: row.label }),
    box,
  ]);
}

function tocRow(row, values, onChange) {
  const box = el("input", { id: "setting-toc", type: "checkbox", className: "set-check" });
  box.checked = !(store.get(TOC_KEY, {}) || {}).hidden;
  box.addEventListener("change", () => {
    store.set(TOC_KEY, { ...store.get(TOC_KEY, {}), hidden: !box.checked });
    onChange(values);
  });
  return el("label", { className: "set-row is-switch" }, [
    el("span", { className: "set-label", textContent: row.label }),
    box,
  ]);
}

function choiceRow(row, values, onChange, redraw) {
  const group = el(
    "div",
    { className: "segmented set-choice" },
    row.options.map(([value, label]) => {
      const button = el("button", { className: "seg", textContent: label });
      if (values[row.key] === value) button.setAttribute("aria-pressed", "true");
      button.addEventListener("click", (event) => {
        event.preventDefault();
        values[row.key] = value;
        onChange(values);
        redraw();
      });
      return button;
    })
  );
  return el("div", { className: "set-row is-switch" }, [
    el("span", { className: "set-label", textContent: row.label }),
    group,
  ]);
}

function grayRow(row, values, onChange) {
  const shown = () => grayColor(values[row.key]).toUpperCase();
  const id = "setting-" + row.key;
  const color = el("input", { type: "text", className: "set-color", name: row.key + "Color",
    value: shown(), spellcheck: false, autocomplete: "off", disabled: values.theme === "dark" });
  color.setAttribute("aria-label", row.accessibleLabel + " color (hex)");
  color.setAttribute("aria-describedby", id + "-hint");
  const hint = el("span", { id: id + "-hint", className: "set-hint", textContent: "Gray hex only · Light mode" });
  const slider = el("input", { id, type: "range", className: "set-slider", name: row.key,
    min: "0", max: "255", step: "1", value: String(values[row.key]), disabled: values.theme === "dark" });
  slider.setAttribute("aria-label", row.accessibleLabel);
  slider.setAttribute("aria-valuetext", shown());
  const clearError = () => {
    color.setCustomValidity("");
    color.removeAttribute("aria-invalid");
    hint.textContent = "Gray hex only · Light mode";
    hint.classList.remove("is-error");
  };
  slider.addEventListener("input", () => {
    values[row.key] = Number(slider.value);
    color.value = shown();
    clearError();
    slider.setAttribute("aria-valuetext", shown());
    onChange(values);
  });
  const acceptColor = (normalize) => {
    const level = parseGrayColor(color.value);
    if (level === null) {
      const message = "Use a gray hex color, e.g. #F2F2F2.";
      color.setCustomValidity(message);
      color.setAttribute("aria-invalid", "true");
      hint.textContent = message;
      hint.classList.add("is-error");
      return;
    }
    const changed = values[row.key] !== level;
    values[row.key] = level;
    slider.value = String(level);
    slider.setAttribute("aria-valuetext", shown());
    clearError();
    if (normalize) color.value = shown();
    if (changed) onChange(values);
  };
  color.addEventListener("input", () => acceptColor(false));
  color.addEventListener("change", () => acceptColor(true));
  color.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); acceptColor(true); }
  });
  return el("div", { className: "set-row is-background" }, [
    el("label", { htmlFor: id, className: "set-label", textContent: row.label }),
    color,
    slider,
    hint,
  ]);
}

export function buildPanel(mount, values, onChange, options = {}) {
  // Keep an unsaved Home draft across theme/reset redraws without watching typing.
  const home = options.home ?? mount.querySelector("#homePath")?.value ?? "";
  const redraw = () => buildPanel(mount, values, onChange);
  const blocks = [];
  for (const section of SECTIONS) {
    const block = el("section", { className: "settings-section" }, [
      el("div", { className: "set-title", textContent: section.title }),
    ]);
    block.setAttribute("aria-label", section.title);
    for (const row of section.rows) {
      if (row.kind === "slider") block.append(sliderRow(row, values, onChange));
      else if (row.kind === "switch") block.append(switchRow(row, values, onChange));
      else if (row.kind === "toc") block.append(tocRow(row, values, onChange));
      else if (row.kind === "home") block.append(el("div", { className: "set-home" }, [
        el("label", { htmlFor: row.id, textContent: row.label }),
        el("input", { id: row.id, type: "text", className: "path", value: home, spellcheck: false, autocomplete: "off" }),
      ]));
      else if (row.kind === "switch-group") block.append(el("fieldset", { className: "set-button-group" }, [
        el("legend", { textContent: row.label }),
        ...row.rows.map((item) => switchRow(item, values, onChange)),
      ]));
      else if (row.kind === "gray") block.append(grayRow(row, values, onChange));
      else block.append(choiceRow(row, values, onChange, redraw));
    }
    blocks.push(block);
  }
  const reset = el("button", { className: "set-reset", textContent: "Reset to defaults" });
  reset.addEventListener("click", () => {
    /* Assigning over the same object rather than replacing it, because the
       sliders above close over this one. */
    Object.assign(values, DEFAULTS);
    store.set(TOC_KEY, { ...store.get(TOC_KEY, {}), hidden: false });
    onChange(values);
    redraw();
  });
  blocks.push(reset);
  mount.replaceChildren(...blocks);
  return mount;
}
