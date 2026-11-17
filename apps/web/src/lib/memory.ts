import type { MemoryPolicy, MemoryUnit, SeriesMemory } from "../api/types";

export const POLICY_CHOICES: Array<{ value: "default" | "on" | "off"; label: string }> = [
  { value: "default", label: "Default" },
  { value: "on", label: "On" },
  { value: "off", label: "Off" },
];

/** The segmented control has no empty value: the API's `""` (follow the global setting) is shown as Default. */
export const toChoice = (policy: MemoryPolicy): "default" | "on" | "off" => (policy === "" ? "default" : policy);
export const fromChoice = (choice: string): MemoryPolicy => (choice === "on" || choice === "off" ? choice : "");

/** Whether story memory is in force for this series: its own switch, or the global one when it has none. */
export function memoryIsOn(memory: Pick<SeriesMemory, "policy" | "global_enabled">): boolean {
  return memory.policy === "on" || (memory.policy === "" && memory.global_enabled);
}

/** `S02E05`, or nothing (after the last episode of the first season): what the worker accepts. */
export function anchorProblem(value: string): string | null {
  const text = value.trim();
  return text === "" || /^S\d+E\d+$/i.test(text) ? null : "Use a form like S02E05";
}

/** Episodes that have a source subtitle and no facts yet: what Extract facts would read. */
export function missingFacts(units: MemoryUnit[]): MemoryUnit[] {
  return units.filter((unit) => unit.has_source && !unit.has_facts);
}

/**
 * One request reads the facts of an episode and of every earlier one that lacks them, so the last episode in story order
 * that still lacks facts covers all the missing ones. `null` when there is nothing to read.
 */
export function extractTarget(units: MemoryUnit[]): MemoryUnit | null {
  return missingFacts(units).at(-1) ?? null;
}

const RESULT_TEXT: Record<string, string> = {
  extracted: "read",
  stored: "read",
  cached: "already had facts",
  "no-source": "no source subtitle",
  failed: "failed",
};

/** "S01E01 read · S01E02 failed": what an extraction did, episode by episode. */
export function resultLine(results: Record<string, string>): string {
  return Object.entries(results)
    .map(([label, result]) => `${label} ${RESULT_TEXT[result] ?? result}`)
    .join(" · ");
}
