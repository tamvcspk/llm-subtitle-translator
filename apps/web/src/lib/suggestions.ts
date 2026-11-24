import type {
  CharacterPayload,
  Evidence,
  GlossaryAddress,
  GlossaryTerm,
  Suggestion,
  SuggestionKind,
  SuggestionPayload,
  SuggestionSource,
} from "../api/types";

export const KIND_LABEL: Record<SuggestionKind, string> = {
  term: "Term",
  address_rule: "Form of address",
  character: "Character",
};

/** Where a suggestion came from, in the words of the person who reads it. */
export const SOURCE_LABEL: Record<SuggestionSource, string> = {
  facts: "Story facts",
  memory: "Story memory",
  feedback: "Your edits",
  research: "Research",
};

const text = (value: string | undefined): string => (value ?? "").trim();

/** What a suggestion says, in one line and a few details: `Kyber crystal → tinh thể Kyber`. */
export function describe(suggestion: Pick<Suggestion, "kind" | "payload">): { title: string; details: string[] } {
  const { kind, payload } = suggestion;
  if (kind === "term") {
    const term = payload as GlossaryTerm;
    const details: string[] = [];
    if (term.locked) details.push("Locked: always used");
    if (term.aliases?.length) details.push(`Also: ${term.aliases.join(", ")}`);
    if (term.from) details.push(`From ${term.from}${term.until ? ` until ${term.until}` : ""}`);
    if (term.note) details.push(term.note);
    return { title: `${term.source} → ${term.target}`, details };
  }
  if (kind === "address_rule") {
    const rule = payload as GlossaryAddress & {
      stages?: Array<{ a_to_b?: string; b_to_a?: string; from?: string; until?: string; note?: string }>;
      change_from?: string;
    };
    const details: string[] = [];
    if (rule.stages && rule.stages.length > 1) {
      for (const stage of rule.stages) {
        const win = stage.from
          ? `From ${stage.from}${stage.until ? ` until ${stage.until}` : ""}`
          : stage.until
            ? `Until ${stage.until}`
            : "Base";
        const forms = [
          stage.a_to_b ? `${rule.a} calls ${rule.b} “${stage.a_to_b}”` : "",
          stage.b_to_a ? `${rule.b} calls ${rule.a} “${stage.b_to_a}”` : "",
        ]
          .filter(Boolean)
          .join(" · ");
        details.push(`${win}: ${forms}`);
      }
      if (rule.note) details.push(rule.note);
    } else {
      if (rule.a_to_b) details.push(`${rule.a} calls ${rule.b} “${rule.a_to_b}”`);
      if (rule.b_to_a) details.push(`${rule.b} calls ${rule.a} “${rule.b_to_a}”`);
      if (rule.from) details.push(`From ${rule.from}${rule.until ? ` until ${rule.until}` : ""}`);
      if (rule.note) details.push(rule.note);
    }
    const baseTitle = `${rule.a} ↔ ${rule.b}`;
    return { title: rule.change_from ? `${baseTitle} · change from ${rule.change_from}?` : baseTitle, details };
  }
  const character = payload as CharacterPayload;
  const details: string[] = [];
  if (character.gender) details.push(`Gender: ${character.gender}`);
  if (character.description) details.push(character.description);
  if (character.card?.speech) details.push(`Voice: ${character.card.speech}`);
  return { title: character.target && character.target !== character.name ? `${character.name} → ${character.target}` : character.name, details };
}

export interface EvidenceLine {
  /** Where it is: `S01E05 · cue 5 · 00:00:08`, or empty. */
  where: string;
  text: string;
  /** The episode page that holds the cue, when the generator named one. */
  unitId?: number;
  url?: string;
}

export function evidenceLines(evidence: readonly Evidence[]): EvidenceLine[] {
  return evidence.map((item) => {
    if (typeof item === "string") return { where: "", text: item };
    const where = [item.episode, item.cue !== undefined ? `cue ${item.cue}` : "", item.timecode].filter((part) => part).join(" · ");
    return { where, text: item.text ?? item.url ?? "", unitId: item.unit_id, url: item.url };
  });
}

/** A research answer cites pages; only an `https` address becomes a link. */
export function safeUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

/** The site a research source is on, as a person reads it: `wikipedia.org`, never `https://www.wikipedia.org/wiki/...`. */
export function sourceName(value: string): string {
  const href = safeUrl(value);
  if (href === null) return value;
  return new URL(href).hostname.replace(/^www\./i, "");
}

export interface SeriesGroup {
  seriesId: number;
  series: string;
  items: Suggestion[];
}

