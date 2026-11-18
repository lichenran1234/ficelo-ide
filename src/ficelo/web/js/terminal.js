/* A terminal is transient: output and running shells are never session state. */
import { el } from "./util.js";

async function call(payload) {
  const response = await fetch("/api/terminal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Terminal request failed");
  return body;
}

let libraries;
function load() {
  if (!libraries) libraries = Promise.all([
    import("/vendor/xterm/xterm.mjs"), import("/vendor/xterm-fit/addon-fit.mjs"),
    new Promise((resolve, reject) => {
      const link = el("link", { rel: "stylesheet", href: "/vendor/xterm/xterm.css" });
      link.onload = resolve; link.onerror = reject; document.head.append(link);
    }),
  ]).catch((error) => { libraries = null; throw error; });
  return libraries;
}

export class TerminalView {
  constructor(path) {
    this.path = path;
    this.element = el("div", { className: "terminal-view" });
    this.cursor = 0;
    this.queue = Promise.resolve();
    this.disposed = false;
  }

  async start() {
    const [{ Terminal }, { FitAddon }] = await load();
    if (this.disposed) return;
    this.terminal = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 5000,
      fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace' });
    this.fit = new FitAddon(); this.terminal.loadAddon(this.fit);
    this.terminal.open(this.element);
    this.terminal.attachCustomKeyEventHandler((event) =>
      !((event.ctrlKey || event.metaKey) && !event.altKey && event.code === "Backquote"));
    this.theme(); this.fit.fit();
    this.ready = new Promise((resolve) => { this.readyToSend = resolve; });
    this.terminal.onData((text) => this.input(text));
    const result = await call({ action: "open", path: this.path, cols: this.terminal.cols, rows: this.terminal.rows });
    this.id = result.id;
    if (this.disposed) { await call({ action: "close", id: this.id }); return; }
    this.readyToSend();
    this.element.dataset.ready = "true";
    this.terminal.onResize(({ cols, rows }) => this.enqueue({ action: "resize", cols, rows }));
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(this.element);
    this.themeObserver = new MutationObserver(() => this.theme());
    this.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    this.poll(); this.terminal.focus();
  }

  theme() {
    const styles = getComputedStyle(document.documentElement);
    this.terminal.options.theme = { background: styles.getPropertyValue("--bg").trim(),
      foreground: styles.getPropertyValue("--ink").trim(), cursor: styles.getPropertyValue("--accent").trim() };
  }

  enqueue(payload) {
    this.queue = this.queue.then(async () => { await this.ready; return this.disposed ? null : call({ ...payload, id: this.id }); })
      .catch((error) => this.terminal?.writeln("\r\n" + error.message));
  }

  input(text) {
    const bytes = new TextEncoder().encode(text);
    for (let at = 0; at < bytes.length; at += 32768) {
      const part = bytes.subarray(at, at + 32768);
      const data = btoa(Array.from(part, (byte) => String.fromCharCode(byte)).join(""));
      this.enqueue({ action: "input", data });
    }
  }

  async poll() {
    if (this.disposed) return;
    try {
      const response = await fetch("/api/terminal?id=" + encodeURIComponent(this.id) + "&after=" + this.cursor);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error);
      if (this.disposed) return;
      if (body.truncated) this.terminal.writeln("\r\n[Older terminal output was discarded]\r\n");
      if (body.data) this.terminal.write(Uint8Array.from(atob(body.data), (char) => char.charCodeAt(0)));
      this.cursor = body.cursor;
      if (body.exited && !body.data) { this.terminal.writeln("\r\n[Shell exited]"); return; }
    } catch (error) {
      if (!this.disposed) this.terminal.writeln("\r\n" + error.message);
      return;
    }
    this.timer = setTimeout(() => this.poll(), document.hidden || !this.element.isConnected ? 1000 : 100);
  }

  resize() {
    if (this.element.clientWidth && this.element.clientHeight) this.fit?.fit();
  }

  focus() { this.resize(); this.terminal?.focus(); }

  dispose(closing = false) {
    if (this.disposed) return;
    this.disposed = true; this.readyToSend?.(); clearTimeout(this.timer);
    this.observer?.disconnect(); this.themeObserver?.disconnect(); this.terminal?.dispose();
    if (this.id) fetch("/api/terminal", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "close", id: this.id }), keepalive: closing }).catch(() => {});
  }
}
