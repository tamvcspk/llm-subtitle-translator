// Episode (or movie): source and translation side by side, correct a cue or add a rule, retranslate,
// and browse, compare or restore earlier versions. `#episode/<unit id>/<language>`.
import { api, get, post } from "../api.js";
import { formatDateTime, plural } from "../format.js";
import { refreshQueue } from "../store.js";
import {
  actionButton, confirmDialog, crumbs, emptyState, errorState, h, icon, loadingState, runAction, setHeading,
  statusPill, toast,
} from "../ui.js";

const $ = (id) => document.getElementById(id);
const TRIGGERS = {
  auto: "Translated", retranslate: "Retranslated", cue_edit: "Cue edit", revert: "Restored", external: "Edited outside the worker",
};
const WATCH_MS = 30 * 60 * 1000;

const state = { unitId: null, lang: null, detail: null, cues: null, corrections: [], versions: null, context: null, query: "", watch: null };
let loadToken = 0;
let searchTimer = 0;

const episodeCode = (unit) => `S${String(unit.season ?? 0).padStart(2, "0")}E${String(unit.episode ?? 0).padStart(2, "0")}`;
const base = () => `/api/units/${state.unitId}`;
const langQuery = () => `lang=${encodeURIComponent(state.lang)}`;
const currentTarget = () => state.detail?.targets.find((target) => target.lang === state.lang);

// ---- Header ---------------------------------------------------------------

function renderHeader() {
  const unit = state.detail;
  const isEpisode = unit.series_id != null;
  const title = isEpisode ? `${episodeCode(unit)}${unit.title ? ` · ${unit.title}` : ""}` : (unit.title || "Movie");
  const seriesTitle = unit.series?.title;
  setHeading(isEpisode ? `${seriesTitle || "Series"} ${episodeCode(unit)}` : title, "Compare, correct and retranslate.");
  crumbs($("episode-crumbs"), [
    ["Library", "#library"],
    ...(isEpisode ? [[seriesTitle || "Series", `#library/${unit.series.id}`]] : []),
    [title],
  ]);
  $("episode-title").textContent = title;
  const target = currentTarget();
  $("episode-meta").textContent = `${unit.source_code || "?"} → ${state.lang}` +
    (target?.version ? ` · version ${target.version}` : "") + (target?.model ? ` · ${target.model}` : "");
  $("episode-status").replaceChildren(target ? statusPill(target.status) : "");
  const glossary = $("episode-glossary");
  glossary.hidden = !isEpisode;
  if (isEpisode) glossary.href = `#glossary/${unit.series.id}/${state.lang}`;
  $("episode-retranslate").disabled = !unit.source_exists;
  $("episode-retranslate").title = unit.source_exists ? "" : "The source subtitle is no longer on disk";

  $("episode-langs").replaceChildren(...unit.targets.map((item) =>
    h("a", { class: "chip chip-link", href: `#episode/${unit.id}/${item.lang}`, ...(item.lang === state.lang ? { "aria-current": "page" } : {}) },
      item.lang, h("span", { class: `dot dot-${item.status}`, title: item.status }))));

  const banner = $("episode-warning");
  const notes = [];
  if (target?.modified_outside) {
    notes.push("This subtitle was changed outside the worker (a hand edit or another tool). It is kept in History before any retranslation or restore.");
  }
  if (target && !target.exists) notes.push("There is no translation file on disk yet.");
  if (state.watch) notes.push("A retranslation is queued. This page updates when it finishes.");
  banner.hidden = !notes.length;
  banner.querySelector("p").textContent = notes.join(" ");
}

// ---- Cues -------------------------------------------------------------------

function cueRow(row, focus) {
  const classes = ["cue", row.n === focus ? "is-focus" : "", row.locked ? "is-locked" : ""].filter(Boolean).join(" ");
  return h("div", { class: classes, id: `cue-${row.n}` },
    h("div", { class: "cue-meta" }, h("span", { class: "cue-n", text: `#${row.n}` }), h("span", { class: "cue-time mono", text: row.start })),
    h("div", { class: "cue-src", text: row.source }),
    h("div", { class: "cue-dst" }, row.locked && icon("lock"), row.target == null ? h("span", { class: "muted", text: "—" }) : row.target),
    h("button", { type: "button", class: "btn btn-ghost btn-sm cue-edit", "aria-label": `Correct cue ${row.n}`, onClick: () => openCueDialog(row) },
      icon("edit")));
}

