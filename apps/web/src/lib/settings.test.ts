import { describe, expect, it } from "vitest";
import {
  activeModels,
  fieldProblem,
  formatBytes,
  keysOf,
  LAST_MODEL_FIELD,
  MODEL_FIELDS,
  modelPlaceholder,
  modelWarnings,
  pick,
  QUEUE_FIELDS,
  sameValue,
  STORY_FIELDS,
  toWire,
  TRANSLATION_FIELDS,
  type Field,
} from "./settings";

const field = (key: string): Field => {
  const found = [...TRANSLATION_FIELDS, ...QUEUE_FIELDS].find((candidate) => candidate.key === key);
  if (!found) throw new Error(key);
  return found;
};

describe("settings", () => {
  it("picks only the keys of a group", () => {
    expect(pick({ a: 1, b: 2, c: 3 }, ["a", "c", "z"])).toEqual({ a: 1, c: 3, z: undefined });
    expect(keysOf(QUEUE_FIELDS)).toContain("quota_reset_tz");
  });

  it("refuses a number the worker would clamp, a blank one and one that is not whole", () => {
    expect(fieldProblem(field("breaker_threshold"), 0)).toBe("At least 1");
    expect(fieldProblem(field("breaker_threshold"), "")).toBe("Enter a number");
    expect(fieldProblem(field("breaker_threshold"), 2.5)).toBe("Use a whole number");
    expect(fieldProblem(field("breaker_threshold"), 3)).toBeNull();
    expect(fieldProblem(field("gst_temperature"), "2.5")).toBe("At most 2");
    expect(fieldProblem(field("gst_temperature"), "0.7")).toBeNull();
    // The Default model must name a model (R19-9); the others may be left empty, and a model is not a number.
    expect(fieldProblem(field("gst_model"), "")).toBe("Choose a model");
    expect(fieldProblem(field("gst_model"), "  ")).toBe("Choose a model");
    expect(fieldProblem(field("gst_model"), "gemini-3.1-flash-lite")).toBeNull();
    expect(fieldProblem(field("memory_model"), "")).toBeNull();
  });

  it("sends numbers as numbers, except the two the worker keeps as text", () => {
    expect(toWire(field("breaker_threshold"), "4")).toBe(4);
    expect(toWire(field("gst_temperature"), 0.7)).toBe("0.7");
    expect(toWire(field("gst_model"), "  flash  ")).toBe("flash");
    expect(toWire(field("gst_no_thinking"), true)).toBe(true);
  });

  it("calls two spellings of one number the same value", () => {
    expect(sameValue(field("gst_temperature"), "0.70", 0.7)).toBe(true);
    expect(sameValue(field("gst_temperature"), "0.7", "0.8")).toBe(false);
    expect(sameValue(field("gst_model"), " a ", "a")).toBe(true);
    expect(sameValue(undefined, [{ a: 1 }], [{ a: 1 }])).toBe(true);
    expect(sameValue(undefined, [{ a: 1 }], [{ a: 2 }])).toBe(false);
  });

  it("writes a size in the unit a person reads", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(38.4 * 1024 * 1024)).toBe("38.4 MB");
    expect(formatBytes(250 * 1024 * 1024)).toBe("250 MB");
  });
});

describe("the models by task (FR-9.3.1)", () => {
  const main = { gst_model: "big", gst_fallback_model: "", memory_model: "", summarize_model: "", research_model: "", judge_model: "", judge_enabled: false };
  const row = (values: Record<string, unknown>, label: string) => activeModels(values).find((r) => r.label === label)!;

  it("keeps every box that chooses a model in the translation group, and none in the story group", () => {
    const keys = keysOf(TRANSLATION_FIELDS);
    for (const name of ["gst_model", "gst_fallback_model", "memory_model", "summarize_model", "research_model", "judge_model"]) expect(keys).toContain(name);
    expect(keysOf(STORY_FIELDS)).not.toContain("memory_model");
    expect(keysOf(STORY_FIELDS)).not.toContain("summarize_model");
    expect(keys.indexOf(LAST_MODEL_FIELD)).toBe(MODEL_FIELDS.length - 1); // the table goes under the last model box
  });

  it("says in an empty box what it runs on, and from which box", () => {
    expect(modelPlaceholder(field("memory_model"), main)).toBe("Same as Default (big)");
    expect(modelPlaceholder(field("summarize_model"), main)).toBe("Same as Default (big)");
    expect(modelPlaceholder(field("summarize_model"), { ...main, memory_model: "cheap" })).toBe("Same as Memory (cheap)");
    expect(modelPlaceholder(field("judge_model"), main)).toBe("Same as Default (big)");
    expect(modelPlaceholder(field("gst_fallback_model"), main)).toBe("(none)");
    expect(modelPlaceholder(field("research_model"), { ...main, gst_model: "" })).toBe("(same as Default)"); // no default to name
  });

  it("follows the worker's rule: an empty box takes the one above it", () => {
    expect(row(main, "Extract facts")).toMatchObject({ model: "big", note: "(uses Default model)" });
    expect(row({ ...main, memory_model: "cheap" }, "Extract facts")).toMatchObject({ model: "cheap", note: "" });
    expect(row({ ...main, memory_model: "cheap" }, "Condense story")).toMatchObject({ model: "cheap", note: "(uses Memory model)" });
    expect(row({ ...main, memory_model: "cheap", summarize_model: "tiny" }, "Condense story")).toMatchObject({ model: "tiny", note: "" });
    expect(row({ ...main, memory_model: "cheap" }, "Suggest from facts")).toMatchObject({ model: "cheap", note: "(uses Extract facts model)" });
    expect(row({ ...main, memory_model: "cheap" }, "Web research")).toMatchObject({ model: "big", note: "(uses Default model)" }); // research does not follow Memory
  });

  it("shows the strong model as off when there is none or it is the default model, and the judge as off until it is on", () => {
    expect(row(main, "Strong fallback").off).toBe("Off (single model)");
    expect(row({ ...main, gst_fallback_model: "big" }, "Strong fallback").model).toBe("");
    expect(row({ ...main, gst_fallback_model: "strong" }, "Strong fallback")).toMatchObject({ model: "strong", off: undefined });
    expect(row(main, "LLM judge").off).toBe("Off  (big when enabled)");
    expect(row({ ...main, judge_enabled: true }, "LLM judge").off).toBeUndefined();
  });

  it("warns, when the default model changes, about the tasks that will follow it", () => {
    const stored = { ...main, gst_model: "small" };
    expect(modelWarnings(stored, stored)).toEqual([]); // nothing changed
    expect(modelWarnings(stored, main)).toEqual([
      "Extract facts, Condense story, Suggest from facts and Web research have no model of their own and will also run on big.",
    ]);
    expect(modelWarnings(stored, { ...main, memory_model: "cheap" })).toEqual(["Web research has no model of its own and will also run on big."]);
    expect(modelWarnings(stored, { ...main, memory_model: "cheap", research_model: "web" })).toEqual([]);
    expect(modelWarnings(stored, { ...main, judge_enabled: true })).toEqual([
      "Extract facts, Condense story, Suggest from facts, Web research and LLM judge have no model of their own and will also run on big.",
    ]);
  });

  it("warns that a strong model equal to the default is dropped when saved", () => {
    expect(modelWarnings(main, { ...main, gst_fallback_model: "big" })).toEqual([
      "The Strong model is the same as the Default model. Saving clears it, and the translation runs on one model.",
    ]);
  });
});
