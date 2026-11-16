import type { CompareRow, Style } from "../api/types";
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
