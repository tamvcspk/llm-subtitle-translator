import type { CharacterCard, GlossaryCharacter, GlossaryDoc, Style } from "../api/types";
import { genericProblem } from "./address";
import { canonicalNames, cleanCharacter, comparePosition } from "./glossary";
import { AXES, INHERIT, axisValue, cleanStyle } from "./style";

/** One character as the page shows it: its Name (an entry of the glossary) and its Voice (a card); either may be missing. */
export interface Person {
  name: string;
  entry: GlossaryCharacter | null;
  card: CharacterCard | null;
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** Every name the glossary or a card knows, glossary order first, matched without regard to case. */
export function people(doc: GlossaryDoc, cards: CharacterCard[]): Person[] {
  const out: Person[] = (doc.characters ?? []).map((entry) => ({ name: entry.name, entry, card: null }));
  for (const card of cards) {
    const found = out.find((person) => same(person.name, card.name));
    if (found) found.card = card;
    else out.push({ name: card.name, entry: null, card });
  }
  return out;
}

/**
 * The other names of a character, one list for both halves: what the glossary entry and the card say, without repeats and
 * without the character's own name (P19b: the form edits one `Aliases` box; Save writes it to both).
 */
export function aliasesOf(person: Pick<Person, "name" | "entry" | "card"> | null): string[] {
  if (person === null) return [];
  return uniqueNames([...(person.entry?.aliases ?? []), ...(person.card?.aliases ?? [])], person.name);
}

function uniqueNames(names: readonly string[], own: string): string[] {
  const seen = new Set([own.trim().toLowerCase()]);
  const out: string[] = [];
  for (const raw of names) {
    const name = raw.trim();
    if (name === "" || seen.has(name.toLowerCase())) continue;
    seen.add(name.toLowerCase());
    out.push(name);
  }
  return out;
}

/** The form of the Add/Edit sheet; lists are one entry per line, as the admin types them. */
export interface PersonDraft {
  /** The name before editing; `null` for a new character. Renaming moves both halves. */
  original: string | null;
  name: string;
  target: string;
  gender: string;
  description: string;
  speech: string;
  aliases: string;
  catchphrases: string;
  samples: string;
  style: Style;
  always: boolean;
  /** Older cards may carry these; they are shown read-only and written back as they were. */
  address: Array<{ to: string; form: string }>;
}

const lines = (value: string): string[] =>
  value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");

const commas = (value: string): string[] =>
  value
    .split(/[,\n]/)
    .map((part) => part.trim())
    .filter((part) => part !== "");

export function draftOf(person: Person | null): PersonDraft {
  return {
    original: person?.name ?? null,
    name: person?.name ?? "",
    target: person?.entry?.target ?? "",
    gender: person?.entry?.gender ?? "",
    description: person?.entry?.description ?? "",
    speech: person?.card?.speech ?? "",
    aliases: aliasesOf(person).join(", "),
    catchphrases: (person?.card?.catchphrases ?? []).join("\n"),
    samples: (person?.card?.samples ?? []).join("\n"),
    style: person?.card?.style ?? {},
    always: person?.card?.always ?? false,
    address: person?.card?.address ?? [],
  };
}

export function sameDraft(a: PersonDraft, b: PersonDraft): boolean {
  return JSON.stringify(normal(a)) === JSON.stringify(normal(b));
}

function normal(draft: PersonDraft) {
  return { ...draft, name: draft.name.trim(), style: cleanStyle(draft.style) };
}

/** The Voice half, as `PUT …/cards` takes it. */
export function cardOf(draft: PersonDraft): CharacterCard {
  const card: CharacterCard = { name: draft.name.trim() };
  const aliases = aliasList(draft);
  if (aliases.length > 0) card.aliases = aliases;
  if (draft.speech.trim()) card.speech = draft.speech.trim();
  if (draft.address.length > 0) card.address = draft.address;
  const catchphrases = lines(draft.catchphrases);
  if (catchphrases.length > 0) card.catchphrases = catchphrases;
  const samples = lines(draft.samples);
  if (samples.length > 0) card.samples = samples;
  const style = cleanStyle(draft.style);
  if (Object.keys(style).length > 0) card.style = style;
  if (draft.always) card.always = true;
  return card;
}

/** The Name half, as an entry of the glossary's `characters`. */
export function entryOf(draft: PersonDraft): GlossaryCharacter {
  return cleanCharacter({ name: draft.name, target: draft.target, gender: draft.gender, description: draft.description, aliases: aliasList(draft) });
}

/** The names of the `Aliases` box, as they are stored. */
export function aliasList(draft: Pick<PersonDraft, "aliases" | "name">): string[] {
  return uniqueNames(commas(draft.aliases), draft.name);
}

/**
 * What is wrong with the names typed in the form, before Save (P19b): a form of address is not a name, and a name is one
 * person's. `name` is for the `Source name` box, `aliases` for the `Aliases` box. Reading a glossary that already holds
 * such a name is not an error: only typing one is.
 */
export function nameProblems(draft: Pick<PersonDraft, "name" | "aliases" | "original">, all: readonly Person[]): { name: string | null; aliases: string | null } {
  const own = (draft.original ?? draft.name).trim().toLowerCase();
  const others = all.filter((person) => person.name.trim().toLowerCase() !== own);
  const owner = (name: string) => others.find((person) => [person.name, ...aliasesOf(person)].some((known) => known.trim().toLowerCase() === name.trim().toLowerCase()));
  const named = draft.name.trim();
  const clash = named !== "" ? owner(named) : undefined;
  const nameProblem = genericProblem(named) ?? (clash ? `"${named}" is already ${clash.name === named ? "a character" : `an alias of ${clash.name}`}.` : null);
  let aliasProblem: string | null = null;
  for (const alias of commas(draft.aliases)) {
    const taken = owner(alias);
    aliasProblem = genericProblem(alias) ?? (taken ? `"${alias}" already belongs to ${taken.name}.` : null);
    if (aliasProblem) break;
  }
  return { name: nameProblem, aliases: aliasProblem };
}

/** An Alias-only change must not make a Voice: a card is made when the admin says how they speak, not for a second name. */
const hasVoice = (card: CharacterCard) => Object.keys(card).some((key) => key !== "name" && key !== "aliases");
const hasNameDetails = (entry: GlossaryCharacter) => Object.keys(entry).some((key) => key !== "name");

/** What saving this draft writes where. A half is written when it exists already or the admin filled it in; a new character always gets its Name entry. */
export function plan(draft: PersonDraft, person: Person | null): { entry: GlossaryCharacter | null; card: CharacterCard | null } {
  const entry = entryOf(draft);
  const card = cardOf(draft);
  return {
    entry: person === null || person.entry !== null || hasNameDetails(entry) ? entry : null,
    card: (person?.card ?? null) !== null || hasVoice(card) ? card : null,
  };
}

/** The glossary with this character's Name entry written (replacing the one it had) or, with `entry: null`, left as it is. */
export function withEntry(doc: GlossaryDoc, entry: GlossaryCharacter | null, original: string | null): GlossaryDoc {
  if (entry === null) return doc;
  const list = doc.characters ?? [];
  const index = original === null ? -1 : list.findIndex((item) => same(item.name, original));
  const next = index >= 0 ? list.map((item, i) => (i === index ? entry : item)) : [...list, entry];
  return { ...doc, characters: next };
}

export function withoutEntry(doc: GlossaryDoc, name: string): GlossaryDoc {
  return { ...doc, characters: (doc.characters ?? []).filter((item) => !same(item.name, name)) };
}

/** "Swearing = Keep · Tone = blunt": the axes a card sets, in the words of the form. */
export function styleSummary(style: Style): string {
  const parts: string[] = [];
  for (const axis of AXES) {
    const value = axisValue(axis, style);
    if (value === INHERIT) continue;
    parts.push(`${axis.label} = ${axis.options.find((option) => option.value === value)?.label ?? value}`);
  }
  if (style.tone) parts.push(`Tone = ${style.tone}`);
  return parts.join(" · ");
}

export interface AddressStageView {
  /** What this character calls the other, and what the other calls this one (`self–other`). */
  says: string;
  hears: string;
  from?: string;
  until?: string;
}

export interface Address {
  with: string;
  /** First stage's forms (convenience for single-stage display). */
  says: string;
  hears: string;
  /** All stages for this partner, sorted by `from` (P18d). */
  stages: AddressStageView[];
}

export function formatAddressWindow(stage: Pick<AddressStageView, "from" | "until">): string {
  if (stage.from && stage.until) return `${stage.from}–${stage.until}`;
  if (stage.from) return `from ${stage.from}`;
  if (stage.until) return `until ${stage.until}`;
  return "";
}

/** The forms of address of the glossary that name this character, grouped by partner with `from–until` windows (P18d). */
export function addressFor(doc: GlossaryDoc, name: string): Address[] {
  // A rule written with an alias (`Carolyn Miller`) is a rule of the character, and so is the partner's alias (P19b).
  const canon = canonicalNames(doc.characters);
  const byPartner = new Map<string, { with: string; stages: AddressStageView[] }>();
  for (const rule of doc.address_rules ?? []) {
    let partner: string | null = null;
    let says = "";
    let hears = "";
    if (same(canon(rule.a), canon(name))) {
      partner = rule.b;
      says = rule.a_to_b ?? "";
      hears = rule.b_to_a ?? "";
    } else if (same(canon(rule.b), canon(name))) {
      partner = rule.a;
      says = rule.b_to_a ?? "";
      hears = rule.a_to_b ?? "";
    }
    if (partner === null) continue;
    const key = canon(partner).toLowerCase();
    const entry = byPartner.get(key) ?? { with: canon(partner), stages: [] };
    entry.stages.push({
      says,
      hears,
      ...(rule.from ? { from: rule.from } : {}),
      ...(rule.until ? { until: rule.until } : {}),
    });
    byPartner.set(key, entry);
  }
  return [...byPartner.values()].map((group) => {
    group.stages.sort((a, b) => comparePosition(a.from, b.from));
    const first = group.stages[0]!;
    return { with: group.with, says: first.says, hears: first.hears, stages: group.stages };
  });
}
