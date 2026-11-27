import { describe, expect, it } from "vitest";
import type { Estimate } from "../api/types";
import { estimateLines, planBreakdown } from "./EstimateSheet";

const base: Estimate = {
  jobs: 1,
  cues: 340,
  calls: 9,
  calls_left_today: null,
  paused_until: null,
  minutes: null,
  finish_at: null,
  extract_calls: 0,
  extract_model: "",
};

const labels = (estimate: Estimate) => estimateLines(estimate).map(([label]) => label);

describe("estimateLines", () => {
  it("has no extract row when no facts need reading", () => {
    expect(labels(base)).toEqual(["Jobs", "Cues", "Translate (est.)"]);
  });

  it("puts the extract row right under the translation calls, with the model it runs on", () => {
    const lines = estimateLines({ ...base, extract_calls: 3, extract_model: "flash-lite", calls_left_today: 40 });
    expect(lines.map(([label]) => label)).toEqual(["Jobs", "Cues", "Translate (est.)", "Extract facts", "Total calls (est.)", "Calls left today"]);
    expect(lines[3]).toEqual(["Extract facts", "3 calls · flash-lite"]);
    expect(lines[4]).toEqual(["Total calls (est.)", "12"]);
  });

  it("says 'call' for one and leaves the model out when the answer names none", () => {
    expect(estimateLines({ ...base, extract_calls: 1 })[3]).toEqual(["Extract facts", "1 call"]);
  });

  it("names the model of the translating calls, and calls a judge a judge", () => {
    expect(estimateLines({ ...base, model: "flash" })[2]).toEqual(["Translate (est.)", "9 calls · flash"]);
    expect(estimateLines({ ...base, calls: 1, model: "big" }, { callsLabel: "Judge (est.)" })[2]).toEqual(["Judge (est.)", "1 call · big"]);
    expect(estimateLines({ ...base, jobs: 99 }, { jobsLabel: "Episodes to release" })[0]).toEqual(["Episodes to release", "99"]);
  });

  it("adds the total only when two or more groups of calls are made", () => {
    expect(labels(base)).not.toContain("Total calls (est.)");
    expect(labels({ ...base, calls: 0, extract_calls: 2 })).not.toContain("Total calls (est.)"); // one group: the box has no sum to add
  });
});

describe("estimateLines, condensing the story", () => {
  it("has a row for the calls that condense old summaries, after the extract row", () => {
    const lines = estimateLines({ ...base, extract_calls: 2, extract_model: "lite", summarize_calls: 4, summarize_model: "lite-2" });
    expect(lines.map(([label]) => label)).toEqual(["Jobs", "Cues", "Translate (est.)", "Extract facts", "Condense story", "Total calls (est.)"]);
    expect(lines[4]).toEqual(["Condense story", "4 calls · lite-2"]);
    expect(lines[5]).toEqual(["Total calls (est.)", "15"]);
    expect(estimateLines({ ...base, calls: 0, summarize_calls: 1 })[3]).toEqual(["Condense story", "1 call"]);
  });

  it("has no such row when an older answer does not carry the count", () => {
    expect(labels({ ...base, summarize_calls: undefined })).toEqual(["Jobs", "Cues", "Translate (est.)"]);
  });
});

describe("planBreakdown", () => {
  it("lists new translations and retranslations with reasons for mode=auto", () => {
    const lines = planBreakdown(
      {
        estimate_only: true,
        mode: "auto",
        estimate: { ...base, jobs: 3 },
        planned_total: 3,
        translate_total: 1,
        retranslate_total: 2,
        skipped_total: 0,
        skipped_by_reason: {},
      },
      "vi",
    );
    expect(lines).toEqual([
      "New translations: 1 episode (no Vietnamese subtitle yet)",
      "Retranslations: 2 episodes (already translated; previous file kept as a version)",
    ]);
  });
});
