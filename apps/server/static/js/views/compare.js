// Compare a candidate (a variant, or a result that could not go on disk) with the current translation, cue by cue,
// and record which is better (FR-18.8). `#compare/<unit id>/<version>/<language>`.
import { get, post } from "../api.js";
import { plural } from "../format.js";
import { confirmDialog, crumbs, emptyState, h, loadingState, runAction, setHeading, toast } from "../ui.js";

const $ = (id) => document.getElementById(id);

const state = { unitId: null, version: null, lang: null, data: null, detail: null, picks: new Map() };

const base = () => `/api/units/${state.unitId}`;
const code = (unit) => `S${String(unit.season ?? 0).padStart(2, "0")}E${String(unit.episode ?? 0).padStart(2, "0")}`;

function showResult(kind, text) {
  const box = $("compare-result");
  box.hidden = !text;
  box.className = `notice notice-${kind}`;
  box.textContent = text;
}

function choice(row, value, label) {
  return h("label", { class: "check check-inline" },
    h("input", {
      type: "radio", name: `pick-${row.n}`, value, checked: state.picks.get(row.n) === value,
      onChange: () => { state.picks.set(row.n, value); },
    }), h("span", { text: label }));
}

function render() {
  const data = state.data;
  const only = $("compare-differing").checked;
  const rows = data.rows.filter((row) => !only || row.differs);
  const params = data.params?.variant;
  $("compare-meta").textContent = [
    params ? `Variant “${params.label || "style"}”` : "Candidate",
    data.model, `${plural(data.differing, "line")} differ${data.differing === 1 ? "s" : ""}`,
    data.aligned ? "" : "the two do not have the same number of cues",
  ].filter(Boolean).join(" · ");
  const list = $("compare-list");
  if (!rows.length) {
    list.replaceChildren(emptyState("Nothing differs", only ? "Untick “Only lines that differ” to see every line." : "The two are identical."));
    return;
  }
  list.replaceChildren(...rows.map((row) => h("div", { class: "cue cue-compare" },
    h("div", { class: "cue-meta" }, h("span", { class: "cue-n", text: `#${row.n}` })),
    h("div", { class: "cue-src", text: row.source }),
    h("div", { class: "cue-dst" }, row.current == null ? h("span", { class: "muted", text: "—" }) : row.current),
    h("div", { class: "cue-dst" }, row.variant == null ? h("span", { class: "muted", text: "—" }) : row.variant),
    h("div", { class: "pick" }, row.differs
      ? [choice(row, "current", "Current"), choice(row, "variant", "Candidate"), choice(row, "tie", "Same")]
      : h("span", { class: "muted", text: "same" })))));
}

function picksBody() {
  return Object.fromEntries([...state.picks].map(([n, winner]) => [String(n), winner]));
}

async function record(apply) {
  if (!state.picks.size) throw new Error("Choose the better line for at least one cue first");
  const result = await post(`${base()}/versions/${state.version}/choose`, { lang: state.lang, picks: picksBody(), apply });
  if (apply && result.applied?.edited_cues?.length) {
    toast(`${plural(result.applied.edited_cues.length, "line")} from the candidate are now your edits of the current translation.`, "success");
    window.location.hash = `#episode/${state.unitId}/${state.lang}`;
    return;
  }
  showResult("ok", `${plural(result.recorded, "choice")} recorded. They add to the score of this style.`);
  toast("Choices recorded", "success");
}

async function adopt() {
  const confirmed = await confirmDialog({
    title: "Use the whole candidate?",
    message: "It is written to the file and becomes the current version. What is on disk now stays in History.",
    confirmLabel: "Use this version",
  });
  if (!confirmed) return;
  await post(`${base()}/versions/${state.version}/choose`, { lang: state.lang, whole: "variant", apply: true });
  toast("The candidate is now on disk", "success");
  window.location.hash = `#episode/${state.unitId}/${state.lang}`;
}

async function load() {
  $("compare-list").replaceChildren(loadingState("Comparing…"));
  const [data, detail] = await Promise.all([
    get(`${base()}/versions/${state.version}/compare?lang=${encodeURIComponent(state.lang)}`),
    get(base()),
  ]);
  state.data = data;
  state.detail = detail;
  state.picks = new Map();
  const isEpisode = detail.series_id != null;
  const title = isEpisode ? `${detail.series?.title || "Series"} ${code(detail)}` : (detail.title || "Movie");
  setHeading(`Compare · ${title}`, "Which of the two is better, line by line.");
  $("compare-title").textContent = title;
  crumbs($("compare-crumbs"), [
    ["Library", "#library"],
    ...(isEpisode ? [[detail.series?.title || "Series", `#library/${detail.series.id}`]] : []),
    [title, `#episode/${state.unitId}/${state.lang}`],
    [`Version ${state.version}`],
  ]);
  const open = data.status === "candidate";
  for (const id of ["compare-verdict", "compare-apply", "compare-adopt"]) $(id).disabled = !open;
  showResult("ok", open ? "" : `Version ${state.version} is ${data.status}: it can be read, but no longer judged.`);
  render();
}

export default {
  title: "Compare",
  subtitle: "Which of the two is better, line by line.",
  nav: "library",

  init() {
    $("compare-differing").addEventListener("change", () => state.data && render());
    $("compare-verdict").addEventListener("click", (event) => runAction(event.currentTarget, () => record(false)));
    $("compare-apply").addEventListener("click", (event) => runAction(event.currentTarget, () => record(true)));
    $("compare-adopt").addEventListener("click", (event) => runAction(event.currentTarget, adopt));
  },

  async enter(param) {
    const [unit, version, lang] = (param || "").split("/");
    const id = Number(unit);
    const number = Number(version);
    if (!Number.isInteger(id) || id <= 0 || !Number.isInteger(number) || number <= 0) {
      window.location.hash = "#library";
      return;
    }
    Object.assign(state, { unitId: id, version: number, lang: lang || null });
    await load();
  },
};
