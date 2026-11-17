import { describe, expect, it } from "vitest";
import { fieldProblem, formatBytes, keysOf, pick, QUEUE_FIELDS, sameValue, toWire, TRANSLATION_FIELDS, type Field } from "./settings";

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
    expect(fieldProblem(field("gst_model"), "")).toBeNull(); // a model is not a number
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
