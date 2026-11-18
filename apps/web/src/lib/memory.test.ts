import { describe, expect, it } from "vitest";
import type { MemoryUnit } from "../api/types";
import { anchorProblem, fromChoice, memoryIsOn, missingFacts, progressLine, resultLine, taskResults, toChoice } from "./memory";

const unit = (id: number, has_source: boolean, has_facts: boolean): MemoryUnit => ({
  id,
  label: `S01E0${id}`,
  season: 1,
  episode: id,
  has_source,
  has_facts,
  facts_hash: "",
});

describe("story memory", () => {
  it("shows the API's empty policy as Default and back", () => {
    expect(toChoice("")).toBe("default");
    expect(toChoice("on")).toBe("on");
    expect(fromChoice("default")).toBe("");
    expect(fromChoice("off")).toBe("off");
  });

  it("is on by its own switch, or by the global one when it has none", () => {
    expect(memoryIsOn({ policy: "", global_enabled: true })).toBe(true);
    expect(memoryIsOn({ policy: "", global_enabled: false })).toBe(false);
    expect(memoryIsOn({ policy: "on", global_enabled: false })).toBe(true);
    expect(memoryIsOn({ policy: "off", global_enabled: true })).toBe(false);
  });

  it("takes an episode code or nothing for Specials after", () => {
    for (const ok of ["", "  ", "S02E05", "s1e9"]) expect(anchorProblem(ok)).toBeNull();
    for (const bad of ["2x05", "S02", "S02E05 00:10:00"]) expect(anchorProblem(bad)).not.toBeNull();
  });

  it("counts the episodes that have a source and no facts: the ones that can be read", () => {
    const units = [unit(1, true, true), unit(2, true, false), unit(3, false, false), unit(4, true, false)];
    expect(missingFacts(units).map((item) => item.id)).toEqual([2, 4]);
    expect(missingFacts([unit(1, true, true), unit(2, false, false)])).toEqual([]);
  });

  it("says what an extraction did, episode by episode", () => {
    expect(resultLine({ S01E01: "extracted", S01E02: "failed", S01E03: "cached" })).toBe("S01E01 read · S01E02 failed · S01E03 already had facts");
  });

  it("reads the results of a finished task, and nothing from one that has none", () => {
    const task = (result: Record<string, unknown> | null) => ({ result }) as Parameters<typeof taskResults>[0];
    expect(taskResults(task({ extracted: ["S01E01"], results: { S01E01: "extracted" } }))).toEqual({ S01E01: "extracted" });
    expect(taskResults(task(null))).toEqual({});
    expect(taskResults(task({ results: "junk" }))).toEqual({});
  });

  it("words how far a task is", () => {
    const task = (state: string, done: number, total: number, message = "") => ({ state, done, total, message }) as Parameters<typeof progressLine>[0];
    expect(progressLine(task("queued", 0, 0))).toBe("Waiting for the task before it");
    expect(progressLine(task("running", 1, 3, "Reading S01E02"))).toBe("Reading S01E02 · 1 of 3");
    expect(progressLine(task("running", 0, 0))).toBe("Starting");
    expect(progressLine(task("done", 3, 3))).toBe("Done · 3 of 3");
    expect(progressLine(task("cancelled", 1, 3))).toBe("Stopped · 1 of 3");
    expect(progressLine(task("failed", 1, 3))).toBe("Failed · 1 of 3");
  });
});
