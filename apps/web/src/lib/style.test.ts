import { describe, expect, it } from "vitest";
import { AXES, INHERIT, axisValue, cleanStyle, impactSentence, inheritLabel, sameDraft, setAxis } from "./style";

const axis = (key: string) => AXES.find((candidate) => candidate.key === key)!;

describe("style axes", () => {
  it("start at Inherit and say what that means right now", () => {
    expect(axisValue(axis("formality"), {})).toBe(INHERIT);
    expect(inheritLabel(axis("formality"), { formality: "neutral" })).toBe("Inherit · now Neutral");
    expect(inheritLabel(axis("formality"), {})).toBe("Inherit");
    expect(inheritLabel(axis("allow_condense"), { allow_condense: true })).toBe("Inherit · now Allow");
  });

  it("set a value, and Inherit takes it away again", () => {
    const set = setAxis({}, axis("profanity"), "soften");
    expect(set).toEqual({ profanity: "soften" });
    expect(setAxis(set, axis("profanity"), INHERIT)).toEqual({});
    expect(setAxis({}, axis("allow_condense"), "dont")).toEqual({ allow_condense: false });
    expect(axisValue(axis("allow_condense"), { allow_condense: false })).toBe("dont");
  });

  it("keep the four main choices in front", () => {
    expect(AXES.filter((a) => !a.more).map((a) => a.label)).toEqual(["Formality", "Swearing", "Idioms and wordplay"]);
  });
});

describe("drafts", () => {
  it("are the same when what they would store is the same", () => {
    expect(cleanStyle({ tone: "  ", notes: " keep it formal " })).toEqual({ notes: "keep it formal" });
    expect(sameDraft({ preset: "", style: { tone: "" } }, { preset: "", style: {} })).toBe(true);
    expect(sameDraft({ preset: "sitcom", style: {} }, { preset: "", style: {} })).toBe(false);
    expect(sameDraft({ preset: "", style: { max_cps: 18, tone: "x" } }, { preset: "", style: { tone: "x", max_cps: 18 } })).toBe(true);
  });
});

describe("impact sentence", () => {
  it("says how many, or that nothing is affected, or nothing while it is not known", () => {
    expect(impactSentence(undefined)).toBeNull();
    expect(impactSentence({ would_stale: 0 })).toBe("No translation is affected.");
    expect(impactSentence({ would_stale: 1 })).toBe("Saving flags 1 episode stale.");
    expect(impactSentence({ would_stale: 12 })).toBe("Saving flags 12 episodes stale.");
    expect(impactSentence({ would_stale: 1 }, "movie")).toBe("Saving flags 1 movie stale.");
  });
});
