/* A right-click menu.

   One at a time, dismissed by anything that is not a click on itself, and
   nudged back on screen when it would open past an edge. */

import { el } from "./util.js";

let open = null;
let previousFocus = null;

export function closeMenu(restoreFocus = true) {
  if (open) {
    open.remove();
    open = null;
    if (restoreFocus && previousFocus?.isConnected) previousFocus.focus();
    previousFocus = null;
  }
}

/* `items` is a list of {label, run, danger} or the string "-" for a divider.
   An item with no `run` is shown greyed. */
export function showMenu(x, y, items) {
  closeMenu();
  previousFocus = document.activeElement;
  const menu = el("div", { className: "menu", tabIndex: -1 });
  menu.setAttribute("role", "menu");
  for (const item of items) {
    if (item === "-") {
      menu.append(el("div", { className: "menu-divider" }));
      continue;
    }
    const button = el("button", {
      className: "menu-item" + (item.danger ? " is-danger" : ""),
      textContent: item.label,
      disabled: !item.run,
    });
    button.setAttribute("role", "menuitem");
    if (item.run) {
      button.addEventListener("click", (event) => {
        /* Stopped here, or this same click carries on to the document
           listener below — which by then sees whatever the action opened,
           decides the click landed outside it, and shuts it again. */
        event.stopPropagation();
        closeMenu();
        item.run();
      });
    }
    menu.append(button);
  }
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  menu.style.maxHeight = Math.max(80, window.innerHeight - 12) + "px";
  menu.style.overflowY = "auto";
  menu.addEventListener("keydown", (event) => {
    const buttons = [...menu.querySelectorAll("button:not(:disabled)")];
    const at = buttons.indexOf(document.activeElement);
    let next;
    if (event.key === "ArrowDown") next = (at + 1) % buttons.length;
    else if (event.key === "ArrowUp") next = (at - 1 + buttons.length) % buttons.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = buttons.length - 1;
    else if (event.key === "Escape") { event.preventDefault(); closeMenu(); }
    else if (event.key === "Tab") closeMenu(false);
    if (next !== undefined && buttons[next]) { event.preventDefault(); buttons[next].focus(); }
  });
  document.body.append(menu);

  /* Measured after insertion, because the width depends on the labels. */
  const box = menu.getBoundingClientRect();
  if (box.right > window.innerWidth) {
    menu.style.left = Math.max(4, window.innerWidth - box.width - 6) + "px";
  }
  if (box.bottom > window.innerHeight) {
    menu.style.top = Math.max(4, window.innerHeight - box.height - 6) + "px";
  }

  open = menu;
  menu.querySelector("button:not(:disabled)")?.focus();
  return menu;
}

/* A card of facts, positioned like a menu but not interactive. */
export function showInfo(x, y, rows) {
  closeMenu();
  previousFocus = document.activeElement;
  const card = el("div", { className: "menu menu-info", tabIndex: 0 });
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-label", "File information");
  for (const [label, value] of rows) {
    card.append(
      el("div", { className: "info-row" }, [
        el("span", { className: "info-label", textContent: label }),
        el("span", { className: "info-value", textContent: value }),
      ])
    );
  }
  card.style.left = x + "px";
  card.style.top = y + "px";
  document.body.append(card);
  const box = card.getBoundingClientRect();
  if (box.right > window.innerWidth) {
    card.style.left = Math.max(4, window.innerWidth - box.width - 6) + "px";
  }
  if (box.bottom > window.innerHeight) {
    card.style.top = Math.max(4, window.innerHeight - box.height - 6) + "px";
  }
  open = card;
  card.focus();
  return card;
}

export function wireDismissal() {
  document.addEventListener("pointerdown", (event) => {
    if (open && !open.contains(event.target)) closeMenu(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
  window.addEventListener("resize", () => closeMenu(false));
  /* A second right-click elsewhere should move the menu, not stack one on
     another; each showMenu closes the previous, so only scroll needs this. */
  window.addEventListener("scroll", (event) => {
    if (open && !open.contains(event.target)) closeMenu(false);
  }, true);
}
