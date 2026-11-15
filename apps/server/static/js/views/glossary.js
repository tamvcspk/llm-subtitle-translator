// Glossary: the rules of one series and target language (FR-4, FR-10.2, FR-18.10). `#glossary/<series id>/<language>`.
// It lives in the worker's database; the form edits it, the YAML tab is the exchange format. "Check" validates and
// counts how many translations a save would make stale, without writing anything.
import { api, get, post } from "../api.js";
import { formatDateTime, plural } from "../format.js";
import { actionButton, confirmDialog, crumbs, h, runAction, setHeading, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);

const EXAMPLE = `# Everything is optional. Rules here always beat what the model infers.
terms:
  - source: Kyber crystal
    target: tinh thể Kyber
    locked: true          # the target must appear whenever the source does
    from: S01E03          # optional: only from this episode on
characters:
  - name: Alice
    gender: f
address_rules:
  - a: Alice
    b: Bob
    a_to_b: em–anh        # how Alice addresses Bob
    b_to_a: anh–em
    from: S01E06 00:32:10 # optional: episode and time the rule starts
`;

// What each table of the form holds. `kind` is how the cell is read back: text, list (comma separated) or flag.
const TABLES = [
  {
    key: "terms", title: "Terms", help: "A locked term's translation must appear wherever the source uses the term.",
    columns: [
      ["source", "Source", "text"], ["target", "Translate as", "text"], ["aliases", "Also written", "list"],
      ["locked", "Locked", "flag"], ["from", "From", "text", "S01E03"], ["until", "Until", "text", "S02E01"], ["note", "Note", "text"],
    ],
  },
  {
    key: "characters", title: "Characters", help: "Names and genders. How a character talks is on the Style page.",
    columns: [["name", "Name", "text"], ["target", "Translate as", "text"], ["gender", "Gender", "text", "f / m"], ["description", "Description", "text"]],
  },
  {
    key: "address_rules", title: "Forms of address", help: "Written “self–other” from the speaker's side, like em–anh. A rule is never trimmed by the words in a scene.",
    columns: [
      ["a", "Speaker A", "text"], ["b", "Speaker B", "text"], ["a_to_b", "A says to B", "text", "em–anh"], ["b_to_a", "B says to A", "text", "anh–em"],
      ["from", "From", "text", "S01E06 00:32:10"], ["until", "Until", "text"], ["note", "Note", "text"],
    ],
  },
];

const state = { seriesId: null, lang: "vi", title: "", saved: "", languages: [], mode: "form", doc: {}, yaml: "", others: [] };

const readCell = (cell, kind) => {
  if (kind === "flag") return cell.checked;
  const text = cell.value.trim();
  return kind === "list" ? text.split(",").map((part) => part.trim()).filter(Boolean) : text;
};

function cellFor(column, value) {
  const [name, label, kind, placeholder] = column;
  if (kind === "flag") return h("input", { type: "checkbox", checked: Boolean(value), "aria-label": label, dataset: { name, kind } });
  const shown = kind === "list" ? (value || []).join(", ") : (value ?? "");
  return h("input", {
    type: "text", class: "input", value: shown, "aria-label": label, placeholder: placeholder || "",
    spellcheck: "false", autocomplete: "off", dataset: { name, kind },
  });
}

function rowFor(table, entry = {}) {
  const row = h("div", { class: `glossary-row glossary-row-${table.key}` },
    ...table.columns.map((column) => h("div", { class: `glossary-cell glossary-cell-${column[2]}` }, cellFor(column, entry[column[0]]))),
    h("button", { type: "button", class: "btn btn-ghost btn-sm", "aria-label": "Remove this row", onClick: () => { row.remove(); onEdit(); } }, "×"));
  return row;
}

function renderForm() {
  const form = $("glossary-form");
  const sections = TABLES.map((table) => {
    const rows = h("div", { class: "glossary-rows", dataset: { table: table.key } },
      ...(state.doc[table.key] || []).map((entry) => rowFor(table, entry)));
    const head = h("div", { class: `glossary-row glossary-row-${table.key} glossary-head`, "aria-hidden": "true" },
      ...table.columns.map((column) => h("div", { class: "glossary-cell", text: column[1] })), h("div"));
    return h("section", { class: "glossary-section" },
      h("div", { class: "glossary-title" }, h("h3", { text: table.title }),
        h("button", { type: "button", class: "btn btn-sm", onClick: () => { rows.append(rowFor(table)); onEdit(); } }, "+ Add")),
      h("p", { class: "hint", text: table.help }), head, rows);
  });
  const legacy = h("section", { class: "glossary-section" },
    h("div", { class: "glossary-title" }, h("h3", { text: "Old style note" })),
    h("p", { class: "hint", text: "Free text from before the Style page existed. It is still sent to the model; move what matters to the Style page." }),
    h("textarea", { id: "glossary-legacy-style", class: "input", rows: 2, spellcheck: "false", "aria-label": "Old style note" }));
  form.replaceChildren(...sections, legacy);
  $("glossary-legacy-style").value = state.doc.style || "";
}