/** The inbox grouped by series, in the order the series first appear (the worker lists them by series). */
export function groupBySeries(items: readonly Suggestion[]): SeriesGroup[] {
  const groups = new Map<number, SeriesGroup>();
  for (const item of items) {
    const group = groups.get(item.series_id) ?? { seriesId: item.series_id, series: item.series || `Series ${item.series_id}`, items: [] };
    group.items.push(item);
    groups.set(item.series_id, group);
  }
  return [...groups.values()];
}

// -- editing before accepting -----------------------------------------------------------------------------------------

/** The boxes of the edit sheet, by kind; every box is text, whatever the field holds. */
export interface EditDraft {
  a: string; // term: source · rule: a · character: name
  b: string; // term: target · rule: b · character: target
  c: string; // rule: what a calls b · character: gender
  d: string; // rule: what b calls a · character: description
  note: string; // term and rule
  speech: string; // character: its voice
  locked: boolean; // term
}

export function draftOf(suggestion: Pick<Suggestion, "kind" | "payload">): EditDraft {
  const { kind, payload } = suggestion;
  if (kind === "term") {
    const term = payload as GlossaryTerm;
    return { a: term.source, b: term.target, c: "", d: "", note: term.note ?? "", speech: "", locked: Boolean(term.locked) };
  }
  if (kind === "address_rule") {
    const rule = payload as GlossaryAddress;
    return { a: rule.a, b: rule.b, c: rule.a_to_b ?? "", d: rule.b_to_a ?? "", note: rule.note ?? "", speech: "", locked: false };
  }
  const character = payload as CharacterPayload;
  return {
    a: character.name,
    b: character.target ?? "",
    c: character.gender ?? "",
    d: character.description ?? "",
    note: "",
    speech: character.card?.speech ?? "",
    locked: false,
  };
}

/**
 * The payload with the edit laid on it. A field the sheet has no box for (aliases, `from`, `until`, the rest of a card)
 * goes back as it came, so editing a word never drops what the generator found.
 */
export function payloadOf(suggestion: Pick<Suggestion, "kind" | "payload">, draft: EditDraft): SuggestionPayload {
  const { kind, payload } = suggestion;
  const put = <T extends object>(base: T, values: Record<string, string | boolean | undefined>): T => {
    const out = { ...base } as Record<string, unknown>;
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined || value === "" || value === false) delete out[key];
      else out[key] = value;
    }
    return out as T;
  };
  if (kind === "term") {
    return put(payload as GlossaryTerm, { source: text(draft.a), target: text(draft.b), note: text(draft.note), locked: draft.locked });
  }
  if (kind === "address_rule") {
    const rule = payload as GlossaryAddress & {
      stages?: Array<{ a_to_b?: string; b_to_a?: string; from?: string; until?: string; note?: string }>;
    };
    const next = put(rule, {
      a: text(draft.a),
      b: text(draft.b),
      a_to_b: text(draft.c),
      b_to_a: text(draft.d),
      note: text(draft.note),
    });
    if (Array.isArray(rule.stages) && rule.stages.length > 0) {
      const first = { ...rule.stages[0] };
      if (text(draft.c)) first.a_to_b = text(draft.c);
      else delete first.a_to_b;
      if (text(draft.d)) first.b_to_a = text(draft.d);
      else delete first.b_to_a;
      next.stages = [first, ...rule.stages.slice(1)];
    }
    return next;
  }
  const character = payload as CharacterPayload;
  const next = put(character, { name: text(draft.a), target: text(draft.b), gender: text(draft.c), description: text(draft.d) });
  const speech = text(draft.speech);
  if (speech) next.card = { ...(character.card ?? { name: text(draft.a) }), name: text(draft.a), speech };
  else if (character.card) {
    const rest = { ...character.card };
    delete rest.speech;
    next.card = rest;
  }
  return next;
}

/** What is missing before the worker would refuse it, or `null`. The worker checks too; this only says so first. */
export function draftProblem(kind: SuggestionKind, draft: EditDraft): string | null {
  if (kind === "term") return text(draft.a) && text(draft.b) ? null : "A term needs both the source and its translation.";
  if (kind === "address_rule") {
    if (!text(draft.a) || !text(draft.b)) return "Both characters need a name.";
    return text(draft.c) || text(draft.d) ? null : "Say how at least one of them addresses the other.";
  }
  return text(draft.a) ? null : "A character needs a name.";
}

export function sameDraft(a: EditDraft, b: EditDraft): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** `Accepting flags 12 episodes stale.`, or the line to show while it is being worked out (`null`). */
export function impactLine(wouldStale: number | undefined): string | null {
  if (wouldStale === undefined) return null;
  if (wouldStale === 0) return "No translation is affected.";
  return `Accepting flags ${wouldStale.toLocaleString()} translation${wouldStale === 1 ? "" : "s"} stale. Nothing is redone.`;
}

export function pendingLine(count: number): string {
  return count === 1 ? "1 suggestion" : `${count.toLocaleString()} suggestions`;
}
