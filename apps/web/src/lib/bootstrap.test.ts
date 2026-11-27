import { describe, expect, it } from "vitest";
import { bootstrapState } from "../test/fixtures";
import { bootstrapMode, capLine, holdingLine, modeLine, releaseLabel, requestedLine } from "./bootstrap";

describe("bootstrapMode", () => {
  it("picks the first state that is true, in the order the box answers them", () => {
    expect(bootstrapMode(bootstrapState({ pending: 3, open_jobs: 2, memory_on: false }))).toBe("review");
    expect(bootstrapMode(bootstrapState({ open_jobs: 2, memory_on: false }))).toBe("running");
    expect(bootstrapMode(bootstrapState({ memory_on: false }))).toBe("memory-off");
    expect(bootstrapMode(bootstrapState())).toBe("empty");
  });
});

describe("the sentences of the box", () => {
  it("counts the episodes held in the language on screen, not in all of them", () => {
    const state = bootstrapState({ held: 120, held_by_lang: { vi: 99, fr: 21 } });
    expect(holdingLine(state, "vi")).toBe("Auto-request is holding 99 episodes for the glossary");
    expect(holdingLine(state, "fr")).toBe("Auto-request is holding 21 episodes for the glossary");
    expect(holdingLine(bootstrapState({ held_by_lang: { vi: 1 } }), "vi")).toBe("Auto-request is holding 1 episode for the glossary");
    expect(holdingLine(state, "de")).toBe("Auto-request is holding 0 episodes for the glossary"); // a language it has no count for
  });

  it("says that only episodes with a source subtitle were requested, and counts the others apart", () => {
    expect(requestedLine(bootstrapState({ limit: 2 }))).toBe("Only the first 2 episodes with source subtitles were requested.");
    expect(requestedLine(bootstrapState({ limit: 1, no_source: 1 }))).toBe(
      "Only the first 1 episode with source subtitles was requested (1 other episode has no source subtitle yet).",
    );
    expect(requestedLine(bootstrapState({ limit: 2, no_source: 22 }))).toContain("(22 other episodes have no source subtitle yet)");
  });

  it("does not promise a review that nothing is waiting for", () => {
    for (const mode of ["running", "memory-off", "empty"] as const) {
      expect(modeLine(mode, bootstrapState())).not.toMatch(/until the suggestions are reviewed/);
    }
    expect(modeLine("review", bootstrapState({ pending: 1 }))).toBe(
      "1 suggestion waits for your review. Decide them and auto-request goes on by itself.",
    );
  });

  it("names the button after what it does", () => {
    expect(releaseLabel(bootstrapState({ glossary_empty: true, held_by_lang: { vi: 99 } }), "vi")).toBe("Auto-request 99 without glossary…");
    expect(releaseLabel(bootstrapState({ glossary_empty: false, held_by_lang: { vi: 99 } }), "vi")).toBe("Auto-request remaining 99…");
  });

  it("says the day's cap only when there is one", () => {
    expect(capLine(0, null)).toBeNull();
    expect(capLine(undefined, null)).toBeNull();
    expect(capLine(50, 40)).toBe("up to 40 today · daily cap 50");
    expect(capLine(50, null)).toBe("up to 50 today · daily cap 50");
  });
});
