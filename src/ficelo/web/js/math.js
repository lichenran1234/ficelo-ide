/* Math is tokenised before Markdown can interpret its punctuation. The
   renderer is an ES module, so lazy loading cannot conflict with Monaco's
   AMD loader. All assets are local. */

const configured = new WeakSet();
let loading;

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function formula(text, display) {
  const tag = display ? "div" : "span";
  const escaped = escapeHtml(text);
  return '<' + tag + ' class="math math-' + (display ? "display" : "inline") +
    '" data-tex="' + escaped + '">' + escaped + '</' + tag + '>';
}

const BLOCK_DELIMITERS = [
  /^ {0,3}\$\$([\s\S]+?)\$\$[ \t]*(?:\n|$)/,
  /^ {0,3}\\\[([\s\S]+?)\\\][ \t]*(?:\n|$)/,
];
const INLINE_DISPLAY = /^\$\$((?:\\.|[^\\$])+?)\$\$/;
const INLINE_PAREN = /^\\\(([\s\S]+?)\\\)/;
const INLINE_DOLLAR = /^\$(?![\s$])((?:\\.|[^\\$\n])+?)(?<![\s\\])\$(?!\d)/;

export function configureMath(marked) {
  if (configured.has(marked)) return;
  configured.add(marked);
  marked.use({ extensions: [
    {
      name: "formulaBlock", level: "block",
      start(src) {
        const match = /(?:^|\n) {0,3}(?:\$\$|\\\[)/.exec(src);
        return match ? match.index + (match[0].startsWith("\n") ? 1 : 0) : undefined;
      },
      tokenizer(src) {
        for (const delimiter of BLOCK_DELIMITERS) {
          const match = delimiter.exec(src);
          if (match && match[1].trim()) return { type: "formulaBlock", raw: match[0], text: match[1].trim() };
        }
      },
      renderer: (token) => formula(token.text, true) + "\n",
    },
    {
      name: "formulaInline", level: "inline",
      start(src) { const at = src.search(/\$|\\\(/); return at < 0 ? undefined : at; },
      tokenizer(src) {
        let match = INLINE_DISPLAY.exec(src);
        if (match) return { type: "formulaInline", raw: match[0], text: match[1].trim(), display: true };
        match = INLINE_PAREN.exec(src) || INLINE_DOLLAR.exec(src);
        if (match) return { type: "formulaInline", raw: match[0], text: match[1].trim(), display: false };
      },
      renderer: (token) => formula(token.text, token.display),
    },
  ] });
}

function renderer() {
  if (!loading) {
    if (!document.querySelector('link[data-math-style]')) {
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "/vendor/katex/katex.min.css";
      css.dataset.mathStyle = "1";
      document.head.append(css);
    }
    loading = import("/vendor/katex/katex.mjs").then((module) => module.default)
      .catch((error) => { loading = null; throw error; });
  }
  return loading;
}

export async function renderMath(root) {
  // Let Markdown own fence parsing, including tilde, long and unclosed fences.
  root.querySelectorAll("pre > code.language-math").forEach((code) => {
    const holder = document.createElement("div");
    holder.className = "math math-display";
    holder.dataset.tex = code.textContent.trim();
    holder.textContent = holder.dataset.tex;
    code.parentNode.replaceWith(holder);
  });
  const nodes = Array.from(root.querySelectorAll(".math:not([data-drawn])"));
  if (!nodes.length) return;
  try {
    const katex = await renderer();
    for (const node of nodes) {
      if (!node.isConnected) continue;
      katex.render(node.dataset.tex, node, {
        displayMode: node.classList.contains("math-display"), throwOnError: false, trust: false,
      });
      node.dataset.drawn = "1";
    }
  } catch (error) {
    nodes.forEach((node) => { node.title = "Math renderer unavailable — showing the source"; });
  }
}
