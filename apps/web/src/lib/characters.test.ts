import { describe, expect, it } from "vitest";
import {
  addressFor,
  aliasesOf,
  aliasList,
  cardOf,
  draftOf,
  entryOf,
  formatAddressWindow,
  nameProblems,
  people,
  plan,
  sameDraft,
  styleSummary,
  withEntry,
  withoutEntry,
} from "./characters";

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
    expect(cardOf({ ...draft, samples: "a\n\n b ", aliases: "Violet, Vi , violet," })).toEqual({
      name: "Vi",
      aliases: ["Violet"], // not twice, and not the character's own name
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

describe("characters, other names (P19b)", () => {
  const withAliases = {
    version: 1,
    characters: [
      { name: "Caroline Miller", target: "Caroline Miller", aliases: ["Carolyn Miller"] },
      { name: "Malcolm", target: "Malcolm" },
      { name: "Lois", target: "Lois", aliases: ["Mrs. Wilkerson"] },
    ],
    address_rules: [
      { a: "Malcolm", b: "Carolyn Miller", a_to_b: "em–cô", b_to_a: "cô–em" },
      { a: "Caroline Miller", b: "Malcolm", a_to_b: "cô–em", b_to_a: "em–cô", from: "S02E01" },
    ],
  };

  it("reads one list of other names from both halves, without repeats and without the character's own name", () => {
    const [caroline] = people(withAliases, [{ name: "Caroline Miller", aliases: ["Carolyn Miller", "Miss Miller", "caroline miller"] }]);
    expect(aliasesOf(caroline ?? null)).toEqual(["Carolyn Miller", "Miss Miller"]);
    expect(aliasesOf(null)).toEqual([]);
    expect(draftOf(caroline ?? null).aliases).toBe("Carolyn Miller, Miss Miller");
  });

  it("writes the same list to the Name, and to the Voice only when there is one", () => {
    const draft = { ...draftOf(null), name: "Caroline Miller", aliases: "Carolyn Miller, Carolyn Miller, Caroline Miller" };
    expect(aliasList(draft)).toEqual(["Carolyn Miller"]);
    expect(entryOf(draft)).toEqual({ name: "Caroline Miller", aliases: ["Carolyn Miller"] });
    // An alias is not a Voice: no card is made for it...
    expect(plan(draft, null)).toEqual({ entry: { name: "Caroline Miller", aliases: ["Carolyn Miller"] }, card: null });
    // ...but a character that has one keeps the two lists equal.
    const [existing] = people({ version: 1 }, [{ name: "Caroline Miller", speech: "warm" }]);
    expect(plan({ ...draftOf(existing ?? null), aliases: "Carolyn Miller" }, existing ?? null).card).toEqual({ name: "Caroline Miller", aliases: ["Carolyn Miller"], speech: "warm" });
  });

  it("refuses a form of address as a name or an alias, and a name that is another character's", () => {
    const all = people(withAliases, []);
    const draft = { ...draftOf(null), original: null as string | null };
    const refusal = `"Mom" is a relative form of address, not a character's name.`;
    expect(nameProblems({ ...draft, name: "Mom", aliases: "" }, all).name).toBe(refusal);
    expect(nameProblems({ ...draft, name: "Ida", aliases: "Mom" }, all).aliases).toBe(refusal);
    expect(nameProblems({ ...draft, name: "Ida", aliases: "Carolyn Miller" }, all).aliases).toBe('"Carolyn Miller" already belongs to Caroline Miller.');
    expect(nameProblems({ ...draft, name: "Mrs. Wilkerson", aliases: "" }, all).name).toBe('"Mrs. Wilkerson" is already an alias of Lois.');
    expect(nameProblems({ ...draft, name: "Ida", aliases: "Mrs. Ida" }, all)).toEqual({ name: null, aliases: null });
    // Editing a character keeps its own names legal.
    expect(nameProblems({ ...draft, original: "Caroline Miller", name: "Caroline Miller", aliases: "Carolyn Miller" }, all)).toEqual({ name: null, aliases: null });
  });

  it("reads the forms of address a character has under any of its names", () => {
    const forms = addressFor(withAliases, "Caroline Miller");
    expect(forms).toHaveLength(1);
    expect(forms[0]!.with).toBe("Malcolm");
    expect(forms[0]!.stages).toEqual([
      { says: "cô–em", hears: "em–cô" },
      { says: "cô–em", hears: "em–cô", from: "S02E01" },
    ]);
    expect(addressFor(withAliases, "Malcolm")[0]!.with).toBe("Caroline Miller"); // the partner is named as the character, not as the alias
  });
});