function renderCues() {
  const data = state.cues;
  const list = $("episode-cue-list");
  const parts = [];
  if (state.query) parts.push(`${plural(data.total, "match")} for “${state.query}”`);
  else parts.push(plural(data.total, "cue"));
  if (!data.has_target) parts.push("no translation yet");
  else if (!data.aligned) parts.push("the translation has a different number of cues than the source");
  $("episode-cue-status").textContent = parts.join(" · ");
  if (!data.rows.length) {
    list.replaceChildren(emptyState("No cues", state.query ? "Nothing matches that search." : "The source subtitle has no cues."));
    return;
  }
  list.replaceChildren(...data.rows.map((row) => cueRow(row, data.focus)));
  if (data.focus != null) $(`cue-${data.focus}`)?.scrollIntoView({ block: "center" });
}

async function loadCues(at = "") {
  const params = new URLSearchParams({ lang: state.lang });
  if (state.query) params.set("q", state.query);
  else if (at) params.set("at", at);
  state.cues = await get(`${base()}/cues?${params}`);
  renderCues();
}

async function jump() {
  const at = $("episode-jump").value.trim();
  if (!at) return;
  $("episode-search").value = "";
  state.query = "";
  await loadCues(at);
  if (state.cues.focus == null) toast("No cue found", "error");
}

// ---- Corrections list -----------------------------------------------------------

function describeCorrection(item) {
  const p = item.payload;
  if (item.kind === "term") return `Term: ${p.source} → ${p.target}${p.locked === false ? "" : " (locked)"}`;
  if (item.kind === "address") {
    const forms = [p.a_to_b && `${p.a} to ${p.b}: ${p.a_to_b}`, p.b_to_a && `${p.b} to ${p.a}: ${p.b_to_a}`].filter(Boolean).join(" · ");
    return `Address: ${forms}${p.from_cue ? ` (from cue ${p.from_cue})` : ""}`;
  }
  if (item.kind === "note") return `Note: ${p.text}`;
  return `Cue ${p.cue} (locked): ${p.text}`;
}

function renderCorrections() {
  $("episode-correction-summary").textContent = `Corrections for this episode (${state.corrections.length})`;
  const list = $("episode-correction-list");
  if (!state.corrections.length) {
    list.replaceChildren(h("p", { class: "muted", text: "None. Use the pencil on a cue to add one. Rules for more than this episode go to the series glossary." }));
    return;
  }
  list.replaceChildren(...state.corrections.map((item) => h("div", { class: "correction" },
    h("span", { class: "tag", text: item.kind.replace("_", " ") }),
    h("span", { class: "correction-text", text: describeCorrection(item) }),
    actionButton("Remove", async () => {
      await post(`${base()}/corrections/${item.id}/remove`);
      await refreshHeaderAndCorrections();
    }, { variant: "btn-ghost" }))));
}

async function loadCorrections() {
  state.corrections = (await get(`${base()}/corrections?${langQuery()}`)).items;
  renderCorrections();
}

async function refreshHeaderAndCorrections() {
  state.detail = await get(base());
  renderHeader();
  await loadCorrections();
  if (state.cues) await loadCues();
}

// ---- History ------------------------------------------------------------------------

function diffLine(kind, cue) {
  return h("div", { class: `diff-line diff-${kind}` },
    h("span", { class: "cue-n", text: `#${cue.n}` }), h("span", { class: "mono", text: cue.time }), h("span", { class: "diff-text", text: cue.text }));
}

