import type { CompareRow, JudgeSummary, JudgeVerdict, Style } from "../api/types";
import type { Winner } from "../api/queries";
import { AXES, NUMBER_FIELDS } from "./style";

export function visibleRows(rows: CompareRow[], onlyDiffering: boolean): CompareRow[] {
  return onlyDiffering ? rows.filter((row) => row.differs) : rows;
}

/**
 * The verdict of a pass over the page: every cue the two translations disagree on goes to the variant when it is ticked
 * and to the current text when it is not, so that the scores of a style count what was left behind as well.
 */
export function verdicts(rows: CompareRow[], picked: ReadonlySet<number>): Record<string, Winner> {
  const out: Record<string, Winner> = {};
  for (const row of rows) if (row.differs) out[String(row.n)] = picked.has(row.n) ? "variant" : "current";
  return out;
}

/** `Selected 12 from variant, 330 kept from current`: only the cues that differ can come from the variant. */
export function tally(rows: CompareRow[], picked: ReadonlySet<number>): { fromVariant: number; kept: number } {
  const fromVariant = rows.filter((row) => row.differs && picked.has(row.n)).length;
  return { fromVariant, kept: rows.length - fromVariant };
}

/** What the judge said about one cue, in words (colour is only a help): `Judge prefers the variant: tighter wording`. */
export function judgeNote(verdict: JudgeVerdict): string {
  const side =
    verdict.winner === "variant" ? "prefers the variant" : verdict.winner === "current" ? "prefers the current text" : "sees no real difference";
  return verdict.why ? `Judge ${side}: ${verdict.why}` : `Judge ${side}`;
}

/** Where the judge stands for this candidate: working, done (with its tally) or not asked yet. */
export function judgeStatus(judge: JudgeSummary): string {
  if (judge.job) return judge.job.stage ? `The judge is working: ${judge.job.stage}.` : `The judge is ${judge.job.state === "running" ? "working" : "in line"}.`;
  if (judge.judged === 0) return "The judge has not looked at this candidate yet.";
  const cues = `${judge.judged.toLocaleString()} ${judge.judged === 1 ? "cue" : "cues"}`;
  return `The judge looked at ${cues}: ${judge.variant} prefer the variant, ${judge.current} the current text, ${judge.tie} tie.`;
}

/** The button under the verdicts: ask about the cues it has not seen, or, when it has seen all, ask again. */
export function judgeAsk(judge: JudgeSummary): { label: string; again: boolean } | null {
  if (judge.job) return null;
  if (judge.todo > 0) return { label: `Ask the judge (${judge.todo.toLocaleString()} ${judge.todo === 1 ? "cue" : "cues"})`, again: false };
  return judge.judged > 0 ? { label: "Ask the judge again", again: true } : null;
}

/** The lines of the confirmation: what the variant sets, by the names of the form (an axis it leaves alone is left out). */
export function styleLines(preset: string, style: Style | undefined, presetName?: string): Array<[string, string]> {
  const lines: Array<[string, string]> = [["Preset", presetName || preset || "—"]];
  for (const axis of AXES) {
    const value = style?.[axis.key];
    if (value === undefined) continue;
    const text = axis.key === "allow_condense" ? (value ? "Allow" : "Don't") : axis.options.find((option) => option.value === value)?.label;
    lines.push([axis.label, text ?? String(value)]);
  }
  if (style?.tone) lines.push(["Tone", style.tone]);
  for (const field of NUMBER_FIELDS) if (style?.[field.key] !== undefined) lines.push([field.label, String(style[field.key])]);
  if (style?.notes) lines.push(["Notes", style.notes]);
  return lines;
}
