import type { BackgroundTask, MemoryPolicy, MemoryUnit, SeriesMemory } from "../api/types";

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

/** What an `extract-facts` task reports per episode (`cached`, `extracted`, ...); nothing while it has not finished. */
export function taskResults(task: Pick<BackgroundTask, "result">): Record<string, string> {
  const results = task.result?.results;
  if (results === null || typeof results !== "object" || Array.isArray(results)) return {};
  return Object.fromEntries(Object.entries(results).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
}

/** "Reading S01E02 · 1 of 3": where a task is, in words a person can read at a glance (the worker words the first part). */
export function progressLine(task: Pick<BackgroundTask, "state" | "done" | "total" | "message">): string {
  const of = task.total > 0 ? ` · ${task.done} of ${task.total}` : "";
  switch (task.state) {
    case "queued":
      return "Waiting for the task before it";
    case "running":
      return task.total > 0 ? `${task.message || "Working"}${of}` : "Starting";
    case "done":
      return `Done${of}`;
    case "cancelled":
      return `Stopped${of}`;
    case "failed":
      return `Failed${of}`;
  }
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
