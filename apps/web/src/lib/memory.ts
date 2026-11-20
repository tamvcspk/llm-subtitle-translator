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

const LEFT_OUT: Record<string, string> = {
  "no-source": "no source subtitle",
  cached: "already has its facts",
};

/** "S01E01 already has its facts · S01E02 no source subtitle": the episodes a request did not make a job for, and why. */
export function skippedLine(skipped: Record<string, string>): string {
  return Object.entries(skipped)
    .map(([label, why]) => `${label} ${LEFT_OUT[why] ?? why}`)
    .join(" · ");
}

/** What the open `extract` jobs are doing, for the Context tab: "Reading S01E02 · 2 more in line". */
export function readingLine(jobs: Array<{ label?: string; running: boolean }>): string {
  if (jobs.length === 0) return "";
  const running = jobs.find((job) => job.running);
  const rest = jobs.length - (running ? 1 : 0);
  if (!running) return `${rest.toLocaleString()} ${rest === 1 ? "episode" : "episodes"} in line to be read`;
  const head = `Reading ${running.label || "an episode"}`;
  return rest === 0 ? head : `${head} · ${rest.toLocaleString()} more in line`;
}
