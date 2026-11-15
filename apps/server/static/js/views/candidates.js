// Bazarr wanted and Library scan list items that can be turned into jobs.
import { get, post } from "../api.js";
import { basename, dirname, plural } from "../format.js";
import { refreshQueue } from "../store.js";
import { actionButton, bindAction, confirmDialog, emptyState, errorState, h, icon, loadingState, toast } from "../ui.js";

function createCandidateView({ key, endpoint, loadingText, emptyTitle, emptyText, summarize }) {
  const state = { items: null, error: null, loading: false };
  const el = (suffix) => document.getElementById(`${key}-${suffix}`);

  async function enqueue(item, button) {
    const payload = {
      ...item,
      media_type: item.type || item.media_type || "movie",
      target_codes: (item.missing_targets || []).map((target) => target.code),
    };
    const data = await post("/api/enqueue", payload);
    item.queued = true;
    button.dataset.done = "1";
    button.replaceChildren(icon("check"), "Queued");
    toast(`Queued ${plural(data.count, "job")}`, "success");
    refreshQueue().catch(() => {});
  }

  function row(item) {
    const targets = item.missing_targets || [];
    const ready = item.can_enqueue !== false;
    let action;
    if (!ready) {
      action = h("span", { class: "status status-warn" }, h("span", { class: "dot" }), "No source subtitle");
    } else {
      action = actionButton(item.queued ? "Queued" : "Queue", (button) => enqueue(item, button), { iconName: item.queued ? "check" : "plus" });
      if (item.queued) {
        action.disabled = true;
        action.dataset.done = "1";
      }
    }
    return h("div", { class: "row" },
      h("div", { class: "row-main" },
        h("div", { class: "row-title" },
          item.type && h("span", { class: "tag", text: item.type === "series" ? "Series" : "Movie" }),
          h("span", { text: item.title || basename(item.subtitle_path) })),
        h("div", {
          class: "row-path mono",
          title: item.subtitle_path || "",
          // Scan rows are titled by file name, so the folder is enough there.
          text: item.subtitle_path ? (item.title ? item.subtitle_path : dirname(item.subtitle_path)) : "No configured source subtitle found",
        })),
      h("div", { class: "row-langs" },
        h("span", { class: "chip chip-source", text: item.source_code || "?" }),
        icon("arrow"),
        targets.map((target) => h("span", { class: "chip", text: target.code }))),
      h("div", { class: "row-action" }, action));
  }

  function render() {
    const list = el("list");
    if (state.loading && !state.items) {
      list.replaceChildren(loadingState(loadingText));
      return;
    }
    if (state.error) {
      list.replaceChildren(errorState(state.error, load));
      return;
    }
    const items = state.items || [];
    const query = el("filter").value.trim().toLowerCase();
    const visible = items.filter((item) => !query ||
      `${item.title || ""} ${item.subtitle_path || ""} ${item.video_path || ""}`.toLowerCase().includes(query));
    el("summary").textContent = summarize(items);
    list.replaceChildren(...(visible.length
      ? visible.map(row)
      : [query ? emptyState("No matching items", "Try a different filter.") : emptyState(emptyTitle, emptyText, "check")]));
  }

  async function load() {
    state.loading = true;
    state.error = null;
    render();
    try {
      const data = await get(endpoint);
      state.items = data.items || [];
    } catch (error) {
      state.error = error.message;
    } finally {
      state.loading = false;
      render();
    }
  }

  return {
    state,
    load,
    render,
    init() {
      el("filter").addEventListener("input", render);
      bindAction(`${key}-refresh`, load);
    },
    async enter() {
      if (!state.items && !state.loading) await load();
    },
  };
}

const wanted = createCandidateView({
  key: "wanted",
  endpoint: "/api/wanted",
  loadingText: "Asking Bazarr for wanted subtitles…",
  emptyTitle: "Nothing to translate",
  emptyText: "Bazarr has no wanted items with a configured source subtitle.",
  summarize: (items) => {
    const ready = items.filter((item) => item.can_enqueue !== false).length;
    return `${plural(items.length, "item")} wanted · ${ready} ready to queue`;
  },
});

const scan = createCandidateView({
  key: "scan",
  endpoint: "/api/scan",
  loadingText: "Scanning media roots…",
  emptyTitle: "Library is up to date",
  emptyText: "Every source subtitle already has its target languages.",
  summarize: (items) => `${plural(items.length, "source subtitle")} missing at least one target language`,
});

async function enqueueAllScanned() {
  const confirmed = await confirmDialog({
    title: "Queue every missing target?",
    message: "The media roots are scanned again and a translation job is queued for each missing target subtitle.",
    confirmLabel: "Queue all",
  });
  if (!confirmed) return;
  const data = await post("/api/enqueue-scan");
  (scan.state.items || []).forEach((item) => { item.queued = true; });
  scan.render();
  toast(`Queued ${plural(data.count, "job")}`, "success");
  refreshQueue().catch(() => {});
}

export const wantedView = {
  title: "Bazarr wanted",
  subtitle: "Missing subtitles in Bazarr that can be translated from a configured source language.",
  init: wanted.init,
  enter: wanted.enter,
};

export const scanView = {
  title: "Library scan",
  subtitle: "Local source subtitles whose configured target languages are missing.",
  init() {
    scan.init();
    bindAction("scan-enqueue-all", enqueueAllScanned);
  },
  enter: scan.enter,
};