async function toggleDiff(version, holder, button) {
  if (holder.dataset.open) {
    holder.replaceChildren();
    delete holder.dataset.open;
    return;
  }
  const data = await get(`${base()}/versions/${version}/diff?${langQuery()}`);
  holder.dataset.open = "1";
  holder.replaceChildren(
    h("p", { class: "muted", text: `Version ${data.version} compared with version ${data.against}: ${plural(data.changed_cues, "cue")} changed.` }),
    ...data.changes.map((change) => h("div", { class: "diff-change" },
      change.before.map((cue) => diffLine("before", cue)), change.after.map((cue) => diffLine("after", cue)))));
  button.blur();
}

async function revert(version) {
  const confirmed = await confirmDialog({
    title: `Restore version ${version}?`,
    message: "The file is replaced with that version, byte for byte. What is there now stays in History, so this can be undone.",
    confirmLabel: "Restore",
  });
  if (!confirmed) return;
  await post(`${base()}/versions/${version}/revert`, { lang: state.lang });
  toast(`Version ${version} restored`, "success");
  await loadAll();
}

function renderVersions() {
  const { versions, modified_outside: edited } = state.versions;
  $("episode-version-count").textContent = versions.length;
  const list = $("episode-version-list");
  if (!versions.length) {
    list.replaceChildren(emptyState("No history yet", "A version is recorded each time the worker writes this subtitle.", "clock"));
    return;
  }
  list.replaceChildren(
    ...(edited ? [h("p", { class: "version-note", text: "The file on disk differs from the latest version below (edited outside the worker)." })] : []),
    ...versions.map((item) => {
      const holder = h("div", { class: "diff" });
      const hasEarlier = versions.some((other) => other.version < item.version);
      return h("div", { class: "version" },
        h("div", { class: "version-main" },
          h("strong", { text: `v${item.version}` }),
          h("span", { class: "tag", text: TRIGGERS[item.trigger] || item.trigger }),
          item.current && h("span", { class: "status status-ok" }, h("span", { class: "dot" }), "On disk"),
          h("span", { class: "muted", text: [formatDateTime(item.created_at), item.model, item.tokens_out ? `${item.tokens_out} tokens out` : ""].filter(Boolean).join(" · ") }),
          h("span", { class: "muted mono", title: item.sha256, text: item.sha256.slice(0, 10) })),
        h("div", { class: "version-actions" },
          hasEarlier && actionButton("Changes", (button) => toggleDiff(item.version, holder, button)),
          !item.current && actionButton("Restore", () => revert(item.version), { iconName: "retry" })),
        holder);
    }));
}

async function loadVersions() {
  state.versions = await get(`${base()}/versions?${langQuery()}`);
  renderVersions();
}

// ---- Context (story memory) ------------------------------------------------------------------

function contextSections(sections) {
  return sections.map((section) => h("details", { class: "context-section", open: true },
    h("summary", { text: section.title }), h("pre", { class: "context-text", text: section.body })));
}

