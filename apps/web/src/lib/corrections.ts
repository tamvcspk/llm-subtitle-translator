import type { Correction } from "../api/types";
import type { CorrectionScope } from "../api/queries";

export type CorrectionKind = Correction["kind"];

export const KIND_LABEL: Record<CorrectionKind, string> = {
  term: "Term",
  address: "Address",
  note: "Note",
  cue_edit: "Cue edit",
};

/** A note and a cue edit belong to one episode; only a term or a form of address can reach wider (API.md). */
export function scopesFor(kind: CorrectionKind, isEpisode: boolean): Array<{ value: CorrectionScope; label: string }> {
  const own = { value: "episode" as const, label: isEpisode ? "Episode" : "This movie" };
  if (kind === "note" || kind === "cue_edit") return [own];
  return isEpisode
    ? [own, { value: "forward", label: "From here on" }, { value: "series", label: "Whole series" }]
    : [own, { value: "series", label: "Movie glossary" }];
}

const text = (value: unknown): string => (typeof value === "string" ? value : value == null ? "" : String(value));

/** One line a person can read at a glance: `"Enforcer" → "Cảnh vệ"`. */
export function describeCorrection(correction: Correction): string {
  const p = correction.payload;
  switch (correction.kind) {
    case "term":
      return `"${text(p.source)}" → "${text(p.target)}"`;
    case "address": {
      const ways = [text(p.a_to_b) && `${text(p.a)} → ${text(p.b)}: ${text(p.a_to_b)}`, text(p.b_to_a) && `${text(p.b)} → ${text(p.a)}: ${text(p.b_to_a)}`];
      return ways.filter(Boolean).join(" · ");
    }
    case "note":
      return text(p.text);
    case "cue_edit":
      return `Cue #${text(p.cue)} locked`;
  }
}

/** The payload of the form for one kind, or the first thing wrong with it (what the person must still fill in). */
export function buildPayload(
  kind: CorrectionKind,
  fields: Record<string, string>,
): { payload: Record<string, unknown> } | { problem: string } {
  const f = (name: string) => (fields[name] ?? "").trim();
  switch (kind) {
    case "term":
      return f("source") && f("target") ? { payload: { source: f("source"), target: f("target") } } : { problem: "Give the term and its translation." };
    case "address": {
      if (!f("a") || !f("b")) return { problem: "Name the two people." };
      if (!f("a_to_b") && !f("b_to_a")) return { problem: "Say how one addresses the other." };
      const payload: Record<string, unknown> = { a: f("a"), b: f("b") };
      if (f("a_to_b")) payload.a_to_b = f("a_to_b");
      if (f("b_to_a")) payload.b_to_a = f("b_to_a");
      if (f("from_cue")) {
        const cue = Number(f("from_cue"));
        if (!Number.isInteger(cue) || cue < 1) return { problem: "The cue number must be a whole number from 1." };
        payload.from_cue = cue;
      }
      return { payload };
    }
    case "note":
      return f("text") ? { payload: { text: f("text") } } : { problem: "Write the note." };
    case "cue_edit": {
      const cue = Number(f("cue"));
      if (!Number.isInteger(cue) || cue < 1) return { problem: "The cue number must be a whole number from 1." };
      return f("text") ? { payload: { cue, text: f("text") } } : { problem: "Write the new line." };
    }
  }
}
