/* Native modal focus management, with the same controls as the rest of the app. */
import { el } from "./util.js";

export function dismissOnBackdrop(dialog, dismiss) {
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right ||
        event.clientY < box.top || event.clientY > box.bottom) dismiss();
  });
}

export function ask(title, message, { value, accept = "Continue", danger = false } = {}) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const dialog = el("dialog", { className: "app-dialog" });
    const heading = el("h2", { textContent: title, id: "dialog-title" });
    dialog.setAttribute("aria-labelledby", heading.id);
    const input = value === undefined ? null : el("input", { value, className: "dialog-input" });
    const cancel = el("button", { type: "button", textContent: "Cancel" });
    const confirm = el("button", { type: "submit", textContent: accept, className: danger ? "is-danger" : "" });
    const form = el("form", {}, [heading, el("p", { textContent: message }), input,
      el("div", { className: "dialog-actions" }, [cancel, confirm])]);
    const finish = (answer) => { dialog.close(); dialog.remove(); previous?.focus(); resolve(answer); };
    form.addEventListener("submit", (event) => { event.preventDefault(); finish(input ? input.value : true); });
    cancel.addEventListener("click", () => finish(null));
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(null); });
    dismissOnBackdrop(dialog, () => finish(null));
    dialog.append(form); document.body.append(dialog); dialog.showModal();
    (input || cancel).focus(); input?.select();
  });
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
  const previous = document.activeElement;
  const field = el("textarea", { value: text });
  field.style.cssText = "position:fixed;left:-10000px;top:0";
  document.body.append(field); field.select();
  let copied = false;
  try { copied = document.execCommand("copy"); } catch (_) {
    // Some browsers throw instead of returning false when copying is blocked.
  } finally {
    field.remove(); previous?.focus({ preventScroll: true });
  }
  if (!copied) await ask("Copy text", "Select and copy this text.", { value: text, accept: "Done" });
  return copied;
}
