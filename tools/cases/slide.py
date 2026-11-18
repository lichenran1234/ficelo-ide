# Mutations for the tab-slide animation. An animation test is unusually easy
# to write so that it cannot fail, because the resting state is identical
# whether or not anything moved.
T = "src/ficelo/web/js/tabs.js"
M = "tests.test_browser.TabSlideTest"

CASES = [
    (T, "the slide is not run at all (tabs jump again)",
     '    this.applyCloseSlot(spacer);\n    this.slide(before);', '    this.applyCloseSlot(spacer);', M),
    (T, "the reflow is dropped, so the two style changes coalesce",
     "    void this.mount.offsetWidth;", "    void 0;", M),
    (T, "reduced-motion is ignored",
     "    if (!before.size || prefersStillness()) return;", "    if (!before.size) return;", M),
    # Deliberately absent: "a newly opened tab flies in from the left edge",
    # which would mutate `if (was === undefined) return;` to `was = 0`.
    # That mutant survives in both the app and the module harness — the
    # delta it produces comes out negligible in every arrangement tried, so
    # no test can distinguish it. The guard stays because its intent is
    # right, and it is recorded here as uncovered rather than left as a
    # case that reports MISSED for ever and trains the eye to skip the
    # report.
    (T, "tabs already on screen stop sliding",
     "      if (Math.abs(delta) < 1) return;", "      if (true) return;",
     "tests.test_modules.TabOrderModuleTest"),
    (T, "the slide starts from where it is going, not where it was",
     "      const delta = was - node.getBoundingClientRect().left;",
     "      const delta = node.getBoundingClientRect().left - was;", M),
    (T, "positions are taken after the rebuild, when they are already new",
     '  draw() {\n    const before = this.positions();',
     '  draw() {\n    const before = new Map();', M),
]
