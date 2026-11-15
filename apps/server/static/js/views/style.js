// Style: how a series should sound, its characters, and a trial before it costs a whole episode (FR-18).
// `#style/<series id>/<language>`. "Check" counts the translations a save would flag stale without saving anything.
import { api, get, post } from "../api.js";
import { plural } from "../format.js";
import { actionButton, confirmDialog, crumbs, emptyState, h, runAction, setHeading, toast } from "../ui.js";
import { describeStyle, readStyle, styleFields, writeStyle } from "./style-form.js";

const $ = (id) => document.getElementById(id);

const state = { seriesId: null, lang: "vi", title: "", overview: null, saved: "", units: [], editing: null };

const currentDoc = () => ({ preset: $("style-preset").value, style: readStyle($("style-fields")) });
const snapshot = () => JSON.stringify(currentDoc());
const dirty = () => snapshot() !== state.saved;
const base = () => `/api/series/${state.seriesId}`;
const code = (unit) => `S${String(unit.season ?? 0).padStart(2, "0")}E${String(unit.episode ?? 0).padStart(2, "0")}`;

function showResult(kind, text) {
  const box = $("style-result");
  box.hidden = !text;
  box.className = `notice notice-${kind}`;
  box.textContent = text;
}

function impactText(data) {
  if (data.would_stale) return `${plural(data.would_stale, "translation")} will become stale; none is retranslated by itself.`;
  return "No translation becomes stale.";
}

// ---- The series' style ---------------------------------------------------------------

function renderEffective() {
  const chips = describeStyle(state.overview.effective);
  $("style-effective").replaceChildren(...(chips.length ? chips.map((text) => h("span", { class: "chip", text })) : [h("span", { class: "muted", text: "Nothing set" })]));
}

function renderPresets() {
  const select = $("style-preset");
  select.replaceChildren(h("option", { value: "", text: "No preset" }),
    ...state.overview.presets.map((p) => h("option", { value: p.id, text: p.name })));
  select.value = state.overview.preset || "";
}

function describePreset(id) {
  const preset = state.overview.presets.find((p) => p.id === id);
  if (!preset) return "";
  const parts = describeStyle(preset.style);
  return parts.length ? `${preset.name}: ${parts.join(" · ")}` : preset.name;
}

async function check() {
  const doc = currentDoc();
  const data = await api(`${base()}/style?lang=${encodeURIComponent(state.lang)}`, { method: "PUT", body: { ...doc, dry_run: true } });
  showResult("ok", `Valid. ${impactText(data)}`);
}

async function save() {
  const doc = currentDoc();
  const data = await api(`${base()}/style?lang=${encodeURIComponent(state.lang)}`, { method: "PUT", body: doc });
  state.saved = JSON.stringify(doc);
  $("style-save").disabled = true;
  showResult("ok", `Saved. ${impactText(data)}`);
  toast("Style saved", "success");
  state.overview = await get(`${base()}/style?lang=${encodeURIComponent(state.lang)}`);
  renderEffective();
}

async function suggest() {
  const data = await get(`${base()}/style/suggestions`);
  const box = $("style-suggestion");
  if (!data.presets.length) {
    box.textContent = data.genres.length
      ? `TMDB lists ${data.genres.join(", ")}; none of the presets fits.`
      : "TMDB gave no genre for this series (is the TMDB key set in Settings?).";
    return;
  }
  box.replaceChildren(`TMDB lists ${data.genres.join(", ")}. Suggested: `, ...data.presets.map((p) => h("button", {
    type: "button", class: "chip chip-link", onClick: () => { $("style-preset").value = p.id; changed(); },
  }, p.name)));
}

function changed() {
  $("style-save").disabled = !dirty();
  $("style-suggestion").textContent = describePreset($("style-preset").value);
}

// ---- Character cards -----------------------------------------------------------------------

function cardSummary(card) {
  return [card.speech, card.always ? "always in the prompt" : "", ...describeStyle(card.style || {})].filter(Boolean).join(" · ");
}

