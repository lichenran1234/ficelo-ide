/* Fenced Markdown code uses the bundled highlight.js grammar and theme. */

const SHELL_LANGUAGES = new Set(["bash", "sh", "shell", "zsh", "console", "shellsession"]);
const SHELL_TOKENS = /(\n)|(\|\||&amp;&amp;|&amp;|[|;])|(--?[A-Za-z][A-Za-z0-9_.-]*)|([^\s|;&]+)|([^\S\n]+)|([\s\S])/g;
const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/;

function decorateText(text, state) {
  let result = "";
  SHELL_TOKENS.lastIndex = 0;
  for (let match; (match = SHELL_TOKENS.exec(text));) {
    const token = match[0];
    if (match[1]) {
      state.command = !state.continued;
      state.continued = false;
      result += token;
    } else if (match[2]) {
      state.command = true;
      state.continued = false;
      result += token;
    } else if (match[3]) {
      state.command = false;
      state.continued = false;
      result += '<span class="hljs-attr">' + token + "</span>";
    } else if (match[4]) {
      state.continued = token === "\\";
      if (state.command && !state.continued && !ASSIGNMENT.test(token)) {
        result += '<span class="hljs-built_in">' + token + "</span>";
        state.command = false;
      } else result += token;
    } else result += token;
  }
  return result;
}

/* Preserve grammar spans, especially quoted strings and comments. Only
   otherwise uncoloured commands and options need the supplemental marks. */
export function decorateShell(html) {
  const state = { command: true, continued: false };
  let result = "", at = 0, depth = 0;
  while (at < html.length) {
    const left = html.indexOf("<", at);
    const text = html.slice(at, left < 0 ? html.length : left);
    result += depth ? text : decorateText(text, state);
    if (left < 0) break;
    const right = html.indexOf(">", left);
    if (right < 0) { result += html.slice(left); break; }
    const tag = html.slice(left, right + 1);
    result += tag;
    if (tag[1] === "/") depth = Math.max(0, depth - 1);
    else if (tag[tag.length - 2] !== "/") depth++;
    at = right + 1;
  }
  return result;
}

export async function colouriseCode(root) {
  if (!window.hljs) return;
  for (const node of root.querySelectorAll("pre code")) {
    try {
      window.hljs.highlightElement(node);
      const language = /language-([A-Za-z0-9_+-]+)/.exec(node.className);
      if (language && SHELL_LANGUAGES.has(language[1].toLowerCase())) {
        node.innerHTML = decorateShell(node.innerHTML);
      }
    } catch (_) { /* Keep the source readable when a grammar cannot handle it. */ }
  }
}
