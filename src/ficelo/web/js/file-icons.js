/* Original, dependency-free file symbols. Filenames only choose a drawing;
   they are never inserted into SVG markup or used as resource URLs. */

const SVG = "http://www.w3.org/2000/svg";
const extensions = new Map();
for (const [type, suffixes] of Object.entries({
  python: "py pyw pyi", javascript: "js mjs cjs", typescript: "ts mts cts",
  jsx: "jsx", tsx: "tsx", c: "c h", cpp: "cc cpp cxx hpp hxx hh",
  rust: "rs", go: "go", java: "java", kotlin: "kt kts", swift: "swift",
  ruby: "rb erb", php: "php phtml", lua: "lua", r: "r rmd", dart: "dart",
  shell: "sh bash zsh fish ksh csh ps1", html: "html htm xhtml vue svelte astro",
  css: "css scss sass less", json: "json json5 jsonc", records: "jsonl ndjson",
  yaml: "yaml yml", config: "toml ini cfg conf config env properties",
  markdown: "md markdown mdown mkd", document: "txt rst org adoc tex log",
  pdf: "pdf", notebook: "ipynb", table: "csv tsv xls xlsx ods",
  data: "parquet arrow feather avro", database: "sql db sqlite sqlite3 duckdb",
  image: "png jpg jpeg gif webp bmp ico avif heic tif tiff", vector: "svg",
  audio: "mp3 wav ogg oga opus flac m4a aac aif aiff",
  video: "mp4 m4v webm avi mov mkv mpeg mpg",
  archive: "zip tar gz tgz bz2 xz zst 7z rar whl jar war",
  binary: "bin exe dll so dylib o a class wasm", font: "woff woff2 ttf otf eot",
  xml: "xml xsd xsl plist",
})) {
  for (const suffix of suffixes.split(" ")) extensions.set(suffix, type);
}

const specialNames = [
  [/^(readme)(?:\.|$)/, "readme"],
  [/^(license|licence|copying|unlicense)(?:\.|$)/, "license"],
  [/^(changelog|changes|history)(?:\.|$)/, "history"],
  [/^(dockerfile|containerfile)(?:\.|$)/, "container"],
  [/^(makefile|gnumakefile|cmakelists\.txt|build\.ninja|meson\.build|justfile|taskfile\.ya?ml)$/, "build"],
  [/^\.(gitignore|gitattributes|gitmodules|gitkeep)$/, "git"],
  [/^\.(bashrc|bash_profile|zshrc|zprofile|profile|fishrc)$/, "shell"],
  [/^\.env(?:\.|$)|^\.(editorconfig|npmrc|pypirc|gitconfig|prettierrc|eslintrc)(?:\.|$)/, "config"],
  [/^(package(?:-lock)?\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|pyproject\.toml|setup\.py|requirements(?:[.-].*)?\.txt|pipfile(?:\.lock)?|poetry\.lock|uv\.lock|cargo\.(toml|lock)|go\.(mod|sum)|gemfile(?:\.lock)?|composer\.(json|lock))$/, "package"],
];

const monograms = {
  python: ["PY", "blue"], javascript: ["JS", "gold"], typescript: ["TS", "blue"],
  jsx: ["JX", "cyan"], tsx: ["TX", "cyan"], c: ["C", "blue"], cpp: ["C++", "blue"],
  rust: ["RS", "orange"], go: ["GO", "cyan"], java: ["JA", "red"],
  kotlin: ["KT", "violet"], swift: ["SW", "orange"], ruby: ["RB", "red"],
  php: ["PH", "violet"], lua: ["LU", "blue"], r: ["R", "blue"], dart: ["DA", "cyan"],
  json: ["{}", "gold"], markdown: ["MD", "blue"], pdf: ["PDF", "red"], font: ["Aa", "violet"],
};

const tones = {
  folder: "gold", "folder-open": "gold", document: "muted", readme: "green",
  license: "gold", history: "green", shell: "green", html: "orange", css: "violet",
  yaml: "red", config: "muted", records: "gold", table: "green", data: "cyan",
  database: "cyan", notebook: "orange", image: "green", vector: "violet",
  audio: "violet", video: "red", archive: "orange", binary: "muted", xml: "orange",
  container: "blue", package: "green", build: "muted", git: "orange",
};

