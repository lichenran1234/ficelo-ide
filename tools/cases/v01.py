# Mutations for the v0.1 surface. Each: (file, description, before, after, test module)
S = "src/ficelo/search.py"
M = "src/ficelo/web/js/markdown.js"
T = "src/ficelo/web/js/tree.js"
A = "src/ficelo/web/js/app.js"
U = "src/ficelo/web/js/util.js"

# The ranking cases that used to live here went stale when rank() was
# rewritten into tiers; tools/cases/search.py covers ranking now.
CASES = [
    (S, "adjacency bonus removed (scattered letters score as well)",
     "        if at == previous + 1:\n            score += 8",
     "        if at == previous + 1:\n            score += 1", "tests.test_search"),
    (S, "walk claims completeness even when truncated",
     '                        return tuple(found), False, seen', '                        return tuple(found), True, seen', "tests.test_search"),
    (S, "skip-list ignored (descends into .git and node_modules)",
     '                    if is_dir and not symlink and entry.name not in SKIP_NAMES:', '                    if is_dir and not symlink:', "tests.test_search"),
    (S, "empty query returns the whole tree instead of nothing",
     '    if not query:\n        return {"matches": [], "complete": True}\n', "", "tests.test_search"),
    (S, "results no longer sorted (filesystem order leaks out)",
     "    scored.sort(key=lambda pair: (-pair[0], pair[1]))", "    pass", "tests.test_search"),
    (M, "reference-link table not carried to the parser",
     "    single.links = tokens.links;\n", "", "tests.test_browser"),
    (M, "source-line stamping dropped",
     "wrapper.dataset.srcLine = String(block.line);", 'wrapper.dataset.srcLine = "0";',
     "tests.test_browser"),
    (M, "sanitiser no longer strips script tags",
     'const FORBIDDEN_TAGS = ["script", "style", "iframe", "object", "embed", "link", "meta", "base", "form"];',
     'const FORBIDDEN_TAGS = ["iframe"];', "tests.test_browser"),
    (M, "javascript: URLs allowed through",
     '        if (value.startsWith("javascript:") || value.startsWith("data:text/html")) {',
     "        if (false) {", "tests.test_browser"),
    (M, "headings no longer collected for the contents pane",
     '  const headings = Array.from(root.querySelectorAll("h1,h2,h3,h4"));', '  const headings = [];',
     "tests.test_browser"),
    ('src/ficelo/web/js/highlighter.js', "fenced code left uncoloured",
     '      window.hljs.highlightElement(node);',
     '      void node;', "tests.test_browser"),
    (T, "tree eagerly expands every folder",
     "      if (entry.is_dir && this.expanded.has(entry.path)) {", "      if (entry.is_dir) {",
     "tests.test_browser"),
    (A, "initial URL routing ignored (a URL no longer opens its file)",
     "  try { asked = routeFromUrl(initialUrl, app.config.root); }", "  try { asked = null; }", "tests.test_browser"),
]
