# Re-check of the three mutations that survived the first v0.1 run.
S = "src/ficelo/search.py"
M = "src/ficelo/web/js/monaco.js"
CASES = [
    (M, "fence info treated as an extension again (the original bug)",
     '  const word = String(info).trim().split(/\\s+/)[0].toLowerCase();\n  if (index.byName.has(word)) return index.byName.get(word);',
     '  const word = String(info).trim().split(/\\s+/)[0].toLowerCase();\n  if (false) return index.byName.get(word);',
     "tests.test_browser"),
    (M, "language aliases dropped from the index",
     '    for (const alias of language.aliases || []) {\n      index.byName.set(String(alias).toLowerCase(), language.id);\n    }\n',
     "", "tests.test_browser"),
]