function renderCards() {
  const list = $("style-cards");
  const cards = state.overview.cards;
  if (!cards.length) {
    list.replaceChildren(emptyState("No character cards", "Add one for a character whose voice should stay recognisable across episodes.", "inbox"));
    return;
  }
  list.replaceChildren(...cards.map((card) => h("div", { class: "row card-row" },
    h("div", { class: "row-main" },
      h("strong", { text: card.name }),
      card.aliases?.length ? h("span", { class: "muted", text: ` (${card.aliases.join(", ")})` }) : null,
      h("div", { class: "muted", text: cardSummary(card) || "No details yet" })),
    h("div", { class: "row-action" },
      actionButton("Edit", () => openCard(card), { iconName: "edit" }),
      actionButton("Delete", () => removeCard(card), { iconName: "trash", variant: "btn-ghost" })))));
}

const lines = (text) => text.split("\n").map((line) => line.trim()).filter(Boolean);
const commas = (text) => text.split(",").map((part) => part.trim()).filter(Boolean);

function openCard(card = null) {
  state.editing = card ? card.name : null;
  $("card-title").textContent = card ? `Character · ${card.name}` : "New character";
  $("card-name").value = card?.name || "";
  $("card-aliases").value = (card?.aliases || []).join(", ");
  $("card-speech").value = card?.speech || "";
  $("card-address").value = (card?.address || []).map((a) => `${a.to} = ${a.form}`).join("\n");
  $("card-catchphrases").value = (card?.catchphrases || []).join("\n");
  $("card-samples").value = (card?.samples || []).join("\n");
  $("card-always").checked = Boolean(card?.always);
  writeStyle($("card-style"), card?.style || {});
  $("card-impact").textContent = "";
  $("card-dialog").showModal();
}

function readCard() {
  const address = lines($("card-address").value).map((line) => {
    const [to, ...rest] = line.split("=");
    return { to: to.trim(), form: rest.join("=").trim() };
  });
  const card = { name: $("card-name").value.trim() };
  const optional = {
    aliases: commas($("card-aliases").value), speech: $("card-speech").value.trim(), address,
    catchphrases: lines($("card-catchphrases").value), samples: lines($("card-samples").value),
    style: readStyle($("card-style")), always: $("card-always").checked,
  };
  for (const [key, value] of Object.entries(optional)) {
    if (Array.isArray(value) ? value.length : (typeof value === "object" ? Object.keys(value).length : value)) card[key] = value;
  }
  return card;
}

async function saveCard() {
  const data = await api(`${base()}/cards?lang=${encodeURIComponent(state.lang)}`, {
    method: "PUT", body: { card: readCard(), replaces: state.editing || undefined },
  });
  $("card-dialog").close();
  toast(`${data.card.name} saved. ${impactText(data)}`, "success");
  await reloadOverview();
}

async function removeCard(card) {
  const confirmed = await confirmDialog({
    title: `Delete ${card.name}?`, message: "Translations that used this card are flagged stale; nothing is retranslated.",
    confirmLabel: "Delete", danger: true,
  });
  if (!confirmed) return;
  const data = await post(`${base()}/cards/delete?lang=${encodeURIComponent(state.lang)}`, { name: card.name });
  toast(`Deleted. ${impactText(data)}`, "success");
  await reloadOverview();
}

async function reloadOverview() {
  state.overview = await get(`${base()}/style?lang=${encodeURIComponent(state.lang)}`);
  renderEffective();
  renderCards();
}

// ---- Trial and comparison ----------------------------------------------------------------------

function trialBody() {
  const doc = currentDoc();
  return { lang: state.lang, preset: doc.preset, style: doc.style };
}

function unitId() {
  const id = Number($("try-unit").value);
  if (!Number.isInteger(id) || id <= 0) throw new Error("Choose an episode first");
  return id;
}

