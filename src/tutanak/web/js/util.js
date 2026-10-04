// DOM and formatting helpers. Text always goes in through textContent, never innerHTML:
// transcripts and model output are untrusted.

const SVG_NS = "http://www.w3.org/2000/svg";

const ICONS = {
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  speaker: "M4 9v6h4l5 4V5L8 9H4zM16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11",
  screen: "M3 5h18v11H3zM8 20h8M12 16v4",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.9-3.9",
  settings: "M4 7h10M18 7h2M4 17h2M10 17h10M14 4.5v5M6 14.5v5",
  play: "M7 4.5v15l12-7.5z",
  pause: "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z",
  stop: "M6 6h12v12H6z",
  bookmark: "M7 4h10v16l-5-4-5 4z",
  download: "M12 4v11M7 11l5 5 5-5M5 20h14",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  refresh: "M19 12a7 7 0 1 1-2.1-5M19 4v4h-4",
  back: "M14 6l-6 6 6 6",
  lock: "M7 11V8a5 5 0 0 1 10 0v3M5 11h14v9H5z",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18 16l.8 2.2L21 19l-2.2.8L18 22l-.8-2.2L15 19l2.2-.8z",
  upload: "M12 16V5M7 9l5-5 5 5M5 20h14",
  close: "M6 6l12 12M18 6L6 18",
  check: "M5 12.5l4.5 4.5L19 7.5",
  clock: "M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16zM12 8v4.5l3 2",
  note: "M6 4h12v16H6zM9 8h6M9 12h6M9 16h3",
  slides: "M3 5h18v12H3zM9 21h6M12 17v4",
  rewind: "M11 6l-7 6 7 6zM20 6l-7 6 7 6z",
};

const FILLED = new Set(["play", "pause", "stop", "bookmark", "spark", "rewind"]);

export function icon(name, size = 18) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("icon");
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || "");
  if (FILLED.has(name)) {
    path.setAttribute("fill", "currentColor");
  } else {
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.8");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
  }
  svg.append(path);
  return svg;
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key in el && !["list", "form", "type"].includes(key)) el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

export function fmtTs(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hrs = Math.floor(total / 3600);
  const min = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return hrs ? `${hrs}:${pad(min)}:${pad(sec)}` : `${pad(min)}:${pad(sec)}`;
}

// Mirrors tutanak.textutil.fold: case- and diacritic-insensitive, Turkish-aware.
export function fold(text) {
  return text
    .replaceAll("İ", "i")
    .replaceAll("I", "i")
    .toLowerCase()
    .replaceAll("ı", "i")
    .normalize("NFKD")
    .replace(/\p{M}/gu, "");
}

export function highlight(text, terms) {
  const fragment = document.createDocumentFragment();
  if (!terms || !terms.length) {
    fragment.append(text);
    return fragment;
  }
  let last = 0;
  for (const match of text.matchAll(/[\p{L}\p{N}_]+/gu)) {
    const folded = fold(match[0]);
    if (!terms.some((term) => folded.startsWith(term))) continue;
    fragment.append(text.slice(last, match.index));
    fragment.append(h("mark", null, match[0]));
    last = match.index + match[0].length;
  }
  fragment.append(text.slice(last));
  return fragment;
}

export function queryTerms(query) {
  return [...new Set(fold(query).match(/[\p{L}\p{N}_]+/gu) || [])];
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// Index of the last item whose `key` is <= value (items sorted by key), or -1.
export function lastAtOrBefore(items, value, key) {
  let lo = 0;
  let hi = items.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid][key] <= value) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

export function toast(message, kind = "info") {
  let host = document.querySelector(".toasts");
  if (!host) {
    host = h("div", { class: "toasts", role: "status" });
    document.body.append(host);
  }
  const el = h("div", { class: `toast ${kind}` }, message);
  host.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
}
