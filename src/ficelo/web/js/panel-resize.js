/* A vertical separator controlling a panel's width. The caller owns layout
   and persistence; cancelled drags restore the width without committing it. */
export function wireResize(handle, { getWidth, getBounds, onResize, onEnd = () => {}, onCancel = () => {}, direction = 1 }) {
  const doc = handle.ownerDocument;
  const view = doc.defaultView;
  const sign = direction < 0 ? -1 : 1;
  const attributes = ["role", "tabindex", "aria-orientation", "aria-valuemin", "aria-valuemax", "aria-valuenow", "aria-valuetext"];
  const original = new Map(attributes.map((name) => [name, handle.getAttribute(name)]));
  const touchAction = handle.style.getPropertyValue("touch-action");
  const touchPriority = handle.style.getPropertyPriority("touch-action");
  let drag = null, frame = null, nextWidth = null, destroyed = false;

  const bounds = () => {
    const { min, max } = getBounds();
    const lower = Math.max(0, Math.round(min));
    return { min: lower, max: Math.max(lower, Math.round(max)) };
  };
  const clamp = (width) => {
    const { min, max } = bounds();
    return Math.max(min, Math.min(max, Math.round(width)));
  };
  const describe = (width) => {
    const { min, max } = bounds();
    handle.setAttribute("aria-valuemin", String(min));
    handle.setAttribute("aria-valuemax", String(max));
    handle.setAttribute("aria-valuenow", String(width));
    handle.setAttribute("aria-valuetext", width + " pixels");
  };
  const sync = () => { if (!destroyed) describe(clamp(getWidth())); };
  const apply = (width) => {
    const value = clamp(width);
    onResize(value);
    describe(value);
    return value;
  };
  const flush = () => {
    if (frame !== null) view.cancelAnimationFrame(frame);
    frame = null;
    if (nextWidth !== null) {
      const value = nextWidth;
      nextWidth = null;
      return apply(value);
    }
  };
  const restoreStyle = (style, name, value, priority) => {
    if (value) style.setProperty(name, value, priority);
    else style.removeProperty(name);
  };

  function finish(commit, event = null) {
    if (!drag) return;
    const prior = drag;
    drag = null;
    doc.removeEventListener("pointermove", move, true);
    doc.removeEventListener("pointerup", up, true);
    doc.removeEventListener("pointercancel", cancelled, true);
    doc.removeEventListener("keydown", cancelKey, true);
    view.removeEventListener("blur", cancel);
    restoreStyle(doc.documentElement.style, "user-select", prior.selection, prior.selectionPriority);
    restoreStyle(doc.documentElement.style, "cursor", prior.cursor, prior.cursorPriority);
    if (!prior.draggingClass) handle.classList.remove("is-dragging");
    if (handle.hasPointerCapture(prior.id)) handle.releasePointerCapture(prior.id);
    if (event) nextWidth = prior.width + sign * (event.clientX - prior.x);
    if (!commit) nextWidth = prior.width;
    const width = flush();
    if (commit) onEnd(width ?? clamp(getWidth()));
    else onCancel();
  }
  function cancel() { finish(false); }
  function cancelKey(event) {
    if (event.key !== "Escape") return;
    event.preventDefault(); event.stopPropagation(); cancel();
  }
  function move(event) {
    if (!drag || event.pointerId !== drag.id) return;
    event.preventDefault();
    nextWidth = drag.width + sign * (event.clientX - drag.x);
    if (frame === null) frame = view.requestAnimationFrame(() => { frame = null; flush(); });
  }
  function up(event) { if (drag && event.pointerId === drag.id) finish(true, event); }
  function cancelled(event) { if (drag && event.pointerId === drag.id) cancel(); }
  function down(event) {
    if (destroyed || drag || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault();
    handle.focus({ preventScroll: true });
    const style = doc.documentElement.style;
    drag = { id: event.pointerId, x: event.clientX, width: clamp(getWidth()),
      selection: style.getPropertyValue("user-select"), selectionPriority: style.getPropertyPriority("user-select"),
      cursor: style.getPropertyValue("cursor"), cursorPriority: style.getPropertyPriority("cursor"),
      draggingClass: handle.classList.contains("is-dragging") };
    style.setProperty("user-select", "none");
    style.setProperty("cursor", "col-resize");
    handle.classList.add("is-dragging");
    doc.addEventListener("pointermove", move, { capture: true, passive: false });
    doc.addEventListener("pointerup", up, true);
    doc.addEventListener("pointercancel", cancelled, true);
    doc.addEventListener("keydown", cancelKey, true);
    view.addEventListener("blur", cancel);
    try { handle.setPointerCapture(event.pointerId); }
    catch (_) { /* A detached handle or synthetic event can lack capture. */ }
  }
  function key(event) {
    if (drag || event.altKey || event.ctrlKey || event.metaKey) return;
    const step = event.shiftKey ? 50 : 10;
    const { min, max } = bounds();
    let width;
    if (event.key === "ArrowLeft") width = getWidth() - sign * step;
    else if (event.key === "ArrowRight") width = getWidth() + sign * step;
    else if (event.key === "Home") width = min;
    else if (event.key === "End") width = max;
    else return;
    event.preventDefault(); event.stopPropagation();
    onEnd(apply(width));
  }

  handle.setAttribute("role", "separator");
  handle.setAttribute("tabindex", "0");
  handle.setAttribute("aria-orientation", "vertical");
  handle.style.setProperty("touch-action", "none");
  handle.addEventListener("pointerdown", down);
  handle.addEventListener("keydown", key);
  handle.addEventListener("lostpointercapture", cancelled);
  sync();
  return {
    sync, cancel,
    destroy() {
      if (destroyed) return;
      cancel();
      destroyed = true;
      handle.removeEventListener("pointerdown", down);
      handle.removeEventListener("keydown", key);
      handle.removeEventListener("lostpointercapture", cancelled);
      restoreStyle(handle.style, "touch-action", touchAction, touchPriority);
      for (const [name, value] of original) {
        if (value === null) handle.removeAttribute(name);
        else handle.setAttribute(name, value);
      }
    },
  };
}