function readForm() {
  const doc = { version: 1, target_lang: state.doc.target_lang || state.lang };
  for (const table of TABLES) {
    const entries = [];
    for (const row of $("glossary-form").querySelectorAll(`[data-table="${table.key}"] .glossary-row`)) {
      const entry = {};
      for (const column of table.columns) {
        const cell = row.querySelector(`[data-name="${column[0]}"]`);
        const value = readCell(cell, column[2]);
        if (column[2] === "flag" ? value : (Array.isArray(value) ? value.length : value)) entry[column[0]] = value;
      }
      // A row with nothing typed in it is not an entry; a locked box alone does not make one.
      if (Object.keys(entry).filter((key) => key !== "locked").length) entries.push(entry);
    }
    if (entries.length) doc[table.key] = entries;
  }
  const legacy = $("glossary-legacy-style").value.trim();
  if (legacy) doc.style = legacy;
  return doc;
}

const current = () => (state.mode === "form" ? JSON.stringify(readForm()) : $("glossary-yaml").value);
const dirty = () => current() !== state.saved;
const onEdit = () => { $("glossary-save").disabled = !dirty(); };

function showResult(kind, text) {
  const box = $("glossary-result");
  box.hidden = !text;
  box.className = `notice notice-${kind}`;
  box.textContent = text;
}

function impactText(data) {
  const parts = [];
  if (data.would_stale) parts.push(`${plural(data.would_stale, "translation")} will become stale`);
  if (data.stale_after) parts.push(`${plural(data.stale_after, "translation")} stale in total afterwards`);
  return parts.length ? parts.join("; ") : "No translation becomes stale.";
}

function glossaryApi(method, body) {
  const url = `/api/series/${state.seriesId}/glossary?lang=${encodeURIComponent(state.lang)}`;
  return method === "PUT" ? api(url, { method, body }) : get(url);
}

async function submit(dryRun) {
  const body = state.mode === "form" ? { doc: readForm(), dry_run: dryRun } : { yaml: $("glossary-yaml").value, dry_run: dryRun };
  const data = await glossaryApi("PUT", body);
  if (dryRun) {
    showResult("ok", `Valid. ${impactText(data)}`);
    return;
  }
  toast("Glossary saved", "success");
  await load(`Saved. ${impactText(data)} Stale translations are never retranslated automatically; open an episode to retranslate it.`);
}

function renderLanguages() {
  const languages = state.languages.includes(state.lang) ? state.languages : [...state.languages, state.lang];
  $("glossary-langs").replaceChildren(...languages.map((lang) =>
    h("a", { class: "chip chip-link", href: `#glossary/${state.seriesId}/${lang}`, ...(lang === state.lang ? { "aria-current": "page" } : {}) }, lang)));
}

