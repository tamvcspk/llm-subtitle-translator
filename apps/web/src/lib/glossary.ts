import type { GlossaryAddress, GlossaryCharacter, GlossaryDoc, GlossaryTerm, StyleImpact } from "../api/types";

const text = (value: string | undefined): string => (value ?? "").trim();

function cleanTerm(term: GlossaryTerm): GlossaryTerm {
  const out: GlossaryTerm = { source: text(term.source), target: text(term.target) };
  if (term.aliases && term.aliases.length > 0) out.aliases = term.aliases;
  if (term.locked) out.locked = true;
  for (const key of ["note", "from", "until"] as const) if (text(term[key])) out[key] = text(term[key]);
  return out;
}

function cleanAddress(rule: GlossaryAddress): GlossaryAddress {
  const out: GlossaryAddress = { a: text(rule.a), b: text(rule.b) };
  for (const key of ["a_to_b", "b_to_a", "from", "until", "note"] as const) if (text(rule[key])) out[key] = text(rule[key]);
  return out;
}

export function cleanCharacter(character: GlossaryCharacter): GlossaryCharacter {
  const out: GlossaryCharacter = { name: text(character.name) };
  for (const key of ["target", "gender", "description"] as const) if (text(character[key])) out[key] = text(character[key]);
  return out;
}

/** A row the admin added and has not filled in: it is not sent (the worker would refuse it) and does not count as a change. */
const blankTerm = (term: GlossaryTerm) => Object.values(cleanTerm(term)).every((value) => value === "" || (Array.isArray(value) && value.length === 0));
const blankAddress = (rule: GlossaryAddress) => Object.keys(cleanAddress(rule)).every((key) => text(rule[key as keyof GlossaryAddress]) === "");
const blankCharacter = (character: GlossaryCharacter) => Object.keys(cleanCharacter(character)).every((key) => text(character[key as keyof GlossaryCharacter]) === "");

/** What would be stored: blank text trimmed away, empty rows and empty lists left out. Fields the form does not edit stay. */
export function cleanDoc(doc: GlossaryDoc): GlossaryDoc {
  const out: GlossaryDoc = {};
  if (doc.version !== undefined) out.version = doc.version;
  if (text(doc.target_lang)) out.target_lang = text(doc.target_lang);
  if (text(doc.style)) out.style = text(doc.style);
  const terms = (doc.terms ?? []).filter((term) => !blankTerm(term)).map(cleanTerm);
  if (terms.length > 0) out.terms = terms;
  const characters = (doc.characters ?? []).filter((character) => !blankCharacter(character)).map(cleanCharacter);
  if (characters.length > 0) out.characters = characters;
  const rules = (doc.address_rules ?? []).filter((rule) => !blankAddress(rule)).map(cleanAddress);
  if (rules.length > 0) out.address_rules = rules;
  return out;
}

export function sameDoc(a: GlossaryDoc, b: GlossaryDoc): boolean {
  return JSON.stringify(cleanDoc(a)) === JSON.stringify(cleanDoc(b));
}

/** The same list with item `index` changed. */
export function patchAt<T>(list: readonly T[], index: number, patch: Partial<T>): T[] {
  return list.map((item, i) => (i === index ? { ...item, ...patch } : item));
}

export function removeAt<T>(list: readonly T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

/** The line under the form: "Saving flags 12 episodes stale." (`verb` says what the admin is doing). `null` while it is being worked out. */
export function glossaryImpact(impact: Pick<StyleImpact, "would_stale"> | undefined, verb: string, noun: "episode" | "movie"): string | null {
  if (!impact) return null;
  const n = impact.would_stale;
  if (n === 0) return "No translation is affected.";
  return `${verb} flags ${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"} stale.`;
}

/** `S01E06` or `S01E06 00:32:10`: the worker checks it too, this only says so before Save. */
export function positionProblem(value: string | undefined): string | null {
  const v = text(value);
  if (v === "") return null;
  return /^S\d+E\d+(\s+\d+:\d{1,2}:\d{1,2}([.,]\d{1,3})?)?$/i.test(v) ? null : "Use S01E06, or S01E06 00:32:10";
}

export const NOTE_LABEL: Record<string, string> = {
  console: "saved in the console",
  import: "imported",
  correction: "from a correction",
  restore: "restored",
  copy: "copied from another series",
};
