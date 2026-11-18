/* Diagrams load on demand and retain their source for theme changes/retries. */
let loading = null;
let darkTheme = false;
let sequence = 0;
let pendingRender = Promise.resolve();
const sources = new WeakMap();
const generations = new WeakMap();

export function loadMermaid(dark = darkTheme) {
  darkTheme = dark;
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/vendor/mermaid/mermaid.min.js";
      // The bundled IIFE registers its global directly. Leave Monaco's AMD
      // loader available while this script downloads and executes.
      const finish = (error) => {
        script.onload = script.onerror = null;
        if (error) { script.remove(); reject(error); }
        else resolve(window.mermaid);
      };
      script.onload = () => finish(window.mermaid ? null : new Error("The diagram library did not register"));
      script.onerror = () => finish(new Error("The diagram library could not be loaded"));
      document.head.append(script);
    }).catch((error) => { loading = null; throw error; });
  }
  return loading;
}

export function setMermaidTheme(dark) {
  // A theme change can arrive while the library or an earlier diagram loads.
  darkTheme = dark;
}

function showError(holder, error, unavailable = false) {
  holder.classList.add("is-diagram-error");
  holder.replaceChildren(
    Object.assign(document.createElement("div"), { className: "diagram-note", textContent: unavailable
      ? "Diagram engine unavailable — showing the source." : "Diagram error: " + String(error?.message || error) }),
    Object.assign(document.createElement("pre"), { textContent: sources.get(holder) })
  );
}

export async function renderDiagrams(root, dark = darkTheme) {
  setMermaidTheme(dark);
  root.querySelectorAll("pre > code.language-mermaid").forEach((code) => {
    const holder = document.createElement("div");
    holder.className = "mermaid-figure";
    sources.set(holder, code.textContent);
    holder.append(Object.assign(document.createElement("pre"), { textContent: code.textContent }));
    code.parentElement.replaceWith(holder);
  });
  const holders = [...root.querySelectorAll(".mermaid-figure")].filter((node) => sources.has(node));
  if (!holders.length) return 0;
  const generation = (generations.get(root) || 0) + 1;
  generations.set(root, generation);
  const current = (holder) => generations.get(root) === generation && root.contains(holder);
  let mermaid;
  try { mermaid = await loadMermaid(); }
  catch (error) {
    holders.forEach((holder) => { if (current(holder)) showError(holder, error, true); });
    return 0;
  }

  let count = 0;
  for (const holder of holders) {
    /* Mermaid has global configuration and temporary DOM. Serialize drawings
       so an older asynchronous render cannot replace a newer theme. */
    const render = pendingRender.then(async () => {
      if (!current(holder)) return;
      const id = "ficelo-diagram-" + ++sequence;
      // Mermaid measures temporary SVGs in the DOM. Keep that work outside
      // the app's flex layout so it cannot move the status bar or show below it.
      const container = document.createElement("div");
      container.style.cssText = "position:fixed;inset:0;overflow:hidden;visibility:hidden;pointer-events:none";
      container.setAttribute("aria-hidden", "true");
      document.body.append(container);
      try {
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: darkTheme ? "dark" : "default" });
        const { svg } = await mermaid.render(id, sources.get(holder), container);
        if (!current(holder)) return;
        holder.classList.remove("is-diagram-error");
        holder.innerHTML = svg;
        count += 1;
      } catch (error) {
        if (current(holder)) showError(holder, error);
      } finally {
        container.remove();
        // Some Mermaid errors leave a temporary SVG outside the holder.
        const leftover = document.getElementById("d" + id);
        if (leftover && !holder.contains(leftover)) leftover.remove();
        const svg = document.getElementById(id);
        if (svg && !holder.contains(svg)) svg.remove();
      }
    });
    pendingRender = render.catch(() => {});
    await pendingRender;
  }
  return count;
}
