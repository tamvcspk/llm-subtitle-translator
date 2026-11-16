import type { RetranslateBody } from "../api/queries";
import { isPlayerTime } from "./unit";

export type RangeScope = "episode" | "cues" | "time";

export interface RangeFields {
  from: string;
  to: string;
}

/** The range of the form as the API takes it, or what is wrong with it. Cue numbers are checked against the cue count. */
export function buildRange(
  scope: RangeScope,
  fields: RangeFields,
  total: number | null,
): { range: RetranslateBody["range"] } | { problem: string } {
  if (scope === "episode") return { range: undefined };
  if (scope === "cues") {
    const first = Number(fields.from);
    const last = Number(fields.to);
    if (!Number.isInteger(first) || !Number.isInteger(last) || fields.from.trim() === "" || fields.to.trim() === "") {
      return { problem: "Give the first and last cue as whole numbers." };
    }
    if (first < 1 || last < first) return { problem: "The first cue must be 1 or more, and not after the last." };
    if (total !== null && last > total) return { problem: `This subtitle has ${total} cues.` };
    return { range: { cues: [first, last] } };
  }
  if (!isPlayerTime(fields.from) || !isPlayerTime(fields.to)) return { problem: "Give both times as 14:20 or 00:14:20." };
  return { range: { time: [fields.from.trim(), fields.to.trim()] } };
}

/** `Will overwrite 336 cues and keep 4 locked.` for a whole episode or a cue range; a time range is only known to the worker. */
export function overwriteLine(scope: RangeScope, range: RetranslateBody["range"], total: number | null, locked: number[]): string | null {
  if (total === null) return null;
  if (scope === "time") return "The cues inside the time range are worked out when you ask for the price.";
  const [first, last] = range && "cues" in range ? range.cues : [1, total];
  const inside = locked.filter((cue) => cue >= first && cue <= last).length;
  const size = Math.max(0, Math.min(last, total) - first + 1);
  const keep = inside === 0 ? "" : ` and keep ${inside} locked`;
  return `Will overwrite ${Math.max(0, size - inside)} ${size - inside === 1 ? "cue" : "cues"}${keep}.`;
}
