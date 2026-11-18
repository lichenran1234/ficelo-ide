# The rendering mutations. These exist because three tests were green while
# the diff editor showed one line per side, so the fix needs guarding.
R = "src/ficelo/web/js/review.js"

CASES = [
    (R, "diff editor stops observing its container (the one-line bug)",
     "    automaticLayout: true,", "    automaticLayout: false,", "tests.test_browser.DiffViewTest"),
    (R, "unchanged regions no longer collapsed",
     "    hideUnchangedRegions: {\n      enabled: true,", "    hideUnchangedRegions: {\n      enabled: false,",
     "tests.test_browser.DiffViewTest"),
    ("src/ficelo/web/js/app.js", "side-by-side ignored, always unified",
     "    renderSideBySide: readLayout() === \"side\",", "    renderSideBySide: false,",
     "tests.test_browser.DiffViewTest"),
    ("src/ficelo/web/js/app.js", "the layout toggle stops reaching the editor",
     "        app.diffEditor.updateOptions({ renderSideBySide: button.dataset.layout === \"side\" });",
     "        void button;", "tests.test_browser.DiffViewTest"),
    (R, "both diff sides share one model uri",
     '  const newUri = monaco.Uri.parse("ficelo-new:/" + identity);',
     '  const newUri = oldUri;', "tests.test_browser.DiffViewTest"),
]
