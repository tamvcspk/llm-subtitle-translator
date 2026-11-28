import { describe, expect, it } from "vitest";
import type { Suggestion } from "../api/types";
import { describe as describeSuggestion, draftOf, draftProblem, evidenceLines, groupBySeries, impactLine, payloadOf, safeUrl, sourceName } from "./suggestions";

type Draftable = Pick<Suggestion, "kind" | "payload">;

const term: Draftable = { kind: "term", payload: { source: "Kyber", target: "Đá", aliases: ["kyber crystal"], from: "S01E02", note: "x" } };

describe("describe", () => {
  it("says a term, a form of address and a character in a line, with the rest as details", () => {
    expect(describeSuggestion(term)).toEqual({
      title: "Kyber → Đá",
      details: ["Also: kyber crystal", "From S01E02", "x"],
    });
    expect(describeSuggestion({ kind: "address_rule", payload: { a: "Vi", b: "Jinx", a_to_b: "chị" } })).toEqual({
      title: "Vi ↔ Jinx",
      details: ["Vi calls Jinx “chị”"],
    });
    expect(
      describeSuggestion({
        kind: "address_rule",
        payload: {
          a: "Hal",
          b: "Lois",
          a_to_b: "anh–em",
          b_to_a: "em–anh",
          change_from: "S03E05",
          stages: [
            { a_to_b: "tôi–cô", b_to_a: "tôi–anh", until: "S03E05" },
            { a_to_b: "anh–em", b_to_a: "em–anh", from: "S03E05" },
          ],
          note: "married",
        } as Suggestion["payload"],
      }),
    ).toEqual({
      title: "Hal ↔ Lois · change from S03E05?",
      details: [
        "Until S03E05: Hal calls Lois “tôi–cô” · Lois calls Hal “tôi–anh”",
        "From S03E05: Hal calls Lois “anh–em” · Lois calls Hal “em–anh”",
        "married",
      ],
    });
    expect(describeSuggestion({ kind: "character", payload: { name: "Vi", target: "Vi", gender: "f", card: { name: "Vi", speech: "blunt" } } })).toEqual({
      title: "Vi",
      details: ["Gender: f", "Voice: blunt"],
    });
    // The other names of a character are said on the card, so accepting it is no surprise (P19b).
    expect(describeSuggestion({ kind: "character", payload: { name: "Caroline Miller", aliases: ["Carolyn Miller"] } })).toEqual({
      title: "Caroline Miller",
      details: ["Also: Carolyn Miller"],
    });
  });
});

describe("evidence", () => {
  it("puts where a cue is in front of what it says, and takes plain text as it is", () => {
    expect(evidenceLines(["said so", { episode: "S01E05", cue: 5, timecode: "00:00:08", text: "Hi", unit_id: 9 }])).toEqual([
      { where: "", text: "said so" },
      { where: "S01E05 · cue 5 · 00:00:08", text: "Hi", unitId: 9, url: undefined },
    ]);
  });

  it("links only web addresses", () => {
    expect(safeUrl("https://wiki.example/a")).toBe("https://wiki.example/a");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("not a url")).toBeNull();
  });

  it("names a research source by its site, not by its address", () => {
    expect(sourceName("https://www.wikipedia.org/wiki/Fern")).toBe("wikipedia.org");
    expect(sourceName("https://vi.wikipedia.org/wiki/Frieren")).toBe("vi.wikipedia.org");
    expect(sourceName("http://fandom.com/")).toBe("fandom.com");
    expect(sourceName("not an address")).toBe("not an address");
  });
});

describe("editing", () => {
  it("lays the edit on the proposal and sends back what the sheet has no box for", () => {
    const draft = { ...draftOf(term), b: " Đá Kyber ", locked: true, note: "" };
    expect(payloadOf(term, draft)).toEqual({ source: "Kyber", target: "Đá Kyber", aliases: ["kyber crystal"], from: "S01E02", locked: true });
  });

  it("keeps the rest of a card when only the voice changes, and drops the voice when it is emptied", () => {
    const character: Draftable = { kind: "character", payload: { name: "Vi", card: { name: "Vi", speech: "blunt", catchphrases: ["hey"] } } };
    expect(payloadOf(character, { ...draftOf(character), speech: "soft" })).toEqual({
      name: "Vi",
      card: { name: "Vi", speech: "soft", catchphrases: ["hey"] },
    });
    expect(payloadOf(character, { ...draftOf(character), speech: "" })).toEqual({ name: "Vi", card: { name: "Vi", catchphrases: ["hey"] } });
  });

  it("syncs stage 0 when editing a multi-stage address_rule proposal", () => {
    const multi: Draftable = {
      kind: "address_rule",
      payload: {
        a: "A",
        b: "B",
        a_to_b: "tôi–cô",
        b_to_a: "tôi–anh",
        stages: [
          { a_to_b: "tôi–cô", b_to_a: "tôi–anh", until: "S02E01" },
          { a_to_b: "anh–em", b_to_a: "em–anh", from: "S02E01" },
        ],
      } as Suggestion["payload"],
    };
    const edited = payloadOf(multi, { ...draftOf(multi), c: "mình–cậu" }) as Record<string, unknown>;
    expect(edited.a_to_b).toBe("mình–cậu");
    expect(edited.stages).toEqual([
      { a_to_b: "mình–cậu", b_to_a: "tôi–anh", until: "S02E01" },
      { a_to_b: "anh–em", b_to_a: "em–anh", from: "S02E01" },
    ]);
  });

  it("says what is missing before the worker would refuse it", () => {
    expect(draftProblem("term", { ...draftOf(term), b: " " })).toMatch(/both the source/);
    expect(draftProblem("address_rule", { ...draftOf({ kind: "address_rule", payload: { a: "A", b: "B" } }) })).toMatch(/how at least one/);
    expect(draftProblem("character", { ...draftOf({ kind: "character", payload: { name: "" } }) })).toMatch(/needs a name/);
    expect(draftProblem("term", draftOf(term))).toBeNull();
  });

  it("refuses a form of address, or another character's alias, as a character's name (R19-2)", () => {
    const named = (name: string) => draftOf({ kind: "character", payload: { name } });
    const known = [
      { name: "Lois", entry: { name: "Lois", aliases: ["Mrs. Wilkerson"] }, card: null },
      { name: "Malcolm", entry: { name: "Malcolm" }, card: null },
    ];
    expect(draftProblem("character", named("Mom"))).toBe('"Mom" is a relative form of address, not a character\'s name.');
    expect(draftProblem("character", named("Mrs. Wilkerson"), known)).toBe('"Mrs. Wilkerson" is already an alias of Lois.');
    // A name the series already has is the same character, not a clash: accepting updates it.
    expect(draftProblem("character", named("Malcolm"), known)).toBeNull();
    expect(draftProblem("character", named("Reese"), known)).toBeNull();
  });
});

describe("the lines", () => {
  it("says the impact in plain words", () => {
    expect(impactLine(undefined)).toBeNull();
    expect(impactLine(0)).toBe("No translation is affected.");
    expect(impactLine(1)).toBe("Accepting flags 1 translation stale. Nothing is redone.");
    expect(impactLine(2000)).toBe("Accepting flags 2,000 translations stale. Nothing is redone.");
  });

  it("groups by series in the order they appear", () => {
    const item = (id: number, series_id: number) => ({ id, series_id, series: `S${series_id}` }) as Suggestion;
    expect(groupBySeries([item(1, 7), item(2, 9), item(3, 7)]).map((g) => [g.seriesId, g.items.map((i) => i.id)])).toEqual([
      [7, [1, 3]],
      [9, [2]],
    ]);
  });
});