function element(tag, attributes = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

const path = (d, attributes) => element("path", { d, ...attributes });
const circle = (cx, cy, r, attributes) => element("circle", { cx, cy, r, ...attributes });
const rect = (x, y, width, height, attributes) => element("rect", { x, y, width, height, ...attributes });

function sheet() {
  return [path("M9.5 1.5H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-8z", { fill: "currentColor", "fill-opacity": ".08" }),
    path("M9.5 1.5v4H13")];
}

function drawing(type) {
  switch (type) {
    case "folder": return [path("M1.5 4V3a1 1 0 0 1 1-1h3l1.5 2H13a1.5 1.5 0 0 1 1.5 1.5V12a1.5 1.5 0 0 1-1.5 1.5H3A1.5 1.5 0 0 1 1.5 12z", { fill: "currentColor", "fill-opacity": ".18" }), path("M1.5 5.5h13")];
    case "folder-open": return [path("M1.5 7V3a1 1 0 0 1 1-1h3l1.5 2h5a1 1 0 0 1 1 1v2"), path("M1.5 6.5h13l-2 6.5H3z", { fill: "currentColor", "fill-opacity": ".24" })];
    case "readme": return [path("M8 3.5C6 2 3.5 2 1.5 2.5v10C4 12 6 12 8 13.5c2-1.5 4-1.5 6.5-1v-10C12.5 2 10 2 8 3.5z", { fill: "currentColor", "fill-opacity": ".08" }), path("M8 3.5v10M3.5 5h2M10.5 5h2M3.5 7.5h2M10.5 7.5h2")];
    case "license": return [...sheet(), circle(9.5, 9.5, 2.2), path("M8 11.5v3l1.5-1 1.5 1v-3M5 5h2M5 7h2")];
    case "history": return [path("M3 3v10M6.5 3h7M6.5 8h5M6.5 13h7"), ...[3, 8, 13].map((y) => circle(3, y, 1.3, { fill: "currentColor" }))];
    case "shell": return [rect(1.5, 2.5, 13, 11, { rx: 2, fill: "currentColor", "fill-opacity": ".08" }), path("M4 6l2 2-2 2M8 10h4")];
    case "html": return [path("M5 4L1.5 8 5 12M11 4l3.5 4-3.5 4M9 2.5l-2 11")];
    case "css": return [path("M6 2L4 14M12 2l-2 12M2 6h12M1 10h12")];
    case "yaml": return [path("M2 3h3M5 6.5h4M5 10h4M2 13h3M3.5 4.5V11.5"), circle(12, 6.5, 1, { fill: "currentColor" }), circle(12, 10, 1, { fill: "currentColor" })];
    case "config": return [path("M2 4h12M2 8h12M2 12h12"), ...[[5, 4], [11, 8], [7, 12]].map(([x, y]) => circle(x, y, 1.5, { fill: "currentColor" }))];
    case "records": return [path("M5.5 3h8M5.5 8h6M5.5 13h8"), ...[3, 8, 13].map((y) => rect(1.5, y - 1, 2, 2, { fill: "currentColor" }))];
    case "table": return [rect(1.5, 2.5, 13, 11, { rx: 1 }), rect(2, 3, 12, 3, { fill: "currentColor", opacity: ".18", stroke: "none" }), path("M1.5 6h13M1.5 9.5h13M6 2.5v11M10.5 6v7.5")];
    case "data": return [path("M1.5 4L8 1.5 14.5 4 8 6.5z", { fill: "currentColor", "fill-opacity": ".14" }), path("M1.5 8L8 10.5 14.5 8M1.5 12L8 14.5l6.5-2.5")];
    case "database": return [element("ellipse", { cx: 8, cy: 3.5, rx: 5.5, ry: 2 }), path("M2.5 3.5v9c0 2.7 11 2.7 11 0v-9M2.5 8c0 2.7 11 2.7 11 0")];
    case "notebook": return [rect(3, 1.5, 11, 13, { rx: 1.5, fill: "currentColor", "fill-opacity": ".08" }), path("M1.5 4h3M1.5 8h3M1.5 12h3M7 5h4M7 8h4M7 11h2")];
    case "image": return [rect(1.5, 2.5, 13, 11, { rx: 1.5 }), circle(10.5, 5.5, 1, { fill: "currentColor" }), path("M3 11l3-3 2 2 1.5-1.5 3.5 3.5")];
    case "vector": return [path("M2 12c2-10 10-10 12 0M2 3h12M8 3v5"), ...[[1, 11], [13, 11], [7, 2]].map(([x, y]) => rect(x, y, 2, 2, { fill: "currentColor" }))];
    case "audio": return [path("M6.5 11.5v-8l6-1.5v8M6.5 6l6-1.5"), element("ellipse", { cx: 4.8, cy: 12, rx: 1.8, ry: 1.5, fill: "currentColor" }), element("ellipse", { cx: 10.8, cy: 10.5, rx: 1.8, ry: 1.5, fill: "currentColor" })];
    case "video": return [rect(1.5, 2.5, 13, 11, { rx: 2 }), path("M6.5 5l4 3-4 3z", { fill: "currentColor", stroke: "none" })];
    case "archive": return [rect(3, 1.5, 10, 13, { rx: 1.5, fill: "currentColor", "fill-opacity": ".08" }), path("M7 3.5h2v2H7v2h2v2H7"), rect(7, 10.5, 2, 2, { rx: .4 })];
    case "binary": return [rect(4, 4, 8, 8, { rx: 1 }), path("M6 1.5V4M10 1.5V4M6 12v2.5M10 12v2.5M1.5 6H4M1.5 10H4M12 6h2.5M12 10h2.5M6.5 6.5h3v3h-3z")];
    case "xml": return [...sheet(), path("M6.5 7L5 9l1.5 2M9.5 7l1.5 2-1.5 2")];
    case "container": return [rect(3, 2, 4, 4, { rx: .5 }), rect(9, 2, 4, 4, { rx: .5 }), rect(3, 8, 4, 4, { rx: .5 }), rect(9, 8, 4, 4, { rx: .5 }), path("M1 14h14")];
    case "package": return [path("M8 1.5l6 3.3v6.4L8 14.5l-6-3.3V4.8z", { fill: "currentColor", "fill-opacity": ".1" }), path("M2 4.8L8 8l6-3.2M8 8v6.5M5 3.2l6 3.2")];
    case "build": return [path("M2 14l8-8M8 4l2-2 4 4-2 2zM1.5 11.5l3 3M7 5l1-1")];
    case "git": return [path("M4 4v8M4 9h4a4 4 0 0 0 4-4"), circle(4, 2.5, 1.5), circle(4, 13.5, 1.5), circle(12, 3.5, 1.5)];
    default: return [...sheet(), path("M5.5 8h5M5.5 10.5h4")];
  }
}

const templates = new Map();

function template(type) {
  if (templates.has(type)) return templates.get(type);
  const monogram = monograms[type];
  const icon = element("svg", {
    class: "file-icon", viewBox: "0 0 16 16", width: 16, height: 16,
    "aria-hidden": "true", focusable: "false", fill: "none", stroke: "currentColor",
    "stroke-width": 1.2, "stroke-linecap": "round", "stroke-linejoin": "round",
    "data-file-type": type, "data-tone": monogram?.[1] || tones[type] || "muted",
  });
  if (monogram) {
    icon.append(rect(1.5, 1.5, 13, 13, { rx: 2.5, fill: "currentColor", "fill-opacity": ".1" }));
    const label = element("text", { x: 8, y: 10.8, "text-anchor": "middle", fill: "currentColor", stroke: "none",
      "font-family": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", "font-size": monogram[0].length > 2 ? 6.4 : monogram[0].length === 1 ? 10 : 8.2, "font-weight": 700 });
    label.textContent = monogram[0];
    icon.append(label);
  } else icon.append(...drawing(type));
  templates.set(type, icon);
  return icon;
}

export function createFileIcon(name, isDirectory = false, open = false) {
  let type = isDirectory ? (open ? "folder-open" : "folder") : "file";
  if (!isDirectory) {
    const base = String(name || "").split("/").pop().toLowerCase();
    const special = specialNames.find(([pattern]) => pattern.test(base));
    type = special?.[1] || extensions.get(base.includes(".") ? base.split(".").pop() : "") || "file";
  }
  return template(type).cloneNode(true);
}