function showMode(mode) {
  state.mode = mode;
  document.querySelectorAll("#glossary-tabs .tab").forEach((el) => {
    if (el.dataset.tab === mode) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("glossary-form-pane").hidden = mode !== "form";
  $("glossary-yaml-pane").hidden = mode !== "yaml";
  state.saved = mode === "form" ? JSON.stringify(readForm()) : $("glossary-yaml").value;
  $("glossary-save").disabled = true;
}

async function switchMode(mode) {
  if (mode === state.mode) return;
  if (dirty()) {
    const discard = await confirmDialog({
      title: "Discard your changes?", message: "Save or check them first, or they are lost when you switch.",
      confirmLabel: "Discard", danger: true,
    });
    if (!discard) return;
    await load();
  }
  showMode(mode);
}

// ---- History and copying ---------------------------------------------------------------------------

async function renderMore() {
  const body = $("glossary-more-body");
  const [history, series] = await Promise.all([get(`/api/series/${state.seriesId}/glossary/history?lang=${encodeURIComponent(state.lang)}`), get("/api/library/series")]);
  state.others = series.items.filter((item) => item.id !== state.seriesId);
  const older = history.items.length
    ? history.items.map((item) => h("div", { class: "row" },
      h("div", { class: "row-main" }, h("strong", { text: `Version ${item.version}` }),
        h("span", { class: "muted", text: ` ${formatDateTime(item.at)} · replaced by ${item.note || "a save"} · ${plural(item.terms, "term")}, ${plural(item.characters, "character")}, ${plural(item.address_rules, "address rule")}` })),
      actionButton("Restore", () => restore(item.version), { iconName: "retry" })))
    : [h("p", { class: "muted", text: "Nothing earlier yet: every save keeps what it replaced here." })];
  const picker = h("select", { id: "glossary-copy-from", class: "input input-sm", "aria-label": "Copy the glossary of" },
    h("option", { value: "", text: "Copy from another series…" }), ...state.others.map((item) => h("option", { value: item.id, text: item.title || item.key })));
  const merge = h("input", { type: "checkbox", id: "glossary-copy-merge", checked: true });
  body.replaceChildren(
    h("h4", { text: "Earlier versions" }), ...older,
    h("h4", { text: "Copy from another series" }),
    h("p", { class: "hint", text: "A copy, not a link: later changes to the other series do not reach this one." }),
    h("div", { class: "series-tools" }, picker,
      h("label", { class: "check check-inline" }, merge, h("span", { text: "Keep what is here (this series wins on conflicts)" })),
      actionButton("Copy", () => copyFrom(Number(picker.value), merge.checked))));
}

async function restore(version) {
  const confirmed = await confirmDialog({
    title: `Restore version ${version}?`,
    message: "It becomes the glossary again. What is saved now goes to the history, so this can be undone.",
    confirmLabel: "Restore",
  });
  if (!confirmed) return;
  await post(`/api/series/${state.seriesId}/glossary/restore`, { lang: state.lang, version });
  toast(`Version ${version} restored`, "success");
  await load("Restored. Translations made with other rules are flagged stale, not retranslated.");
}

async function copyFrom(sourceId, merge) {
  if (!sourceId) throw new Error("Choose a series to copy from");
  const confirmed = await confirmDialog({
    title: "Copy this glossary?",
    message: merge ? "Its entries are added to this glossary; this series keeps its own on conflicts."
      : "This glossary is replaced by the copy. The current one stays in the history.",
    confirmLabel: "Copy",
  });
  if (!confirmed) return;
  const result = await post(`/api/series/${state.seriesId}/glossary/copy`, { lang: state.lang, from_series: sourceId, merge });
  toast(`Copied from ${result.from}`, "success");
  await load(`Copied from ${result.from}.`);
}

// ---- Loading -------------------------------------------------------------------------------------------

async function load(message = "") {
  const [info, glossary] = await Promise.all([
    get(`/api/library/series/${state.seriesId}/units`),
    glossaryApi("GET"),
  ]);
  state.title = info.series.title || info.series.key;
  state.languages = info.languages;
  setHeading(`${state.title} glossary`, "Names, terms and forms of address for this series.");
  crumbs($("glossary-crumbs"), [["Library", "#library"], [state.title, `#library/${state.seriesId}`], [`Glossary · ${state.lang}`]]);
  $("glossary-style").href = `#style/${state.seriesId}/${state.lang}`;
  renderLanguages();
  state.doc = glossary.doc || {};
  $("glossary-yaml").value = glossary.yaml || "";
  $("glossary-yaml").placeholder = EXAMPLE;
  renderForm();
  showMode(state.mode);
  const notes = [message];
  if (glossary.stale_units) notes.push(`${plural(glossary.stale_units, "translation")} already stale because the rules changed after they were made.`);
  if (!glossary.exists && !message) notes.push("No glossary yet. Add a row, or paste YAML in the YAML tab.");
  showResult("ok", notes.filter(Boolean).join(" "));
  if ($("glossary-more").open) await renderMore();
}

export default {
  title: "Glossary",
  subtitle: "Names, terms and forms of address for this series.",
  nav: "library",

  init() {
    $("glossary-form").addEventListener("input", onEdit);
    $("glossary-form").addEventListener("change", onEdit);
    $("glossary-yaml").addEventListener("input", onEdit);
    document.querySelectorAll("#glossary-tabs .tab").forEach((el) => el.addEventListener("click", () => switchMode(el.dataset.tab)));
    $("glossary-more").addEventListener("toggle", () => { if ($("glossary-more").open) renderMore().catch((error) => toast(error.message, "error")); });
    $("glossary-check").addEventListener("click", (event) => runAction(event.currentTarget, () => submit(true)));
    $("glossary-save").addEventListener("click", (event) => runAction(event.currentTarget, () => submit(false)));
    $("view-glossary").addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty()) runAction($("glossary-save"), () => submit(false));
      }
    });
    window.addEventListener("beforeunload", (event) => {
      if (state.seriesId && !$("view-glossary").hidden && dirty()) event.preventDefault();
    });
  },

  async enter(param) {
    const [idText, lang] = (param || "").split("/");
    const id = Number(idText);
    if (!Number.isInteger(id) || id <= 0) {
      window.location.hash = "#library";
      return;
    }
    Object.assign(state, { seriesId: id, lang: lang || state.lang || "vi", mode: "form" });
    await load();
  },
};