function renderContext() {
  const data = state.context;
  const body = $("episode-context-body");
  const hasSeries = state.detail.series_id != null;
  $("episode-context-tab").hidden = !hasSeries || !state.detail.memory_applicable;
  if (!data || !data.applicable) {
    body.replaceChildren(emptyState("No story memory", "Story memory applies to episodes of a series only.", "clock"));
    return;
  }
  const parts = [];
  parts.push(h("p", { class: "context-state" },
    h("span", { class: `status status-${data.enabled ? "ok" : "muted"}` }, h("span", { class: "dot" }), data.enabled ? "On" : "Off"),
    ` Story memory is ${data.enabled ? "on" : "off"} for this series.`,
    data.special ? " This is a special (season 0): it sees the story up to the point set below, and its own facts never feed the main story." : ""));

  const used = data.used;
  const flag = $("episode-context-flag");
  const alert = Boolean(used && (used.changed || !used.complete));
  flag.hidden = !alert;
  flag.textContent = "!";
  if (used) {
    if (used.changed) {
      parts.push(h("div", { class: "banner banner-warn banner-flat" }, h("span", { class: "banner-icon" }, icon("alert")),
        h("p", { text: "The story behind earlier episodes has changed since this was translated, so the translation is flagged stale. Retranslate it when you want the new context used." })));
    }
    if (!used.complete) {
      parts.push(h("div", { class: "banner banner-warn banner-flat" }, h("span", { class: "banner-icon" }, icon("alert")),
        h("p", { text: `Translated without ${used.missing.join(", ")}: no subtitle (or no readable facts) was available for ${used.missing.length === 1 ? "that episode" : "those episodes"} at the time.` })));
    }
    parts.push(h("h3", { class: "context-heading", text: `Used for version ${used.version}` }));
    parts.push(used.sections?.length ? contextSections(used.sections)
      : h("p", { class: "muted", text: "The exact text was not kept for this version." }));
  } else {
    parts.push(h("p", { class: "muted", text: "This translation was made without story memory." }));
  }

  const now = data.now;
  parts.push(h("h3", { class: "context-heading", text: "What a new translation would get now" }));
  parts.push(h("div", { class: "chip-list" }, ...now.prior.map((item) => h("span", {
    class: `chip ${item.has_facts ? "chip-current" : "chip-incomplete"}`,
    title: item.has_facts ? "Facts read" : (item.has_source ? "Has a subtitle; facts not read yet" : "No subtitle"),
  }, item.label, h("span", { class: `dot dot-${item.has_facts ? "current" : "incomplete"}` })))));
  if (!now.prior.length) parts.push(h("p", { class: "muted", text: "No earlier episodes are known, so there is no history to give." }));
  if (now.missing.length) {
    parts.push(h("p", { class: "muted", text: `No facts yet for ${now.missing.join(", ")}. Use “Read facts now”, or they are read when a translation needs them.` }));
  }
  if (now.sections.length) {
    if (now.approximate) parts.push(h("p", { class: "muted", text: "Older episodes are shown shortened; a translation condenses them with the model." }));
    parts.push(contextSections(now.sections));
  }
  body.replaceChildren(...parts.flat()); // contextSections() returns a list of elements

  $("context-policy").value = data.policy || "";
  $("context-anchor").value = data.specials_after || "";
}

async function loadContext() {
  if (!state.detail?.memory_applicable) {
    state.context = null;
    renderContext();
    return;
  }
  state.context = await get(`${base()}/context?${langQuery()}`);
  renderContext();
}

async function saveMemorySettings() {
  const seriesId = state.detail.series.id;
  await api(`/api/series/${seriesId}/memory`, {
    method: "PUT", body: { policy: $("context-policy").value, specials_after: $("context-anchor").value.trim() },
  });
  toast("Saved. Translations are only flagged stale if their story changed; none are retranslated.", "success");
  await loadAll();
}

async function extractFacts() {
  const result = await post(`${base()}/context/extract`);
  toast(result.extracted.length ? `Read the facts of ${result.extracted.join(", ")}.` : "Every episode already has its facts.", "success");
  await loadAll();
}

// ---- Cue dialog (edit + add a correction) -----------------------------------------------

let dialogRow = null;

function syncCorrectionForm() {
  const kind = $("corr-kind").value;
  document.querySelectorAll("[data-corr-kind]").forEach((el) => { el.hidden = el.dataset.corrKind !== kind; });
  const scope = $("corr-scope");
  const movie = state.detail.series_id == null;
  for (const option of scope.options) option.hidden = (kind === "note" && option.value !== "episode") || (movie && option.value === "forward");
  if (scope.selectedOptions[0]?.hidden) scope.value = "episode";
}

function openCueDialog(row) {
  dialogRow = row;
  $("cue-title").textContent = `Cue ${row.n} · ${row.start}`;
  $("cue-source").textContent = row.source;
  $("cue-text").value = row.target ?? "";
  $("cue-text").disabled = !state.cues.has_target || !state.cues.aligned;
  $("cue-save").disabled = $("cue-text").disabled;
  $("cue-edit-note").hidden = !$("cue-text").disabled;
  for (const id of ["corr-term-source", "corr-term-target", "corr-a", "corr-b", "corr-a-to-b", "corr-b-to-a", "corr-note"]) $(id).value = "";
  $("corr-from-cue").checked = false;
  $("corr-kind").value = "term";
  syncCorrectionForm();
  $("cue-dialog").showModal();
}

