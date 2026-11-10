// DOM helpers shared by every view. Text always goes through text nodes, never
// innerHTML, because paths and errors come from the filesystem and providers.
import { formatDuration, formatRelative, nowSeconds } from "./format.js";

const SVG_NS = "http://www.w3.org/2000/svg";

// Static, trusted icon markup (24x24, stroked).
const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  queue: '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>',
  wanted: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  scan: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  system: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
  archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
  logs: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
  refresh: '<path d="M21 12a9 9 0 0 1-15.36 6.36L3 16"/><path d="M3 12a9 9 0 0 1 15.36-6.36L21 8"/><path d="M21 3v5h-5M3 21v-5h5"/>',
  retry: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5M12 3v12"/>',
  trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3z"/><path d="M12 9v4M12 17h.01"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z"/>',
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
};

export function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name] || "";
  return svg;
}

/** Prepend icons to static markup that declares `data-icon`. */
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    if (!el.querySelector(":scope > svg.icon")) el.prepend(icon(el.dataset.icon));
  });
}

/** Create an element: h("div", { class: "row", onClick }, child, "text"). */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function toast(message, kind = "info") {
  const container = document.getElementById("toasts");
  const el = h("div", { class: `toast toast-${kind}` },
    icon(kind === "error" ? "alert" : kind === "success" ? "check" : "clock"),
    h("span", { text: message }));
  container.append(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 250);
  }, kind === "error" ? 7000 : 3500);
}

/** Resolve true when the user confirms. Uses the shared <dialog>. */
export function confirmDialog({ title, message = "", confirmLabel = "Confirm", dismissLabel = "Cancel", danger = false }) {
  const dialog = document.getElementById("confirm-dialog");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-message").textContent = message;
  const accept = document.getElementById("confirm-accept");
  accept.textContent = confirmLabel;
  accept.className = `btn ${danger ? "btn-danger-solid" : "btn-primary"}`;
  document.getElementById("confirm-dismiss").textContent = dismissLabel;
  dialog.returnValue = "cancel";
  dialog.showModal();
  accept.focus();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

/**
 * Run an async button action: disable while busy and surface failures as a
 * toast. Set `button.dataset.done` inside the handler to keep it disabled.
 */
export async function runAction(button, handler) {
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add("is-busy");
  try {
    await handler(button);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.classList.remove("is-busy");
    if (!button.dataset.done) button.disabled = false;
  }
}

export function bindAction(id, handler) {
  const button = document.getElementById(id);
  button.addEventListener("click", () => runAction(button, handler));
  return button;
}

export function actionButton(label, handler, { variant = "", iconName, size = "btn-sm" } = {}) {
  const button = h("button", { type: "button", class: ["btn", size, variant].filter(Boolean).join(" ") },
    iconName && icon(iconName), label);
  button.addEventListener("click", () => runAction(button, handler));
  return button;
}

export function emptyState(title, text = "", iconName = "inbox") {
  return h("div", { class: "empty" }, icon(iconName), h("strong", { text: title }), text && h("p", { text }));
}

export function loadingState(text) {
  return h("div", { class: "empty empty-loading" }, h("span", { class: "spinner" }), h("p", { text }));
}

export function errorState(text, retry) {
  return h("div", { class: "empty empty-error" }, icon("alert"), h("strong", { text: "Could not load" }),
    h("p", { text }), retry && actionButton("Try again", retry, { iconName: "refresh" }));
}

/** Language pair pill: "en → zh". */
export function pairPill(source, target) {
  return h("span", { class: "pair" }, h("span", { text: source || "?" }), icon("arrow"), h("span", { text: target || "?" }));
}

/**
 * Live timers in rendered markup:
 *   data-since="<epoch>"  elapsed duration
 *   data-until="<epoch>"  remaining duration
 *   data-rel="<epoch>"    relative time ("3 minutes ago")
 */
export function updateClocks(root = document) {
  const now = nowSeconds();
  root.querySelectorAll("[data-since]").forEach((el) => { el.textContent = formatDuration(now - Number(el.dataset.since)); });
  root.querySelectorAll("[data-until]").forEach((el) => { el.textContent = formatDuration(Number(el.dataset.until) - now); });
  root.querySelectorAll("[data-rel]").forEach((el) => { el.textContent = formatRelative(Number(el.dataset.rel)); });
}

/** Replace children while keeping open <details data-key> expanded. */
export function replaceKeepingOpen(container, children) {
  const open = new Set(Array.from(container.querySelectorAll("details[open][data-key]"), (el) => el.dataset.key));
  container.replaceChildren(...children);
  container.querySelectorAll("details[data-key]").forEach((el) => { if (open.has(el.dataset.key)) el.open = true; });
  updateClocks(container);
}
