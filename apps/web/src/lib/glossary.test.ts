import { describe, expect, it } from "vitest";
import { cleanDoc, glossaryImpact, patchAt, positionProblem, removeAt, sameDoc } from "./glossary";

describe("glossary document", () => {
  it("leaves out blank rows, trims text and keeps what the form does not edit", () => {
    const doc = cleanDoc({
      version: 1,
      target_lang: "vi",
      terms: [
        { source: "  Enforcer ", target: "Cảnh vệ", aliases: ["Enforcers"], locked: true, note: " " },
        { source: "", target: "" },
      ],
      address_rules: [{ a: "", b: "" }],
      characters: [{ name: "" }],
    });
    expect(doc).toEqual({
      version: 1,
      target_lang: "vi",
      terms: [{ source: "Enforcer", target: "Cảnh vệ", aliases: ["Enforcers"], locked: true }],
    });
  });

  it("does not count an added row nobody filled in as a change, and does count a filled one", () => {
    const stored = { version: 1, terms: [{ source: "A", target: "B" }] };
    expect(sameDoc({ ...stored, terms: [...stored.terms, { source: "", target: "" }] }, stored)).toBe(true);
    expect(sameDoc({ ...stored, terms: [{ source: "A", target: "C" }] }, stored)).toBe(false);
    expect(sameDoc({ ...stored, terms: [{ source: "A", target: "B", locked: false }] }, stored)).toBe(true);
  });

  it("changes one row without touching the others", () => {
    expect(patchAt([{ n: 1 }, { n: 2 }], 1, { n: 5 })).toEqual([{ n: 1 }, { n: 5 }]);
    expect(removeAt(["a", "b", "c"], 1)).toEqual(["a", "c"]);
  });

  it("says what a save flags, in the right number", () => {
    expect(glossaryImpact(undefined, "Saving", "episode")).toBeNull();
    expect(glossaryImpact({ would_stale: 0 }, "Saving", "episode")).toBe("No translation is affected.");
    expect(glossaryImpact({ would_stale: 1 }, "Importing", "movie")).toBe("Importing flags 1 movie stale.");
    expect(glossaryImpact({ would_stale: 12 }, "Saving", "episode")).toBe("Saving flags 12 episodes stale.");
  });

  it("accepts an episode or an episode with a time, and nothing else", () => {
    for (const ok of ["", "S01E06", "s1e6", "S01E06 00:32:10", "S01E06 0:32:10.5"]) expect(positionProblem(ok)).toBeNull();
    for (const bad of ["1x06", "S01", "S01E06 32:10"]) expect(positionProblem(bad)).not.toBeNull();
  });
});
