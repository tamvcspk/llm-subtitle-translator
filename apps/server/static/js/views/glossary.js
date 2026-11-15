// Glossary: the YAML rules of one series and target language (FR-4, FR-10.2). `#glossary/<series id>/<language>`.
// "Check" validates and counts how many translations a save would make stale, without writing anything.
import { api, get } from "../api.js";
import { plural } from "../format.js";
import { crumbs, h, runAction, setHeading, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);

const EXAMPLE = `# Everything is optional. Rules here always beat what the model infers.
style: casual, keep honorifics
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

const state = { seriesId: null, lang: "vi", title: "", saved: "", languages: [] };

const dirty = () => $("glossary-yaml").value !== state.saved;

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

async function submit(dryRun) {
  const data = await glossaryApi("PUT", { yaml: $("glossary-yaml").value, dry_run: dryRun });
  if (dryRun) {
    showResult("ok", `Valid. ${impactText(data)}`);
    return;
  }
  state.saved = $("glossary-yaml").value;
  $("glossary-save").disabled = true;
  showResult("ok", `Saved. ${impactText(data)} Stale translations are never retranslated automatically; open an episode to retranslate it.`);
  toast("Glossary saved", "success");
}

function glossaryApi(method, body) {
  const url = `/api/series/${state.seriesId}/glossary?lang=${encodeURIComponent(state.lang)}`;
  return method === "PUT" ? api(url, { method, body }) : get(url);
}

function renderLanguages() {
  const languages = state.languages.includes(state.lang) ? state.languages : [...state.languages, state.lang];
  $("glossary-langs").replaceChildren(...languages.map((lang) =>
    h("a", { class: "chip chip-link", href: `#glossary/${state.seriesId}/${lang}`, ...(lang === state.lang ? { "aria-current": "page" } : {}) }, lang)));
}

async function load() {
  const [info, glossary] = await Promise.all([
    get(`/api/library/series/${state.seriesId}/units`),
    glossaryApi("GET"),
  ]);
  state.title = info.series.title || info.series.key;
  state.languages = info.languages;
  setHeading(`${state.title} glossary`, "Names, terms and forms of address for this series.");
  crumbs($("glossary-crumbs"), [["Library", "#library"], [state.title, `#library/${state.seriesId}`], [`Glossary · ${state.lang}`]]);
  renderLanguages();
  $("glossary-yaml").value = glossary.yaml || "";
  state.saved = $("glossary-yaml").value;
  $("glossary-yaml").placeholder = EXAMPLE;
  $("glossary-save").disabled = true;
  showResult("ok", glossary.stale_units
    ? `${plural(glossary.stale_units, "translation")} already stale because the rules changed after they were made.`
    : "");
  if (!glossary.exists) showResult("ok", "No glossary yet. The grey text shows the format; the file is created when you save.");
}

export default {
  title: "Glossary",
  subtitle: "Names, terms and forms of address for this series.",
  nav: "library",

  init() {
    $("glossary-yaml").addEventListener("input", () => { $("glossary-save").disabled = !dirty(); });
    $("glossary-check").addEventListener("click", (event) => runAction(event.currentTarget, () => submit(true)));
    $("glossary-save").addEventListener("click", (event) => runAction(event.currentTarget, () => submit(false)));
    $("glossary-yaml").addEventListener("keydown", (event) => {
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
    Object.assign(state, { seriesId: id, lang: lang || state.lang || "vi" });
    await load();
  },
};
