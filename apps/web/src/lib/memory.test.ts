import { describe, expect, it } from "vitest";
import type { MemoryUnit } from "../api/types";
import { anchorProblem, extractTarget, fromChoice, memoryIsOn, missingFacts, resultLine, toChoice } from "./memory";

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

  it("counts the episodes that have a source and no facts, and reads them all from the last one", () => {
    const units = [unit(1, true, true), unit(2, true, false), unit(3, false, false), unit(4, true, false)];
    expect(missingFacts(units).map((item) => item.id)).toEqual([2, 4]);
    expect(extractTarget(units)?.id).toBe(4);
    expect(extractTarget([unit(1, true, true), unit(2, false, false)])).toBeNull();
  });

  it("says what an extraction did, episode by episode", () => {
    expect(resultLine({ S01E01: "extracted", S01E02: "failed", S01E03: "cached" })).toBe("S01E01 read · S01E02 failed · S01E03 already had facts");
  });
});
