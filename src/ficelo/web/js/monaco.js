/* Loading Monaco, and the two questions the rest of the app asks of it.

   Monaco ships as AMD. Its loader installs a global `define`, which is why
   index.html loads marked first: marked's UMD build registers as an AMD
   module if it sees one, and window.marked would then be undefined. */

import { backgroundDark, paletteFor, readableColor } from "./reading-background.js";

let loading = null;
let lightTheme = null;
let darkTheme = null;
let readingBackground = null;
const READING_THEME = "github-light-reading";

export const CODE_FONT = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace";

export function editorTheme(dark) {
  return dark ? "github-dark" : "github-light";
}

/* GitHub's public Primer syntax colours. Keeping the same token rules in
   both palettes makes source files and review editors change together. */
function defineGithubThemes(monaco) {
  const light = {
    text: "1f2328", comment: "6e7781", keyword: "cf222e", blue: "0550ae",
    string: "0a3069", orange: "953800", purple: "8250df", green: "116329",
  };
  const dark = {
    text: "e6edf3", comment: "8b949e", keyword: "ff7b72", blue: "79c0ff",
    string: "a5d6ff", orange: "ffa657", purple: "d2a8ff", green: "7ee787",
  };
  const rules = (palette) => [
    { token: "", foreground: palette.text },
    { token: "comment", foreground: palette.comment, fontStyle: "italic" },
    { token: "keyword", foreground: palette.keyword },
    { token: "keyword.operator", foreground: palette.blue },
    { token: "string", foreground: palette.string },
    { token: "string.escape", foreground: palette.blue },
    { token: "number", foreground: palette.blue },
    { token: "constant", foreground: palette.blue },
    { token: "regexp", foreground: palette.string },
    { token: "type", foreground: palette.orange },
    { token: "type.identifier", foreground: palette.orange },
    { token: "variable", foreground: palette.orange },
    { token: "variable.predefined", foreground: palette.blue },
    { token: "function", foreground: palette.purple },
    { token: "identifier", foreground: palette.text },
    { token: "tag", foreground: palette.green },
    { token: "attribute.name", foreground: palette.blue },
    { token: "attribute.value", foreground: palette.string },
    { token: "delimiter", foreground: palette.text },
    { token: "metatag", foreground: palette.keyword },
    { token: "key", foreground: palette.blue },
  ];
  lightTheme = {
    base: "vs", inherit: true, rules: rules(light),
    colors: {
      "editor.background": "#ffffff",
      "editor.foreground": "#1f2328",
      "editorLineNumber.foreground": "#8c959f",
      "editorLineNumber.activeForeground": "#1f2328",
      "editor.selectionBackground": "#0550ae26",
      "editor.lineHighlightBackground": "#eaeef280",
      "editorIndentGuide.background": "#d1d9e0",
      "editorIndentGuide.background1": "#d1d9e0",
    },
  };
  monaco.editor.defineTheme(editorTheme(false), lightTheme);
  darkTheme = {
    base: "vs-dark", inherit: true, rules: rules(dark),
    colors: {
      "editor.background": "#0d1117",
      "editor.foreground": "#e6edf3",
      "editorLineNumber.foreground": "#6e7681",
      "editorLineNumber.activeForeground": "#e6edf3",
      "editor.selectionBackground": "#79c0ff33",
      "editor.lineHighlightBackground": "#6e768133",
      "editorIndentGuide.background": "#21262d",
      "editorIndentGuide.background1": "#21262d",
    },
  };
  monaco.editor.defineTheme(editorTheme(true), darkTheme);
}

export function loadMonaco() {
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      if (!window.require) {
        reject(new Error("Monaco's loader did not load"));
        return;
      }
      window.require.config({ paths: { vs: "/vendor/monaco/vs" } });
      window.require(["vs/editor/editor.main"], () => {
        defineGithubThemes(window.monaco);
        resolve(window.monaco);
      }, reject);
    });
  }
  return loading;
}

/* Monaco already knows which extensions, filenames and aliases belong to
   which language, so asking it beats keeping a second table here that drifts
   from the first. Built once, on first use, because getLanguages() walks
   every registered contribution. */
let index = null;

