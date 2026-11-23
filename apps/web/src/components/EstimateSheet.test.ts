import { describe, expect, it } from "vitest";
import type { Estimate } from "../api/types";
import { estimateLines } from "./EstimateSheet";

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
    expect(labels(base)).toEqual(["Jobs", "Cues", "Model calls (est.)"]);
  });

  it("puts the extract row right under the translation calls, with the model it runs on", () => {
    const lines = estimateLines({ ...base, extract_calls: 3, extract_model: "flash-lite", calls_left_today: 40 });
    expect(lines.map(([label]) => label)).toEqual(["Jobs", "Cues", "Model calls (est.)", "Extract facts", "Calls left today"]);
    expect(lines[3]).toEqual(["Extract facts", "3 calls · flash-lite"]);
  });

  it("says 'call' for one and leaves the model out when the answer names none", () => {
    expect(estimateLines({ ...base, extract_calls: 1 })[3]).toEqual(["Extract facts", "1 call"]);
  });
});
