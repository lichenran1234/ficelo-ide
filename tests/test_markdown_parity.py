"""Markdown rendering contracts checked together in one browser."""

import json
from pathlib import Path

from test_modules import ModuleTest


class MarkdownParityTest(ModuleTest):
    corpus = json.loads((Path(__file__).parent / "fixtures" / "markdown_rendering.json").read_text())
    checks = "const renderingCorpus = " + json.dumps(corpus) + ";\n" + r'''
import { renderMarkdown, frontMatter } from "/js/markdown.js";

function parsed(source) {
  return markdownBlocks(source, window.marked).filter(block => block.html).map(block => block.html).join("");
}
function documentFor(source) {
  const host = document.createElement("div");
  renderMarkdown(source, window.marked, host);
  return host;
}
function formulas(source) {
  return [...documentFor(source).querySelectorAll(".math")].map(node =>
    [node.dataset.tex, node.classList.contains("math-display"), node.tagName]);
}
for (const example of renderingCorpus) {
  check("Markdown grammar: " + example.name, () => eq(parsed(example.source), example.html));
}
check("soft and explicit line breaks retain their distinct meanings", () =>
  eq([parsed("one\ntwo\n"), parsed("one  \ntwo\n"), parsed("one\\\ntwo\n")],
    ["<p>one\ntwo</p>\n", "<p>one<br>two</p>\n", "<p>one<br>two</p>\n"]));
check("lexer and parser keep GFM and soft breaks when global defaults differ", () => {
  const before = {gfm: window.marked.defaults.gfm, breaks: window.marked.defaults.breaks};
  try {
    window.marked.setOptions({gfm:false, breaks:true});
    return eq(parsed("one\ntwo\n\n~~gone~~"), "<p>one\ntwo</p>\n<p><del>gone</del></p>\n");
  } finally { window.marked.setOptions(before); }
});
check("frontmatter accepts trimmed delimiters and colon-adjacent values", () => {
  const source = "\ufeff  --- \r\ntitle:value\r\n  ... \r\n# Body\r\n";
  const meta = frontMatter(source), host = documentFor(source);
  return eq([meta.rows, source.slice(meta.raw.length), host.querySelector("th").textContent,
    host.querySelector("td").textContent, host.querySelector(".md-block").dataset.srcLine,
    host.querySelector("table").parentNode === host],
    [[["title","value"]], "# Body\r\n", "title", "value", "4", true]);
});
check("frontmatter keeps nested text but omits blank lines and trims displayed values", () => {
  const source = "---\ntitle :   value  \n\ntools:\n  - Read\n\n  - Write\n---\nBody\n";
  const host = documentFor(source);
  return eq([...host.querySelectorAll("tr")].map(row => [...row.children].map(node => node.textContent)),
    [["title","value"],["tools","- Read\n  - Write"]]);
});
check("frontmatter only separates simple unindented keys", () => {
  const meta = frontMatter("---\nhello world: text\n标题: 值\nname: okay\n  nested: literal\n---\nBody");
  return eq(meta.rows, [["","hello world: text\n标题: 值"],["name","okay\n  nested: literal"]]);
});
check("empty, incomplete and later metadata delimiters remain Markdown", () =>
  eq([frontMatter("---\n---\nBody"), frontMatter("---\nname: x\nBody"),
    frontMatter("# Title\n\n---\nname: x\n---\n")], [null,null,null]));
check("metadata values are text and cannot introduce executable HTML", () => {
  const host = documentFor("---\nname: <img src=x onerror=alert(1)>\n---\nBody");
  return eq([host.querySelectorAll("img").length, host.querySelector("td").textContent],
    [0,"<img src=x onerror=alert(1)>"]);
});
check("math protects punctuation and trims inline TeX", () =>
  eq(formulas("$a_1 * b_2$ and \\( z \\)."), [["a_1 * b_2",false,"SPAN"],["z",false,"SPAN"]]));
check("inline display math accepts spaces and newlines", () =>
  eq(formulas("Before $$ x $$ then $$\ny\n$$ after."), [["x",true,"DIV"],["y",true,"DIV"]]));
check("display blocks interrupt prose and retain source line anchors", () => {
  const host = documentFor("# Title\n\nBefore\n$$\nx^2\n$$\n\nAfter\n");
  return eq([[...host.querySelectorAll(".md-block")].map(node => node.dataset.srcLine),
    [...host.querySelectorAll(".math")].map(node => node.dataset.tex)], [["1","3","4","8"],["x^2"]]);
});
check("all Markdown math fence forms are claimed after parsing", () => {
  const sources = ["```math\n x \n```", "~~~math\nx\n~~~", "````math\nx\n````", "```math title\nx\n```", "```math\nx\n"];
  return eq(sources.map(source => formulas(source)), sources.map(() => [["x",true,"DIV"]]));
});
check("a language beginning with math remains code", () => {
  const host = documentFor("```mathematica\nIntegrate[x,x]\n```\n\n```txt\n$x$\n```");
  return eq([host.querySelectorAll(".math").length,
    [...host.querySelectorAll("pre code")].map(node => [node.className,node.textContent])],
    [0,[["language-mathematica","Integrate[x,x]\n"],["language-txt","$x$\n"]]]);
});
check("prices, escaped dollars and empty parentheses remain literal", () =>
  eq(["It costs $5 and $10.", "\\$x\\$", "from $x$5.", "\\(\\)", "`$code$`"].map(formulas), [[],[],[],[],[]]));
check("empty display blocks remain visible source", () => {
  const host = documentFor("$$\n \n$$\n");
  return eq([host.querySelectorAll(".math").length, [...host.querySelectorAll("p")].map(node => node.textContent)], [0,["$$","$$"]]);
});
check("escaped dollars within math survive and unescaped nested dollars end inline math", () =>
  eq([formulas("$x\\$ + y$"), formulas("Inline $$a$b$$ done")],
    [[["x\\$ + y",false,"SPAN"]],[["a",false,"SPAN"]]]));
check("heading IDs remove punctuation, preserve hyphens and limit length", () => {
  const host = documentFor("# A/B\n\n## A.B\n\n### a--b\n\n#### " + "A".repeat(70) + "\n");
  return eq([...host.querySelectorAll("h1,h2,h3,h4")].map(node => node.id),
    ["ab","ab-2","a--b","a".repeat(60)]);
});
check("heading names get counted suffixes and only four levels participate", () => {
  const host = documentFor("# Title\n\n## Title\n\n### Title-2\n\n##### Deep\n\n###### Deeper\n");
  return eq([[...host.querySelectorAll("h1,h2,h3,h5,h6")].map(node => node.id), host.headings.map(heading => heading.depth)],
    [["title","title-2","title-2","",""],[1,2,3]]);
});
check("a single heading keeps its existing ID without creating one", () => {
  const plain = documentFor("# Only\n\n##### Deep\n"), explicit = documentFor('<h2 id="own">Only</h2>');
  return eq([plain.querySelector("h1").hasAttribute("id"), explicit.querySelector("h2").id], [false,"own"]);
});
check("multiple headings assign IDs from text even when HTML has an ID", () => {
  const host = documentFor('# First\n\n<h2 id="old">Second</h2>');
  return eq([host.querySelector("h1").id, host.querySelector("h2").id], ["first","second"]);
});
check("safe HTML survives while executable tags and URLs are removed", () => {
  const host = documentFor('<details><summary>Open</summary><b>Safe</b></details>\n\n' +
    '<style>body{display:none}</style><iframe src="https://example.com"></iframe>' +
    '<a href="java&#10;script:alert(1)" onclick="alert(1)">Link</a>');
  return eq([host.querySelector("summary").textContent, host.querySelectorAll("style,iframe").length,
    host.querySelector("a").hasAttribute("href"), host.querySelector("a").hasAttribute("onclick")], ["Open",0,false,false]);
});
'''

    def test_markdown_rendering_contract(self):
        self.assertAllPass()
