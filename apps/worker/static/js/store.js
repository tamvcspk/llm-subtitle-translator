// Shared data fetched from the worker, with change notifications so the
// sidebar, banner and views stay in sync from a single poll.
import { get } from "./api.js";

export const store = {
  status: null, // GET /api/status
  queue: null, // GET /api/queue
  queueError: null,
  languages: null, // GET /api/languages items, cached for the page lifetime
  models: null, // GET /api/gemini-models items
};

const bus = new EventTarget();

export function publish(name, value) {
  store[name] = value;
  bus.dispatchEvent(new Event(name));
}

export function subscribe(name, listener) {
  bus.addEventListener(name, () => listener(store[name]));
}

let queueRequest = null;

export function refreshQueue() {
  queueRequest ??= get("/api/queue")
    .then((snapshot) => {
      store.queueError = null;
      publish("queue", snapshot);
      return snapshot;
    })
    .catch((error) => {
      store.queueError = error;
      publish("queue", store.queue);
      throw error;
    })
    .finally(() => { queueRequest = null; });
  return queueRequest;
}

let statusRequest = null;

/** Status includes a Bazarr ping, so it is refreshed on demand, not polled. */
export function refreshStatus() {
  statusRequest ??= get("/api/status")
    .then((status) => {
      publish("status", status);
      return status;
    })
    .finally(() => { statusRequest = null; });
  return statusRequest;
}

export async function loadLanguages() {
  if (!store.languages) {
    const data = await get("/api/languages");
    publish("languages", data.items || []);
  }
  return store.languages;
}

export async function loadModels() {
  if (!store.models) {
    const data = await get("/api/gemini-models");
    store.models = data.items || [];
  }
  return store.models;
}

export function languageLabel(item) {
  const match = (store.languages || []).find((language) => language.code === item.code);
  return `${item.code} · ${match ? match.name : item.language || item.code}`;
}

export function enabledLanguages(list) {
  return (list || []).filter((item) => item.enabled !== false);
}
