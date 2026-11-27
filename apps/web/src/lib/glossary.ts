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
  if (character.aliases && character.aliases.length > 0) out.aliases = character.aliases; // not edited here yet: kept as stored
  return out;
}

/** A row the admin added and has not filled in: it is not sent (the worker would refuse it) and does not count as a change. */
const blankTerm = (term: GlossaryTerm) => Object.values(cleanTerm(term)).every((value) => value === "" || (Array.isArray(value) && value.length === 0));
const blankAddress = (rule: GlossaryAddress) => Object.keys(cleanAddress(rule)).every((key) => text(rule[key as keyof GlossaryAddress]) === "");
const blankCharacter = (character: GlossaryCharacter) => Object.values(cleanCharacter(character)).every((value) => value === "" || (Array.isArray(value) && value.length === 0));

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

/** Unordered pair key: `(A, B)` and `(B, A)` map to the same group (P18d, P18f). */
export function addressPairKey(a: string, b: string): string {
  const pair = [text(a).toLowerCase(), text(b).toLowerCase()].sort();
  return `${pair[0]}\0${pair[1]}`;
}

/** Sort key for an episode/time position (`""` sorts first, then season, episode, seconds). */
export function positionOrder(value: string | undefined): [number, number, number, number] {
  const v = text(value);
  if (!v) return [0, 0, 0, 0];
  const match = /^S(\d+)E(\d+)(?:\s+(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?)?$/i.exec(v);
  if (!match) return [2, 0, 0, 0];
  const season = Number(match[1]);
  const episode = Number(match[2]);
  const h = match[3] ? Number(match[3]) : 0;
  const m = match[4] ? Number(match[4]) : 0;
  const s = match[5] ? Number(match[5]) : 0;
  const ms = match[6] ? Number(match[6].padEnd(3, "0")) : 0;
  return [1, season, episode, ((h * 60 + m) * 60 + s) * 1000 + ms];
}

export function comparePosition(a: string | undefined, b: string | undefined): number {
  const pa = positionOrder(a);
  const pb = positionOrder(b);
  for (let i = 0; i < pa.length; i++) {
    if (pa[i]! !== pb[i]!) return pa[i]! - pb[i]!;
  }
  return 0;
}

export interface AddressPairGroup {
  key: string;
  label: string;
  items: Array<{ rule: GlossaryAddress; index: number }>;
}

/** Group forms of address by unordered character pair and sort each pair's stages by `from` (P18d). */
export function groupAddressRules(rules: readonly GlossaryAddress[]): AddressPairGroup[] {
  const groups = new Map<string, AddressPairGroup>();
  rules.forEach((rule, index) => {
    const a = text(rule.a);
    const b = text(rule.b);
    const isBlank = !a && !b;
    const key = isBlank ? `__new_${index}` : addressPairKey(a, b);
    const group = groups.get(key) ?? {
      key,
      label: isBlank ? `New form of address` : `${a || "?"} ↔ ${b || "?"}`,
      items: [],
    };
    group.items.push({ rule, index });
    groups.set(key, group);
  });
  for (const group of groups.values()) {
    group.items.sort((x, y) => comparePosition(x.rule.from, y.rule.from) || x.index - y.index);
  }
  return [...groups.values()];
}

const sameFold = (a: string | undefined, b: string | undefined) => text(a).toLowerCase() === text(b).toLowerCase();

/**
 * Collapse duplicate or adjacent identical forms of address of the same unordered pair (P18f): `(A, B)` and `(B, A)`
 * are compared in the same orientation and adjacent rules with identical forms merge into one window.
 */
export function dedupeAddressRules(rules: readonly GlossaryAddress[]): { rules: GlossaryAddress[]; removed: number } {
  const result: GlossaryAddress[] = [];
  for (const group of groupAddressRules(rules)) {
    if (group.key.startsWith("__new_")) {
      for (const item of group.items) result.push(item.rule);
      continue;
    }
    const anchor = group.items[0]!.rule;
    const anchorA = text(anchor.a).toLowerCase();
    const merged: GlossaryAddress[] = [];
    for (const { rule } of group.items) {
      const reversed = text(rule.a).toLowerCase() !== anchorA && text(rule.b).toLowerCase() === anchorA;
      const oriented: GlossaryAddress = reversed
        ? {
            ...rule,
            a: anchor.a,
            b: anchor.b,
            a_to_b: rule.b_to_a,
            b_to_a: rule.a_to_b,
          }
        : { ...rule };
      const prev = merged.at(-1);
      // R18-7: only merge when windows are contiguous (prev has no `until`, or prev.until matches oriented.from).
      // Keep prev.from (never widen into earlier episodes) and take `until` from the last merged rule.
      const contiguous = prev !== undefined && (!text(prev.until) || sameFold(prev.until, oriented.from));
      if (prev && contiguous && sameFold(prev.a_to_b, oriented.a_to_b) && sameFold(prev.b_to_a, oriented.b_to_a)) {
        if (oriented.until) prev.until = oriented.until;
        else delete prev.until;
        if (!prev.note && oriented.note) prev.note = oriented.note;
        continue;
      }
      merged.push(oriented);
    }
    result.push(...merged);
  }
  return { rules: result, removed: rules.length - result.length };
}

export function filterTerms(terms: readonly GlossaryTerm[], query: string): Array<{ term: GlossaryTerm; index: number }> {
  const needle = text(query).toLowerCase();
  return terms
    .map((term, index) => ({ term, index }))
    .filter(({ term }) => {
      if (!needle) return true;
      return [term.source, term.target, term.note ?? "", ...(term.aliases ?? [])].some((part) => part.toLowerCase().includes(needle));
    });
}

export function filterAddressGroups(groups: readonly AddressPairGroup[], query: string): AddressPairGroup[] {
  const needle = text(query).toLowerCase();
  if (!needle) return [...groups];
  return groups.filter(
    (group) =>
      group.label.toLowerCase().includes(needle) ||
      group.items.some(({ rule }) =>
        [rule.a, rule.b, rule.a_to_b ?? "", rule.b_to_a ?? "", rule.from ?? "", rule.until ?? "", rule.note ?? ""].some((part) =>
          part.toLowerCase().includes(needle),
        ),
      ),
  );
}