async function tryStyle() {
  const body = { ...trialBody(), unit_id: unitId() };
  const cues = commas($("try-cues").value).map(Number);
  if (cues.length) {
    if (cues.some((n) => !Number.isInteger(n))) throw new Error("Cues are numbers, like 12, 40, 41");
    body.cues = cues;
  } else body.count = Number($("try-count").value) || 20;
  $("try-result").replaceChildren(h("div", { class: "empty empty-loading" }, h("span", { class: "spinner" }), h("p", { text: "Translating the sample lines…" })));
  let data;
  try {
    data = await post(`${base()}/style/preview`, body);
  } catch (error) {
    $("try-result").replaceChildren();
    throw error;
  }
  $("try-result").replaceChildren(
    h("p", { class: "muted try-meta", text: `${plural(data.rows.length, "line")} translated by ${data.model} (${data.tokens_in} tokens in, ${data.tokens_out} out). Nothing was written.` }),
    h("div", { class: "cue cue-head cue-try", "aria-hidden": "true" },
      h("div", { class: "cue-meta", text: "Cue" }), h("div", { class: "cue-src", text: "Source" }), h("div", { class: "cue-dst", text: "Now" }), h("div", { class: "cue-dst", text: "With this style" })),
    ...data.rows.map((row) => h("div", { class: "cue cue-try" },
      h("div", { class: "cue-meta" }, h("span", { class: "cue-n", text: `#${row.n}` })),
      h("div", { class: "cue-src", text: row.source }),
      h("div", { class: "cue-dst" }, row.current == null ? h("span", { class: "muted", text: "—" }) : row.current),
      h("div", { class: "cue-dst", text: row.preview }))));
}

async function queueVariant() {
  const label = $("variant-label").value.trim();
  const id = unitId();
  const data = await post(`/api/units/${id}/variant`, { ...trialBody(), label: label || "variant" });
  toast(`Queued “${data.label}”. When it finishes it appears under History of that episode, to compare.`, "success");
}

// ---- Loading ---------------------------------------------------------------------------------------

async function load() {
  const [overview, info] = await Promise.all([
    get(`${base()}/style?lang=${encodeURIComponent(state.lang)}`),
    get(`/api/library/series/${state.seriesId}/units`),
  ]);
  state.overview = overview;
  state.title = info.series.title || info.series.key;
  state.units = info.units;
  setHeading(`${state.title} style`, "How the translation should sound.");
  crumbs($("style-crumbs"), [["Library", "#library"], [state.title, `#library/${state.seriesId}`], [`Style · ${state.lang}`]]);
  $("style-glossary").href = `#glossary/${state.seriesId}/${state.lang}`;
  renderPresets();
  writeStyle($("style-fields"), overview.style);
  state.saved = snapshot();
  $("style-save").disabled = true;
  $("style-suggestion").textContent = describePreset(overview.preset);
  showResult("ok", "");
  renderEffective();
  renderCards();
  const withTarget = info.units.filter((u) => u.targets.some((t) => t.lang === state.lang));
  const choices = withTarget.length ? withTarget : info.units;
  $("try-unit").replaceChildren(...choices.map((u) => h("option", { value: u.id, text: `${code(u)}${u.title ? ` · ${u.title}` : ""}` })));
  $("try-result").replaceChildren();
}

export default {
  title: "Style",
  subtitle: "How the translation should sound.",
  nav: "library",

  init() {
    $("style-fields").replaceChildren(...styleFields("style"));
    $("card-style").replaceChildren(...styleFields("card"));
    $("style-fields").addEventListener("input", changed);
    $("style-fields").addEventListener("change", changed);
    $("style-preset").addEventListener("change", changed);
    $("style-suggest").addEventListener("click", (event) => runAction(event.currentTarget, suggest));
    $("style-check").addEventListener("click", (event) => runAction(event.currentTarget, check));
    $("style-save").addEventListener("click", (event) => runAction(event.currentTarget, save));
    $("card-add").addEventListener("click", () => openCard());
    $("card-save").addEventListener("click", (event) => runAction(event.currentTarget, saveCard));
    $("try-run").addEventListener("click", (event) => runAction(event.currentTarget, tryStyle));
    $("variant-run").addEventListener("click", (event) => runAction(event.currentTarget, queueVariant));
    $("card-dialog").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.tagName === "INPUT") event.preventDefault();
    });
    window.addEventListener("beforeunload", (event) => {
      if (state.seriesId && !$("view-style").hidden && dirty()) event.preventDefault();
    });
  },

  async enter(param) {
    const [idText, lang] = (param || "").split("/");
    const id = Number(idText);
    if (!Number.isInteger(id) || id <= 0) {
      window.location.hash = "#library";
      return;
    }
    Object.assign(state, { seriesId: id, lang: lang || state.lang || "vi" });
    await load();
  },
};
