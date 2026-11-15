// The fields of a style (FR-18.2), shared by a series' Style page and a character card's overrides.
// A field left at "Not set" says nothing, so the layer below shows through.
import { h } from "../ui.js";

const NOT_SET = ["", "Not set"];

export const STYLE_FIELDS = [
  { key: "formality", label: "Formality", options: [NOT_SET, ["casual", "Casual, spoken"], ["neutral", "Neutral"], ["formal", "Formal"]] },
  { key: "tone", label: "Tone", text: true, placeholder: "humorous, dry, grave…" },
  { key: "profanity", label: "Swearing", options: [NOT_SET, ["keep", "Keep its strength"], ["soften", "Soften it"], ["remove", "Leave it out"]] },
  { key: "localization", label: "Idioms and wordplay", options: [NOT_SET, ["literal", "Stay close to the source"], ["balanced", "Natural, recognisable"], ["adapt", "Replace with local equivalents"]] },
  { key: "dialect", label: "Dialect", options: [NOT_SET, ["north", "Northern"], ["south", "Southern"], ["neutral", "Neutral"]] },
  { key: "honorifics", label: "Honorifics (anime)", options: [NOT_SET, ["keep", "Keep -san, -kun…"], ["translate", "Translate them"]] },
  { key: "max_cps", label: "Characters per second", number: [1, 60], placeholder: "17" },
  { key: "max_line_length", label: "Longest line", number: [10, 120], placeholder: "42" },
  { key: "max_lines", label: "Lines per cue", number: [1, 4], placeholder: "2" },
  { key: "allow_condense", label: "Fast dialogue", options: [NOT_SET, ["true", "Shorten the wording to fit"], ["false", "Never drop meaning to fit"]] },
  { key: "notes", label: "Notes", textarea: true, wide: true, placeholder: "A family sitcom; jokes should land in Vietnamese." },
];

export const STYLE_LABELS = Object.fromEntries(STYLE_FIELDS.map((f) => [f.key, f.label]));

function control(field, id) {
  const common = { id, class: "input", dataset: { styleKey: field.key } };
  if (field.options) {
    return h("select", common, field.options.map(([value, text]) => h("option", { value, text })));
  }
  if (field.textarea) return h("textarea", { ...common, rows: 3, placeholder: field.placeholder || "" });
  if (field.number) {
    return h("input", { ...common, type: "number", min: field.number[0], max: field.number[1], placeholder: field.placeholder || "" });
  }
  return h("input", { ...common, type: "text", placeholder: field.placeholder || "", autocomplete: "off" });
}

/** The field elements of a style form, ids prefixed with `prefix`. */
export function styleFields(prefix) {
  return STYLE_FIELDS.map((field) => h("div", { class: `field${field.wide ? " field-wide" : ""}` },
    h("label", { for: `${prefix}-${field.key}`, text: field.label }), control(field, `${prefix}-${field.key}`)));
}

/** Fill a form from a style document (unset keys clear their field). */
export function writeStyle(root, doc = {}) {
  root.querySelectorAll("[data-style-key]").forEach((el) => {
    const value = doc[el.dataset.styleKey];
    el.value = value === undefined || value === null ? "" : String(value);
  });
}

/** The style document a form holds: only the fields that are set. */
export function readStyle(root) {
  const doc = {};
  root.querySelectorAll("[data-style-key]").forEach((el) => {
    const key = el.dataset.styleKey;
    const raw = el.value.trim();
    if (!raw) return;
    const field = STYLE_FIELDS.find((f) => f.key === key);
    if (field?.number) doc[key] = Number(raw);
    else if (key === "allow_condense") doc[key] = raw === "true";
    else doc[key] = raw;
  });
  return doc;
}

/** "Formality: formal" style chips for a style document. */
export function describeStyle(doc) {
  return STYLE_FIELDS.filter((f) => doc[f.key] !== undefined && doc[f.key] !== "" && f.key !== "notes").map((f) => {
    const value = doc[f.key];
    const option = f.options?.find(([v]) => v === String(value));
    return `${f.label}: ${option ? option[1] : value}`;
  });
}
