F = "src/ficelo/files.py"
G = "src/ficelo/git.py"
SV = "src/ficelo/server.py"
SY = "src/ficelo/web/js/sync.js"
TB = "src/ficelo/web/js/tabs.js"
VW = "src/ficelo/web/js/viewers.js"
ST = "src/ficelo/web/js/settings.js"

CASES = [
    # --- conflict detection and file management -------------------------
    (F, "stale-stamp check removed (a save overwrites another editor)",
     "    if expect is not None and stamp(path) != expect:", "    if False:", "tests.test_server"),
    (F, "stamp ignores size, so two saves in one second collide",
     '    return f"{info.st_mtime_ns}:{info.st_size}"',
     '    return f"{info.st_mtime_ns // 1000000000}"', "tests.test_server"),
    (F, "create lets the OSError through, leaking the disk path",
     '    if path.exists():\n        raise FileExistsError("something is already there")\n',
     "", "tests.test_server"),
    (F, "rename clobbers whatever is at the target",
     '    if target.exists() or target.is_symlink():\n        raise FileExistsError("something is already at the new name")\n',
     '', "tests.test_server"),
    (F, "delete becomes recursive",
     '    elif path.is_dir():\n        try:\n            path.rmdir()',
     '    elif path.is_dir():\n        try:\n            __import__("shutil").rmtree(str(path))',
     "tests.test_server"),
    (F, "permissions not preserved on save",
     "            os.chmod(str(temp), stat_module.S_IMODE(path.stat().st_mode))",
     "            pass", "tests.test_files"),

    # --- git ------------------------------------------------------------
    (G, "compares against the base tip instead of the merge base",
     '        return run(repo, "merge-base", "HEAD", base).strip()', "        return base",
     "tests.test_git"),
    (G, "untracked files dropped from the review",
     '    untracked = run(repo, "ls-files", "--others", "--exclude-standard", "-z")',
     '    untracked = ""', "tests.test_git"),
    (G, "renames no longer detected",
     '    raw = run(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--find-renames", against, "--")',
     '    raw = run(repo, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--no-renames", against, "--")', "tests.test_git"),
    (G, "an unknown base silently falls back instead of erroring",
     '    if wanted:\n        raise GitFailed(f"no such revision: {wanted}")\n', '',
     "tests.test_git"),
    (G, "commit records split on newlines rather than the separator",
     '    fields = run(repo, *args).split("\\0")', '    fields = run(repo, *args).split("\\n")',
     "tests.test_git"),
    (G, "rev ignored, so one commit's details report the newest",
     '    if rev:\n        args.append(resolve_revision(repo, rev))', '    if rev:\n        args.append("HEAD")', "tests.test_git"),

    # --- server ----------------------------------------------------------
    (SV, "mutating endpoints no longer check writability",
     '    def require_writable(self):\n        if not self.config.writable:',
     '    def require_writable(self):\n        if False:', "tests.test_server"),
    (SV, "the served root can be deleted",
     '        if target == Path(os.path.realpath(self.config.root)):\n            raise Failed(HTTPStatus.BAD_REQUEST, "that is the folder being served")\n        try:\n            files.remove(target)',
     '        try:\n            files.remove(target)', "tests.test_server"),
    (SV, "svg served inline again",
     '"image/x-icon", "image/vnd.microsoft.icon", "image/avif",',
     '"image/x-icon", "image/vnd.microsoft.icon", "image/avif", "image/svg+xml",',
     "tests.test_server"),

    # --- frontend pure functions -----------------------------------------
    (SY, "line-to-top mapping stops interpolating",
     "  return a.top + ((line - a.line) / reach) * (b.top - a.top);", "  return a.top;",
     "tests.test_modules"),
    (SY, "top-to-line mapping stops interpolating",
     "  return a.line + ((top - a.top) / reach) * (b.line - a.line);", "  return a.line;",
     "tests.test_modules"),
    (SY, "anchor list loses its end sentinel",
     '  anchors.push({\n    line: Math.max(lastLine || 0, anchors[anchors.length - 1].line + 1),\n    top: topWithin(container, final) + final.offsetHeight,\n  });\n',
     '', "tests.test_modules"),
    (TB, "tab age bands collapse to one",
     "    if (days < AGE_DAYS[i]) return \"age-\" + i;",
     "    if (days < 100000) return \"age-0\";", "tests.test_modules"),
    (TB, "a future timestamp is treated as ancient",
     "  if (days < 0) return \"age-0\";", "  if (days < 0) return \"age-\" + AGE_DAYS.length;",
     "tests.test_modules"),
    (TB, 'last-tab spacing becomes negative',
     '    if (slot.last) node.style.marginLeft = Math.max(0, delta) + "px";', '    if (slot.last) node.style.marginLeft = delta + "px";',
     'tests.test_browser.TabSlideTest'),
    (VW, "quoted csv fields stop being honoured",
     '    if (ch === \'"\' && field === "") {', "    if (false) {", "tests.test_modules"),
    (ST, "appearance values no longer clamped",
     '  const bounded = Math.min(high, Math.max(low, number));', '  const bounded = number;', "tests.test_modules"),
    (ST, "remembered scroll positions grow without bound",
     "    keys.slice(0, keys.length - MAX_REMEMBERED_SCROLLS).forEach((key) => delete all[key]);",
     "    void keys;", "tests.test_modules"),
]