function build() {
  const monaco = window.monaco;
  index = { byExtension: new Map(), byName: new Map() };
  for (const language of monaco.languages.getLanguages()) {
    for (const ext of language.extensions || []) {
      index.byExtension.set(ext.toLowerCase(), language.id);
    }
    for (const name of language.filenames || []) {
      index.byName.set(name.toLowerCase(), language.id);
    }
    index.byName.set(language.id.toLowerCase(), language.id);
    for (const alias of language.aliases || []) {
      index.byName.set(String(alias).toLowerCase(), language.id);
    }
  }
}

export function languageFor(filename) {
  if (!window.monaco) return "plaintext";
  if (!index) build();
  const lower = filename.toLowerCase();
  if (index.byName.has(lower)) return index.byName.get(lower);
  const dot = lower.lastIndexOf(".");
  /* A leading dot is the whole name, not an extension: `.gitignore` is not a
     file of type `gitignore`. */
  if (dot > 0 && index.byExtension.has(lower.slice(dot))) {
    return index.byExtension.get(lower.slice(dot));
  }
  return "plaintext";
}

/* The word after ```. It is a language name or alias — `python`, `js`, `sh` —
   and emphatically not a file extension, which is what an earlier version
   treated it as: it built "x.python" and asked for the extension `.python`,
   which nothing registers, so every fenced block silently stayed grey. */
export function languageForFence(info) {
  if (!info || !window.monaco) return "plaintext";
  if (!index) build();
  const word = String(info).trim().split(/\s+/)[0].toLowerCase();
  if (index.byName.has(word)) return index.byName.get(word);
  /* Some documents write the extension anyway (```py, ```rs). */
  if (index.byExtension.has("." + word)) return index.byExtension.get("." + word);
  return "plaintext";
}

export function applyTheme(name, background = null) {
  const monaco = window.monaco;
  if (!monaco) return;
  const color = name === "light" && typeof background === "string" && /^#[\da-f]{6}$/i.test(background)
    ? background.toLowerCase() : null;
  let theme = editorTheme(name === "dark");
  if (color && lightTheme) {
    theme = READING_THEME;
    if (readingBackground !== color) {
      const base = backgroundDark(color) ? darkTheme : lightTheme;
      const palette = paletteFor(color);
      const ink = (value) => readableColor(readableColor(value, color), palette.selection);
      const brackets = backgroundDark(color)
        ? ["#ffd700", "#da70d6", "#179fff"] : ["#0431fa", "#319331", "#7b3814"];
      monaco.editor.defineTheme(theme, {
        ...base,
        // Language-specific inherited rules can bypass the adjusted colors.
        // Unlisted tokens use our readable default instead.
        inherit: false,
        rules: base.rules.map((rule) => ({ ...rule,
          foreground: ink("#" + rule.foreground).slice(1) })),
        colors: {
          ...base.colors,
          "editor.background": color,
          "editor.foreground": ink(base.colors["editor.foreground"]),
          "editorGutter.background": color,
          "minimap.background": color,
          "editorLineNumber.foreground": readableColor(base.colors["editorLineNumber.foreground"], color),
          "editorLineNumber.activeForeground": readableColor(base.colors["editorLineNumber.activeForeground"], color),
          "editorCursor.foreground": palette.ink,
          "editorIndentGuide.background": palette.border,
          "editorIndentGuide.background1": palette.border,
          "editorBracketHighlight.foreground1": ink(brackets[0]),
          "editorBracketHighlight.foreground2": ink(brackets[1]),
          "editorBracketHighlight.foreground3": ink(brackets[2]),
          "editorBracketHighlight.unexpectedBracket.foreground": ink("#ff1212"),
          /* A tinted current line can invalidate text contrast at middle
             grays. Outline it instead; tokens also fit selection paper. */
          "editor.lineHighlightBackground": color,
          "editor.lineHighlightBorder": palette.border + "66",
          "editor.selectionBackground": palette.selection,
          "editor.inactiveSelectionBackground": palette.selection,
        },
      });
      readingBackground = color;
    }
  }
  /* Editor factories can select a base theme independently of this helper.
     Reapply the chosen name; Monaco already ignores an unchanged theme. */
  monaco.editor.setTheme(theme);
}