async function saveCueEdit() {
  const result = await post(`${base()}/corrections`, {
    lang: state.lang, kind: "cue_edit", payload: { cue: dialogRow.n, text: $("cue-text").value },
  });
  $("cue-dialog").close();
  toast(result.warnings?.length ? `Saved. ${result.warnings[0]}` : `Cue ${dialogRow.n} saved and locked`, result.warnings?.length ? "info" : "success");
  await loadAll();
}

function correctionBody() {
  const kind = $("corr-kind").value;
  const value = (id) => $(id).value.trim();
  let payload;
  if (kind === "term") payload = { source: value("corr-term-source"), target: value("corr-term-target"), locked: $("corr-term-locked").checked };
  else if (kind === "address") {
    payload = { a: value("corr-a"), b: value("corr-b"), a_to_b: value("corr-a-to-b"), b_to_a: value("corr-b-to-a") };
    if ($("corr-from-cue").checked) payload.from_cue = dialogRow.n;
  } else payload = { text: value("corr-note") };
  return { lang: state.lang, kind, scope: $("corr-scope").value, payload };
}

async function addCorrection() {
  const body = correctionBody();
  const result = await post(`${base()}/corrections`, body);
  $("cue-dialog").close();
  if (body.scope === "episode") toast("Correction saved. It applies the next time you retranslate.", "success");
  else {
    const stale = result.impact?.would_stale;
    toast(`Saved to the glossary${stale ? `; ${plural(stale, "translation")} will become stale` : ""}.`, "success");
  }
  await refreshHeaderAndCorrections();
}

// ---- Retranslate dialog ----------------------------------------------------------------------

function syncRetranslateForm() {
  const scope = document.querySelector('input[name="rt-scope"]:checked').value;
  $("rt-cues").hidden = scope !== "cues";
  $("rt-time").hidden = scope !== "time";
}

function openRetranslate() {
  document.querySelector('input[name="rt-scope"][value="all"]').checked = true;
  for (const id of ["rt-first", "rt-last", "rt-start", "rt-end", "rt-term-source", "rt-term-target", "rt-note"]) $(id).value = "";
  const rules = state.corrections.filter((item) => item.kind !== "cue_edit");
  const locked = state.corrections.filter((item) => item.kind === "cue_edit").length;
  $("rt-corrections").replaceChildren(...rules.map((item) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: true, dataset: { id: item.id } }), h("span", { text: describeCorrection(item) }))));
  $("rt-corrections-wrap").hidden = !rules.length;
  $("rt-locked").hidden = !locked;
  $("rt-locked").textContent = `${plural(locked, "hand-edited cue")} will be kept exactly as you wrote ${locked === 1 ? "it" : "them"}.`;
  syncRetranslateForm();
  $("retranslate-dialog").showModal();
}

async function submitRetranslate() {
  const scope = document.querySelector('input[name="rt-scope"]:checked').value;
  const body = { lang: state.lang };
  if (scope === "cues") body.range = { cues: [Number($("rt-first").value), Number($("rt-last").value)] };
  if (scope === "time") body.range = { time: [$("rt-start").value.trim(), $("rt-end").value.trim()] };
  const boxes = [...$("rt-corrections").querySelectorAll("input[type=checkbox]")];
  if (boxes.length) body.correction_ids = boxes.filter((box) => box.checked).map((box) => Number(box.dataset.id));
  const fresh = [];
  const source = $("rt-term-source").value.trim();
  const target = $("rt-term-target").value.trim();
  if (source || target) fresh.push({ kind: "term", scope: "episode", payload: { source, target, locked: true } });
  if ($("rt-note").value.trim()) fresh.push({ kind: "note", scope: "episode", payload: { text: $("rt-note").value.trim() } });
  if (fresh.length) body.new_corrections = fresh;

  await post(`${base()}/retranslate`, body);
  $("retranslate-dialog").close();
  toast("Retranslation queued. It runs ahead of automatic jobs.", "success");
  refreshQueue().catch(() => {});
  state.watch = { versions: state.versions?.versions.length ?? 0, until: Date.now() + WATCH_MS };
  await refreshHeaderAndCorrections();
}

