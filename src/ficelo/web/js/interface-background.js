/* Interface colors stay separate from file and repository reading surfaces. */
import { backgroundDark, grayColor, readableColor } from "./reading-background.js";

function blend(first, second, amount) {
  return "#" + [1, 3, 5].map((start) => {
    const a = parseInt(first.slice(start, start + 2), 16);
    const b = parseInt(second.slice(start, start + 2), 16);
    return Math.round(a + (b - a) * amount).toString(16).padStart(2, "0");
  }).join("");
}

export function interfacePalette(level = 242) {
  const paper = grayColor(Number.isFinite(level) ? level : 242);
  const dark = backgroundDark(paper);
  const endpoint = dark ? "#ffffff" : "#000000";
  // All translucent-looking fills are concrete colors in the same foreground
  // family. Near middle gray, a tint must not cross the black/white boundary.
  const surface = (color, amount) => {
    const tint = blend(paper, color, amount);
    return backgroundDark(tint) === dark ? tint : blend(paper, dark ? "#000000" : "#ffffff", amount);
  };
  const hover = surface("#2563eb", .08);
  const dangerFill = surface("#d1453b", .12);
  const diff = surface("#2e9e4f", .10), diffActive = surface("#2e9e4f", .20);
  const commit = surface("#17a2a0", .12), commitActive = surface("#17a2a0", .24);
  const backgrounds = [paper, hover, dangerFill, diff, diffActive, commit, commitActive];
  const readable = (color) => backgrounds.reduce((ink, background) => readableColor(ink, background), color);
  const ink = readable(dark ? "#e6e8ec" : "#1c1e21");
  const muted = readable(dark ? "#9aa2af" : "#6b7280");
  const accent = readable(dark ? "#6ea8ff" : "#2563eb");
  const palette = { paper, hover, dangerFill, diff, diffActive, commit, commitActive,
    ink, muted, accent, onAccent: readableColor("#ffffff", accent),
    line: blend(paper, endpoint, .17), scheme: dark ? "dark" : "light",
    warning: readable("#b45309"), danger: readable("#d1453b"),
    added: readable("#2e9e4f"), modified: readable("#b58900"), star: readable("#d9a300") };
  for (const [tone, color] of Object.entries({ muted: "#697783", blue: "#3278ab", cyan: "#267f8e",
    gold: "#a47718", green: "#3b8061", violet: "#8261aa", red: "#b25361", orange: "#af6630" })) {
    palette["icon-" + tone] = readable(color);
  }
  for (let age = 0; age <= 6; age += 1) palette["age-" + age] = readable(blend(ink, paper, age * .08));
  return palette;
}

export function applyInterfaceBackground(level = 242) {
  const palette = interfacePalette(level);
  for (const [role, value] of Object.entries(palette)) {
    const name = role.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
    document.documentElement.style.setProperty("--interface-" + name, value);
  }
  return palette;
}
