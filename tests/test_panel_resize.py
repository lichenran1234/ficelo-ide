"""Width dividers share pointer, keyboard and cancellation behavior."""

from cdp import Browser
from test_modules import ModuleTest


class PanelResizeTest(ModuleTest):
    def results(self):
        # Unlike dump-dom virtual time, CDP advances animation frames.
        browser = Browser()
        try:
            browser.navigate(f"http://127.0.0.1:{self.port}/")
            browser.wait_for("document.getElementById('report')?.textContent.startsWith('REPORT')",
                             what="panel resize checks")
            return browser.evaluate("JSON.parse(document.getElementById('report').textContent.slice(6, -9))")
        finally:
            browser.close()

    checks = r'''
import { wireResize } from "/js/panel-resize.js";

function separator(options = {}) {
  const handle = document.createElement("div");
  handle.setAttribute("aria-label", "Panel width");
  document.body.append(handle);
  let width = 240, limits = {min: 120, max: 400};
  const changes = [], commits = [];
  const resize = wireResize(handle, {
    getWidth: () => width, getBounds: () => limits,
    onResize: value => { width = value; changes.push(value); },
    onEnd: value => commits.push(value), ...options,
  });
  const pointer = (type, x, extra = {}) => handle.dispatchEvent(new PointerEvent(type, {
    pointerId: 1, isPrimary: true, button: 0, clientX: x, bubbles: true, cancelable: true, ...extra,
  }));
  const key = (value, extra = {}) => handle.dispatchEvent(new KeyboardEvent("keydown", {
    key: value, bubbles: true, cancelable: true, ...extra,
  }));
  return { handle, resize, pointer, key, changes, commits, width: () => width,
    limits: value => { limits = value; },
    dispose: () => { resize.destroy(); handle.remove(); },
  };
}

check("dragging outside a separator commits its final bounded width", () => {
  const item = separator();
  item.pointer("pointerdown", 200);
  document.body.dispatchEvent(new PointerEvent("pointermove", {pointerId: 1, clientX: 1000, bubbles: true}));
  document.body.dispatchEvent(new PointerEvent("pointerup", {pointerId: 1, clientX: 500, bubbles: true}));
  const result = [item.width(), item.commits, item.handle.getAttribute("aria-valuenow"),
    document.documentElement.style.userSelect, item.handle.classList.contains("is-dragging")];
  item.dispose();
  return eq(result, [400, [400], "400", "", false]);
});

check("right-side panels grow when dragging left and shrink at their minimum", () => {
  const item = separator({direction: -1});
  item.pointer("pointerdown", 200); item.pointer("pointerup", 130);
  const larger = item.width();
  item.pointer("pointerdown", 130); item.pointer("pointerup", 900);
  const result = [larger, item.width(), item.commits];
  item.dispose();
  return eq(result, [310, 120, [310, 120]]);
});

check("secondary buttons and pointers cannot start or finish a drag", () => {
  const item = separator();
  item.pointer("pointerdown", 100, {button: 2}); item.pointer("pointerup", 200);
  item.pointer("pointerdown", 100, {isPrimary: false}); item.pointer("pointerup", 200);
  item.pointer("pointerdown", 100);
  item.pointer("pointermove", 500, {pointerId: 2}); item.pointer("pointerup", 500, {pointerId: 2});
  item.pointer("pointerup", 120);
  const result = [item.width(), item.commits];
  item.dispose();
  return eq(result, [260, [260]]);
});

check("cancelled drags restore width and existing document styles without saving", () => {
  const style = document.documentElement.style;
  style.setProperty("user-select", "text", "important"); style.setProperty("cursor", "crosshair");
  const cancelledWidths = [];
  const item = separator({onCancel: () => cancelledWidths.push(item.width())});
  item.resize.cancel();
  for (const cause of ["pointercancel", "lostpointercapture", "Escape", "blur", "cancel"]) {
    item.pointer("pointerdown", 100); item.pointer("pointermove", 160);
    if (cause === "Escape") item.key("Escape");
    else if (cause === "blur") window.dispatchEvent(new Event("blur"));
    else if (cause === "cancel") item.resize.cancel();
    else item.pointer(cause, 160);
  }
  item.resize.cancel();
  const result = [item.width(), item.commits, cancelledWidths,
    style.userSelect, style.getPropertyPriority("user-select"), style.cursor];
  item.dispose(); style.removeProperty("user-select"); style.removeProperty("cursor");
  return eq(result, [240, [], [240, 240, 240, 240, 240], "text", "important", "crosshair"]);
});

check("keyboard arrows follow panel direction, use larger shift steps and respect limits", () => {
  const item = separator({direction: -1});
  item.key("ArrowLeft"); item.key("ArrowRight", {shiftKey: true});
  item.key("Home"); item.key("ArrowRight"); item.key("End");
  item.key("ArrowLeft", {ctrlKey: true});
  const result = [item.width(), item.commits, item.handle.getAttribute("role"),
    item.handle.getAttribute("aria-orientation"), item.handle.getAttribute("aria-valuetext")];
  item.dispose();
  return eq(result, [400, [250, 200, 120, 120, 400], "separator", "vertical", "400 pixels"]);
});

check("changing bounds updates accessible values and subsequent gestures", () => {
  const item = separator();
  item.limits({min: 150, max: 260}); item.resize.sync();
  const bounds = [item.handle.getAttribute("aria-valuemin"), item.handle.getAttribute("aria-valuemax")];
  item.pointer("pointerdown", 100); item.pointer("pointerup", 800);
  const result = [bounds, item.width(), item.commits];
  item.dispose();
  return eq(result, [["150", "260"], 260, [260]]);
});

check("committed width does not depend on synchronous layout measurements", () => {
  let cancelled = 0;
  const item = separator({getWidth: () => 240, onCancel: () => { cancelled++; }});
  item.pointer("pointerdown", 100); item.pointer("pointerup", 160);
  const result = [item.commits, cancelled];
  item.dispose();
  return eq(result, [[300], 0]);
});

check("destroying during a drag cancels pending work and restores the handle", () => {
  const cancelledWidths = [];
  const item = separator({onCancel: () => cancelledWidths.push(item.width())});
  item.pointer("pointerdown", 100); item.pointer("pointermove", 200);
  item.resize.destroy(); item.resize.destroy();
  item.pointer("pointerup", 300); item.key("End");
  const result = [item.width(), item.commits, cancelledWidths, item.handle.getAttribute("role"), item.handle.getAttribute("tabindex"),
    item.handle.getAttribute("aria-label"), item.handle.style.touchAction, document.documentElement.style.userSelect];
  item.dispose();
  return eq(result, [240, [], [240], null, null, "Panel width", "", ""]);
});

check("dragging updates the panel before release and cancellation restores it", async () => {
  const item = separator();
  item.pointer("pointerdown", 100); item.pointer("pointermove", 160);
  await new Promise(requestAnimationFrame);
  const during = item.width();
  item.pointer("pointercancel", 160);
  await new Promise(requestAnimationFrame);
  const result = [during, item.width(), item.commits];
  item.dispose();
  return eq(result, [300, 240, []]);
});
'''

    def test_panel_resize_pointer_keyboard_and_cleanup(self):
        self.assertAllPass()
