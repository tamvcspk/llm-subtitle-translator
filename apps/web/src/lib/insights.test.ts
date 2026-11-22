import { describe, expect, it } from "vitest";
import type { FromEditsAnswer } from "../api/types";
import { abLabel, against, barWidth, dailyLimit, duration, fromEditsLine, millis, outcomeLabel, percent } from "./insights";

describe("insights formatting", () => {
  it("writes a rate as a percentage and no data as a dash", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0)).toBe("0 %");
    expect(percent(0.012)).toBe("1.2 %");
    expect(percent(0.4)).toBe("40 %");
  });

  it("writes durations in the unit a person reads", () => {
    expect(duration(null)).toBe("—");
    expect(duration(45)).toBe("45 s");
    expect(duration(300)).toBe("5 min");
    expect(duration(7500)).toBe("2 h 5 min");
    expect(duration(7200)).toBe("2 h");
    expect(millis(840)).toBe("840 ms");
    expect(millis(2400)).toBe("2.4 s");
  });

  it("says what was used against the limit, and that there is none when it is 0", () => {
    expect(against(12, 50, "calls today")).toBe("12 of 50 calls today");
    expect(against(12, 0, "calls today")).toBe("12 calls today, no limit");
  });

  it("takes the daily limit that bites first", () => {
    expect(dailyLimit({ rpd: 0, max_calls_per_day: 0 })).toBe(0);
    expect(dailyLimit({ rpd: 250, max_calls_per_day: 0 })).toBe(250);
    expect(dailyLimit({ rpd: 250, max_calls_per_day: 100 })).toBe(100);
  });

  it("names outcomes and styles, and keeps a name it does not know", () => {
    expect(outcomeLabel("rate_limited")).toBe("Too many requests (429)");
    expect(outcomeLabel("something_new")).toBe("something_new");
    expect(abLabel({ style: "casual", prompt_version: "p3" })).toBe("casual · prompt p3");
    expect(abLabel({ style: "", prompt_version: "" })).toBe("(no name)");
  });

  it("scales a day's bar against the busiest day, and does not divide by zero", () => {
    const days = [{ calls: 10 }, { calls: 5 }, { calls: 0 }];
    expect(days.map((d) => barWidth(d, days))).toEqual([100, 50, 0]);
    expect(barWidth({ calls: 0 }, [{ calls: 0 }])).toBe(0);
  });

  describe("what looking at the corrections found", () => {
    const answer = (over: Partial<FromEditsAnswer>): FromEditsAnswer => ({
      cues: 12, without_before: 0, patterns: 0, proposed: 0, unnamed: 0, report: null, ...over,
    });

    it("says when nothing was corrected, and when only old corrections exist", () => {
      expect(fromEditsLine(answer({ cues: 0 }))).toBe("No cue of this series has been corrected yet.");
      expect(fromEditsLine(answer({ cues: 0, without_before: 4 }))).toMatch(/older than this feature/);
    });

    it("says no pattern yet in plain words", () => {
      expect(fromEditsLine(answer({}))).toBe("Looked at 12 corrected cues. No word was changed the same way in three cues yet.");
    });

    it("counts what is new in Review", () => {
      const report = { added: 1, pending_already: 0, accepted_before: 0, rejected_before: 0, in_glossary: 0 };
      expect(fromEditsLine(answer({ patterns: 1, proposed: 1, report }))).toBe(
        "Looked at 12 corrected cues. 1 repeated change; 1 new suggestion in Review.",
      );
    });

    it("says why a pattern became no suggestion", () => {
      const line = fromEditsLine(answer({ patterns: 2, proposed: 1, unnamed: 1, report: { added: 0, pending_already: 1, accepted_before: 0, rejected_before: 0, in_glossary: 0 } }));
      expect(line).toContain("1 already waiting in Review");
      expect(line).toContain("1 without a word of the source in common");
    });
  });
});
