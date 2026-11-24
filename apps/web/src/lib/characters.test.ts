import { describe, expect, it } from "vitest";
import { addressFor, cardOf, draftOf, formatAddressWindow, people, plan, sameDraft, styleSummary, withEntry, withoutEntry } from "./characters";

const doc = {
  version: 1,
  characters: [{ name: "Vi", target: "Vi", gender: "f", description: "Brawler" }],
  address_rules: [{ a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị" }],
};

describe("characters", () => {
  it("puts the Name of the glossary and the Voice of a card together, whatever the case, and keeps a card with no name entry", () => {
    const list = people(doc, [{ name: "vi", speech: "blunt" }, { name: "Jinx", speech: "wild" }]);
    expect(list.map((person) => [person.name, person.entry !== null, person.card !== null])).toEqual([
      ["Vi", true, true],
      ["Jinx", false, true],
    ]);
  });

  it("starts a draft from both halves and turns it back into a card", () => {
    const [vi] = people(doc, [{ name: "Vi", aliases: ["Violet"], catchphrases: ["Cupcake"], samples: ["a", "b"], style: { profanity: "keep" }, always: true }]);
    const draft = draftOf(vi ?? null);
    expect(draft).toMatchObject({ name: "Vi", target: "Vi", aliases: "Violet", catchphrases: "Cupcake", samples: "a\nb", always: true });
    expect(cardOf({ ...draft, samples: "a\n\n b ", aliases: "Violet, Vi ," })).toEqual({
      name: "Vi",
      aliases: ["Violet", "Vi"],
      catchphrases: ["Cupcake"],
      samples: ["a", "b"],
      style: { profanity: "keep" },
      always: true,
    });
  });

  it("writes only the halves that exist or were filled in", () => {
    const bare = draftOf(null);
    expect(plan({ ...bare, name: "Caitlyn" }, null)).toEqual({ entry: { name: "Caitlyn" }, card: null });
    expect(plan({ ...bare, name: "Caitlyn", speech: "calm" }, null).card).toEqual({ name: "Caitlyn", speech: "calm" });

    const [jinx] = people({ version: 1 }, [{ name: "Jinx", speech: "wild" }]);
    // A voice without a name entry stays that way until the admin fills the Name in.
    expect(plan(draftOf(jinx ?? null), jinx ?? null).entry).toBeNull();
    expect(plan({ ...draftOf(jinx ?? null), target: "Jinx" }, jinx ?? null).entry).toEqual({ name: "Jinx", target: "Jinx" });
  });

  it("replaces an entry when the name changes, adds a new one, and removes one", () => {
    const renamed = withEntry(doc, { name: "Violet", target: "Vi" }, "Vi");
    expect(renamed.characters).toEqual([{ name: "Violet", target: "Vi" }]);
    expect(withEntry(doc, { name: "Jinx" }, null).characters).toHaveLength(2);
    expect(withEntry(doc, null, "Vi")).toBe(doc);
    expect(withoutEntry(doc, "vi").characters).toEqual([]);
  });

  it("reads forms of address from either side of a rule, grouped per partner in story order", () => {
    expect(addressFor(doc, "Vi")).toEqual([
      { with: "Powder", says: "chị–em", hears: "em–chị", stages: [{ says: "chị–em", hears: "em–chị" }] },
    ]);
    expect(addressFor(doc, "Powder")).toEqual([
      { with: "Vi", says: "em–chị", hears: "chị–em", stages: [{ says: "em–chị", hears: "chị–em" }] },
    ]);
    expect(addressFor(doc, "Caitlyn")).toEqual([]);

    const stagedDoc = {
      version: 1,
      address_rules: [
        { a: "Powder", b: "Vi", a_to_b: "tôi–cô", b_to_a: "tôi–em", from: "S02E01" },
        { a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị", until: "S02E01" },
      ],
    };
    expect(addressFor(stagedDoc, "Vi")).toEqual([
      {
        with: "Powder",
        says: "chị–em",
        hears: "em–chị",
        stages: [
          { says: "chị–em", hears: "em–chị", until: "S02E01" },
          { says: "tôi–em", hears: "tôi–cô", from: "S02E01" },
        ],
      },
    ]);
    expect(formatAddressWindow({ from: "S01E01", until: "S02E01" })).toBe("S01E01–S02E01");
    expect(formatAddressWindow({ from: "S02E01" })).toBe("from S02E01");
    expect(formatAddressWindow({ until: "S02E01" })).toBe("until S02E01");
    expect(formatAddressWindow({})).toBe("");
  });

  it("summarises the style of a card in the words of the form", () => {
    expect(styleSummary({ profanity: "keep", tone: "blunt" })).toBe("Swearing = Keep · Tone = blunt");
    expect(styleSummary({})).toBe("");
  });

  it("treats a name written with other case or spaces around it as the same draft", () => {
    const a = draftOf(null);
    expect(sameDraft({ ...a, name: " Vi " }, { ...a, name: "Vi" })).toBe(true);
    expect(sameDraft({ ...a, speech: "x" }, a)).toBe(false);
  });
});