// ---- Loading ----------------------------------------------------------------------------------

function showTab(tab) {
  document.querySelectorAll("#episode-tabs .tab").forEach((el) => {
    if (el.dataset.tab === tab) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("episode-cues-pane").hidden = tab !== "cues";
  $("episode-history-pane").hidden = tab !== "history";
  $("episode-context-pane").hidden = tab !== "context";
}

async function loadAll() {
  const mine = ++loadToken;
  try {
    const detail = await get(base());
    if (mine !== loadToken) return;
    state.detail = detail;
    if (!detail.targets.some((target) => target.lang === state.lang)) state.lang = detail.targets[0]?.lang ?? null;
    if (!state.lang) {
      setHeading("Episode", "");
      $("episode-cue-list").replaceChildren(emptyState("No translation target", "This item has no target language recorded yet."));
      return;
    }
    renderHeader();
    await Promise.all([loadCues(), loadCorrections(), loadVersions(), loadContext()]);
  } catch (error) {
    if (mine === loadToken) $("episode-cue-list").replaceChildren(errorState(error.message, loadAll));
  }
}

export default {
  title: "Episode",
  subtitle: "Compare, correct and retranslate.",
  nav: "library",

  init() {
    document.querySelectorAll("#episode-tabs .tab").forEach((el) => el.addEventListener("click", () => showTab(el.dataset.tab)));
    $("episode-jump").addEventListener("keydown", (event) => { if (event.key === "Enter") runAction($("episode-jump-go"), jump); });
    $("episode-jump-go").addEventListener("click", (event) => runAction(event.currentTarget, jump));
    $("episode-search").addEventListener("input", (event) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.query = event.target.value.trim();
        loadCues().catch((error) => toast(error.message, "error"));
      }, 250);
    });
    $("episode-retranslate").addEventListener("click", openRetranslate);
    $("context-save").addEventListener("click", (event) => runAction(event.currentTarget, saveMemorySettings));
    $("context-extract").addEventListener("click", (event) => runAction(event.currentTarget, extractFacts));
    $("corr-kind").addEventListener("change", syncCorrectionForm);
    $("cue-save").addEventListener("click", (event) => runAction(event.currentTarget, saveCueEdit));
    $("cue-add").addEventListener("click", (event) => runAction(event.currentTarget, addCorrection));
    document.querySelectorAll('input[name="rt-scope"]').forEach((el) => el.addEventListener("change", syncRetranslateForm));
    $("rt-submit").addEventListener("click", (event) => runAction(event.currentTarget, submitRetranslate));
    // Enter in a text box must not submit the dialog form (which would just close it).
    for (const id of ["cue-dialog", "retranslate-dialog"]) {
      $(id).addEventListener("keydown", (event) => {
        if (event.key === "Enter" && event.target.tagName === "INPUT") event.preventDefault();
      });
    }
  },

  async enter(param) {
    const [idText, lang] = (param || "").split("/");
    const id = Number(idText);
    if (!Number.isInteger(id) || id <= 0) {
      window.location.hash = "#library";
      return;
    }
    if (id !== state.unitId) state.watch = null;
    Object.assign(state, { unitId: id, lang: lang || null, cues: null, versions: null, context: null, query: "" });
    $("episode-search").value = "";
    $("episode-jump").value = "";
    $("episode-cue-list").replaceChildren(loadingState("Loading subtitles…"));
    showTab("cues");
    await loadAll();
  },

  /** While a retranslation is queued, notice when its new version appears. */
  async tick() {
    if (!state.watch || !state.lang) return;
    if (Date.now() > state.watch.until) {
      state.watch = null;
      return;
    }
    try {
      const latest = await get(`${base()}/versions?${langQuery()}`);
      if (latest.versions.length > state.watch.versions) {
        state.watch = null;
        toast("Retranslation finished", "success");
        await loadAll();
      }
    } catch {
      // The next tick tries again.
    }
  },
};
