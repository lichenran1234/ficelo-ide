/* Optional per-file reading paper, keyed by absolute filesystem path. */
import { store } from "./util.js";

const KEY = "ficelo.filebackgrounds";
const ROLES = ["paper", "row", "stripe", "header", "border", "ink", "muted", "link",
  "stripeInk", "stripeLink", "headerInk", "headerLink", "inlineInk", "markInk", "warning",
  "calloutInk", "calloutLink"];

function hexColor(value) {
  return typeof value === "string" && /^#[\da-f]{6}$/i.test(value) ? value.toLowerCase() : null;
}

export function grayColor(level = 242) {
  const value = Number.isFinite(level) ? Math.round(Math.max(0, Math.min(255, level))) : 242;
  return "#" + value.toString(16).padStart(2, "0").repeat(3);
}

export function parseGrayColor(value) {
  const match = typeof value === "string" && /^#?([\da-f]{3}|[\da-f]{6})$/i.exec(value.trim());
  if (!match) return null;
  const hex = match[1].length === 3 ? [...match[1]].map((digit) => digit + digit).join("") : match[1];
  const rgb = channels("#" + hex);
  return rgb.every((channel) => channel === rgb[0]) ? rgb[0] : null;
}

function channels(color) {
  return [1, 3, 5].map((start) => parseInt(color.slice(start, start + 2), 16));
}

function toHex(rgb) {
  return "#" + rgb.map((channel) => Math.round(Math.max(0, Math.min(255, channel)))
    .toString(16).padStart(2, "0")).join("");
}

function luminance(color) {
  const linear = channels(color).map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

function contrast(first, second) {
  const a = luminance(first), b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

export function backgroundDark(color) {
  const paper = hexColor(color);
  return !!paper && contrast("#ffffff", paper) > contrast("#000000", paper);
}

/* Keep the original hue when it is readable; otherwise make the smallest
   blend toward the stronger black/white endpoint that reaches the target.
   Test rounded RGB colors during the search, including middle grays where
   simply swapping Light and Dark palettes does not provide enough contrast. */
export function readableColor(foreground, background, minimum = 4.5) {
  const ink = hexColor(foreground), paper = hexColor(background);
  if (!ink || !paper) return null;
  if (contrast(ink, paper) >= minimum) return ink;
  const target = backgroundDark(paper) ? 255 : 0;
  const original = channels(ink);
  let low = 0, high = 1, best = grayColor(target);
  for (let step = 0; step < 16; step += 1) {
    const fraction = (low + high) / 2;
    const candidate = toHex(original.map((value) => value + (target - value) * fraction));
    if (contrast(candidate, paper) >= minimum) { best = candidate; high = fraction; }
    else low = fraction;
  }
  return best;
}

export function readFileBackground(absolutePath, dark = false) {
  if (dark || typeof absolutePath !== "string" || !absolutePath.startsWith("/")) return null;
  const saved = store.get(KEY, null);
  if (!saved || typeof saved !== "object" || Array.isArray(saved) ||
      !Object.prototype.hasOwnProperty.call(saved, absolutePath)) return null;
  return hexColor(saved[absolutePath]);
}

export function paletteFor(color) {
  const paper = hexColor(color);
  if (!paper) return null;
  const dark = backgroundDark(paper);
  const shade = (amount) => toHex(channels(paper).map((channel) => channel + (dark ? amount : -amount)));
  const stripe = shade(7), header = shade(14);
  // Monaco only applies a separate selected-text foreground in its high
  // contrast themes. Keep selection paper in the same foreground family.
  const selection = backgroundDark(header) === dark ? header : shade(-14);
  const ink = readableColor(dark ? "#b5b5b8" : "#2d3d50", paper);
  const link = readableColor(dark ? "#6785e0" : "#607cd2", paper);
  return { paper, row: paper, stripe, header, border: shade(38), selection, ink, link,
    muted: readableColor(dark ? "#bbbbbb" : "#586069", paper),
    stripeInk: readableColor(ink, stripe), stripeLink: readableColor(link, stripe),
    headerInk: readableColor(ink, header), headerLink: readableColor(link, header),
    inlineInk: readableColor("#607cd2", "#f3f6ff"),
    calloutInk: readableColor("#2d3d50", "#f5f7fa"), calloutLink: readableColor("#607cd2", "#f5f7fa"),
    markInk: readableColor("#2d3d50", "#fff07e"), warning: readableColor("#9a6700", paper) };
}

export function applyReadingBackground(scrollport, color) {
  const palette = paletteFor(color);
  for (const role of ROLES) {
    const variable = "--file-" + role.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
    if (palette) scrollport.style.setProperty(variable, palette[role]);
    else scrollport.style.removeProperty(variable);
  }
  if (palette) scrollport.dataset.fileBackground = palette.paper;
  else delete scrollport.dataset.fileBackground;
  return palette;
}
