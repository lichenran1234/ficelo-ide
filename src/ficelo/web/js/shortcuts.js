/* Shortcut hints follow the browser's platform, not the server's OS. */
export function primaryShortcut(key, browser = navigator) {
  const platform = browser.userAgentData?.platform || browser.platform || browser.userAgent || "";
  // iPad browsers can identify as MacIntel without running macOS.
  const mac = /mac/i.test(platform) && !(browser.maxTouchPoints > 1);
  return (mac ? "⌘" : "Ctrl+") + key;
}

/* Shared with the sandboxed HTML preview by serialising this function.
   Keep it self-contained so every view recognises the same shortcuts. */
export function shortcutFor(event) {
  const target = event.composedPath?.()[0] || event.target;
  if (event.defaultPrevented || event.isComposing || event.keyCode === 229 ||
      event.target?.closest?.("dialog") || target?.closest?.("dialog")) return null;
  const meta = event.metaKey || event.ctrlKey;
  const alt = event.altKey;
  // AltGr must keep producing text, rather than triggering Alt shortcuts.
  if (meta && alt) return null;
  const physical = { BracketLeft: "[", BracketRight: "]" };
  const key = (alt && /^(Key[A-Z]|Digit[1-9]|BracketLeft|BracketRight)$/.test(event.code)
    ? physical[event.code] || event.code.replace(/^(Key|Digit)/, "") : event.key || "").toLowerCase();
  const typing = /^(INPUT|TEXTAREA)$/.test(target?.tagName) || target?.isContentEditable ||
    target?.closest?.('[role="textbox"]');
  if (meta && (event.code === "Backquote" || key === "`")) return "terminal";
  if (event.target?.closest?.(".terminal-view")) return null;
  if (meta && key === "s" || alt && !event.shiftKey && key === "s") return "save";
  if (meta && (!event.shiftKey && key === "p" || key === "k")) return "find";
  if (meta && key === "b") return "sidebar";
  if (!alt) return null;
  if (key === "r") return "reload";
  if (event.shiftKey && (key === "w" || key === "t")) return "reopen";
  if (!event.shiftKey && key === "w") return "close";
  if (!typing && event.shiftKey && key === "arrowleft") return "move-left";
  if (!typing && event.shiftKey && key === "arrowright") return "move-right";
  if (!typing && key === "arrowleft" || key === "[") return "previous";
  if (!typing && key === "arrowright" || key === "]") return "next";
  if (/^[1-9]$/.test(key)) return "tab-" + key;
  return null;
}
