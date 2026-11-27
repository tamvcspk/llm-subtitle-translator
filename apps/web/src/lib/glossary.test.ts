import { describe, expect, it } from "vitest";
import {
  cleanDoc,
  canonicalNames,
  dedupeAddressRules,
  filterAddressGroups,
  filterTerms,
  glossaryImpact,
  groupAddressRules,
  patchAt,
  positionProblem,
  removeAt,
  sameDoc,
} from "./glossary";

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

  it("keeps the aliases of a character the form does not edit, and still drops a blank character", () => {
    const doc = cleanDoc({
      characters: [{ name: " Caroline Miller ", aliases: ["Carolyn Miller"] }, { name: "", aliases: [] }, { name: "" }],
    });
    expect(doc).toEqual({ characters: [{ name: "Caroline Miller", aliases: ["Carolyn Miller"] }] });
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

  it("groups address rules by unordered pair sorted by from position", () => {
    const groups = groupAddressRules([
      { a: "Lois", b: "Hal", a_to_b: "em–anh", b_to_a: "anh–em", from: "S02E01" },
      { a: "Hal", b: "Lois", a_to_b: "tôi–cô", b_to_a: "tôi–anh" },
      { a: "Malcolm", b: "Reese", a_to_b: "em–anh", b_to_a: "anh–em" },
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0]!.label).toBe("Lois ↔ Hal");
    expect(groups[0]!.items.map((item) => item.rule.from ?? "")).toEqual(["", "S02E01"]);
    expect(groups[1]!.label).toBe("Malcolm ↔ Reese");
  });

  it("dedupes identical forms across reversed pairs and adjacent stages", () => {
    const res = dedupeAddressRules([
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh" },
      { a: "Lois", b: "Hal", a_to_b: "em–anh", b_to_a: "anh–em", from: "S01E03" },
      { a: "Hal", b: "Lois", a_to_b: "tôi–cô", b_to_a: "tôi–anh", from: "S03E01" },
    ]);
    expect(res.removed).toBe(1);
    expect(res.rules).toHaveLength(2);
    expect(res.rules[0]!.from).toBeUndefined();
    expect(res.rules[1]!.from).toBe("S03E01");

    // R18-7: merging identical rules starting in season 2 preserves the first rule's `from` (does not widen to S01).
    const lateStart = dedupeAddressRules([
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S02E01" },
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S02E05" },
    ]);
    expect(lateStart.removed).toBe(1);
    expect(lateStart.rules).toEqual([{ a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S02E01" }]);

    // R18-7: rules separated by a gap (until S01E04 vs from S01E07) are NOT merged across the gap.
    const gapped = dedupeAddressRules([
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", until: "S01E04" },
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S01E07" },
    ]);
    expect(gapped.removed).toBe(0);
    expect(gapped.rules).toHaveLength(2);

    // Contiguous bounded windows (`until === next.from`) merge cleanly and keep both outer bounds.
    const contiguous = dedupeAddressRules([
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S01E01", until: "S01E04" },
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S01E04", until: "S01E08" },
    ]);
    expect(contiguous.removed).toBe(1);
    expect(contiguous.rules).toEqual([
      { a: "Hal", b: "Lois", a_to_b: "anh–em", b_to_a: "em–anh", from: "S01E01", until: "S01E08" },
    ]);
  });

  it("filters terms and address groups by keyword", () => {
    const terms = filterTerms(
      [
        { source: "Enforcer", target: "Cảnh vệ", note: "Piltover" },
        { source: "Hextech", target: "Hextech" },
      ],
      "piltover",
    );
    expect(terms.map((item) => item.index)).toEqual([0]);

    const groups = groupAddressRules([
      { a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị" },
      { a: "Jayce", b: "Viktor", a_to_b: "tôi–cậu", b_to_a: "tôi–cậu" },
    ]);
    expect(filterAddressGroups(groups, "powder").map((g) => g.label)).toEqual(["Vi ↔ Powder"]);
    expect(filterAddressGroups(groups, "tôi–cậu").map((g) => g.label)).toEqual(["Jayce ↔ Viktor"]);
  });
});

describe("forms of address and other names (P19b)", () => {
  const people = [{ name: "Caroline Miller", aliases: ["Carolyn Miller"] }, { name: "Malcolm" }];
  const forms = { a_to_b: "em–cô", b_to_a: "cô–em" };

  it("groups a rule written with an alias under the character's own name and says so", () => {
    const groups = groupAddressRules(
      [
        { a: "Malcolm", b: "Caroline Miller", ...forms },
        { a: "Malcolm", b: "Carolyn Miller", ...forms, from: "S02E01" },
      ],
      people,
    );
    expect(groups).toHaveLength(1);
    expect(groups[0]!.label).toBe("Malcolm ↔ Caroline Miller");
    expect(groups[0]!.items.map((item) => item.via)).toEqual([[], ["Carolyn Miller"]]);
    // Without the characters, they are two people, as before.
    expect(groupAddressRules([{ a: "Malcolm", b: "Caroline Miller" }, { a: "Malcolm", b: "Carolyn Miller" }])).toHaveLength(2);
  });

  it("merges a rule written with an alias into the character's rule, and the survivor says the character's name", () => {
    const res = dedupeAddressRules(
      [
        { a: "Malcolm", b: "Carolyn Miller", ...forms },
        { a: "Caroline Miller", b: "Malcolm", a_to_b: "cô–em", b_to_a: "em–cô", from: "S02E01" },
      ],
      people,
    );
    expect(res.removed).toBe(1);
    expect(res.rules).toEqual([{ a: "Malcolm", b: "Caroline Miller", ...forms }]);
  });

  it("leaves a rule that disagrees about the same window, and says the stages conflict", () => {
    const rules = [
      { a: "Malcolm", b: "Caroline Miller", ...forms },
      { a: "Malcolm", b: "Carolyn Miller", a_to_b: "tôi–cô", b_to_a: "tôi–em" },
    ];
    const res = dedupeAddressRules(rules, people);
    expect(res.removed).toBe(0);
    expect(res.rules).toHaveLength(2);
    expect(groupAddressRules(rules, people)[0]!.conflicting).toBe(true);
    // Different windows are stages, not a conflict.
    expect(groupAddressRules([{ a: "Malcolm", b: "Caroline Miller", ...forms }, { a: "Malcolm", b: "Caroline Miller", a_to_b: "x", b_to_a: "y", from: "S02E01" }], people)[0]!.conflicting).toBe(false);
  });

  it("does not take a name that is a character as an alias of another", () => {
    const canon = canonicalNames([{ name: "Lois", aliases: ["Hal"] }, { name: "Hal" }]);
    expect(canon("hal")).toBe("Hal");
    expect(canon("Ida")).toBe("Ida");
  });
});
