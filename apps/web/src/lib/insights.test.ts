import { describe, expect, it } from "vitest";
import type { FromEditsAnswer } from "../api/types";
import {
  abLabel,
  against,
  barWidth,
  dailyLimit,
  dayEditRate,
  duration,
  editsOf,
  fromEditsLine,
  goldenFileName,
  millis,
  money,
  outcomeLabel,
  percent,
  preview,
  tasksLine,
  tooFewToConclude,
  trend,
} from "./insights";

const noReport = { added: 0, auto_accepted: 0, pending_already: 0, accepted_before: 0, rejected_before: 0, in_glossary: 0, invalid: [], failed_to_apply: [] };

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
    const days = [{ episodes: 10 }, { episodes: 5 }, { episodes: 0 }];
    expect(days.map((d) => barWidth(d, days))).toEqual([100, 50, 0]);
    expect(barWidth({ episodes: 0 }, [{ episodes: 0 }])).toBe(0);
  });

  it("says how a rate moved against the window before, a lower one being better", () => {
    expect(trend(null, 0.05)).toBeNull();
    expect(trend(0.04, null)).toEqual({ tone: "none", text: "— no previous data" });
    expect(trend(0.042, 0.048)).toEqual({ tone: "better", text: "▼ 0.6 pts" });
    expect(trend(0.02, 0.018)).toEqual({ tone: "worse", text: "▲ 0.2 pts" });
    expect(trend(0.03, 0.03)).toEqual({ tone: "same", text: "= no change" });
  });

  it("adds up the corrected cues of a series, of one language or of all", () => {
    const counts = [{ series_id: 1, lang: "vi", edits: 14 }, { series_id: 1, lang: "fr", edits: 2 }, { series_id: 2, lang: "vi", edits: 6 }];
    expect(editsOf(counts, 1, "vi")).toBe(14);
    expect(editsOf(counts, 1)).toBe(16);
    expect(editsOf(counts, 3, "vi")).toBe(0);
  });

  it("lists the calls by task, the most used first", () => {
    expect(tasksLine({ extract: 38, translate: 412 })).toBe("Translate 412 · Extract 38");
    expect(tasksLine({})).toBe("—");
  });

  it("says a style has too few comparisons under ten, and a day's edit rate only when something was translated", () => {
    expect(tooFewToConclude({ judged: 9 })).toBe(true);
    expect(tooFewToConclude({ judged: 10 })).toBe(false);
    expect(dayEditRate({ cues_translated: 0, cue_edits: 0 })).toBeNull();
    expect(dayEditRate({ cues_translated: 200, cue_edits: 8 })).toBe(0.04);
  });

  it("writes an amount with the decimals a small price needs, and no data as a dash", () => {
    expect(money(null)).toBe("—");
    expect(money(0.014)).toBe("0.014");
    expect(money(1.2)).toBe("1.20");
  });

  it("names the golden file by series key and language, and cuts a long text to its first lines", () => {
    expect(goldenFileName("arcane", "vi")).toBe("arcane.vi.golden.json");
    expect(goldenFileName("Bazarr 7 (UK)", "VI")).toBe("bazarr-7--uk.vi.golden.json"); // as the worker names the file
    expect(preview("a\nb\nc", 5)).toBe("a\nb\nc");
    expect(preview("a\nb\nc\nd", 2)).toBe("a\nb\n…");
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
      const report = { ...noReport, added: 1 };
      expect(fromEditsLine(answer({ patterns: 1, proposed: 1, report }))).toBe(
        "Looked at 12 corrected cues. 1 repeated change; 1 new suggestion in Review.",
      );
    });

    it("says why a pattern became no suggestion", () => {
      const line = fromEditsLine(answer({ patterns: 2, proposed: 1, unnamed: 1, report: { ...noReport, pending_already: 1 } }));
      expect(line).toContain("1 already waiting in Review");
      expect(line).toContain("1 without a word of the source in common");
    });
  });
});
