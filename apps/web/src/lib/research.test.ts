import { describe, expect, it } from "vitest";
import type { QueueAnswer, QueueJob, ResearchEstimate } from "../api/types";
import { researchJobOf, researchLabel, researchLines, researchOff, stripFor } from "./research";

const estimate = (over: Partial<ResearchEstimate> = {}): ResearchEstimate => ({
  ok: true,
  estimate_only: true,
  terms: 3,
  asked: 3,
  calls: 2,
  search_model: "gemini-3.1-flash-lite",
  read_model: "gemini-3.1-flash-lite",
  budget: { cap: 10, used: 4, after: 6 },
  auto_accept: true,
  blocked: "",
  reason: "",
  calls_left_today: null,
  paused_until: null,
  minutes: null,
  finish_at: null,
  ...over,
});

const job = (over: Partial<QueueJob> = {}): QueueJob => ({
  job_id: "research:1:vi-1",
  title: "Arcane",
  label: "",
  kind: "research",
  stored_series_id: 1,
  target_code: "vi",
  created_at: 100,
  ...over,
});

const queue = (over: Partial<Record<"pending" | "processing" | "deferred" | "done" | "failed" | "cancelled", QueueJob[]>> = {}): QueueAnswer =>
  ({ counts: {}, total: 0, daily_quota_pause: null, providers: [], pending: [], processing: [], deferred: [], done: [], failed: [], cancelled: [], ...over }) as unknown as QueueAnswer;

describe("Research terms button", () => {
  it("counts the terms in its label, and a run that cannot be made says why", () => {
    expect(researchLabel(3)).toBe("Research terms (3)");
    expect(researchLabel(undefined)).toBe("Research terms (0)");
    expect(researchOff(estimate())).toBeNull();
    expect(researchOff(estimate({ blocked: "no_terms" }))).toEqual({ reason: "No unmapped terms found", settings: false });
    expect(researchOff(estimate({ blocked: "no_web_search" }))).toEqual({ reason: "Web research requires Gemini", settings: true });
    expect(researchOff(estimate({ blocked: "budget" }))).toEqual({ reason: "Research budget reached for this series", settings: true });
    expect(researchOff(undefined)).toBeNull();
  });
});

describe("the price of a run", () => {
  it("lists the terms, the two calls, the models and what is left of the budget", () => {
    const lines = Object.fromEntries(researchLines(estimate()));
    expect(lines["Terms"]).toBe("3");
    expect(lines["Model calls (est.)"]).toBe("2 (1 search, 1 read)");
    expect(lines["Search model"]).toBe("gemini-3.1-flash-lite");
    expect(lines["Series budget"]).toBe("4 → 6 of 10");
    expect(lines).not.toHaveProperty("Calls left today");
  });

  it("says when only the first terms are asked, when there is no cap, and what the day still allows", () => {
    expect(Object.fromEntries(researchLines(estimate({ terms: 45, asked: 20 })))["Terms"]).toBe("20 of 45");
    expect(Object.fromEntries(researchLines(estimate({ budget: { cap: 0, used: 7, after: 9 } })))["Series budget"]).toBe("7 used · no cap");
    expect(Object.fromEntries(researchLines(estimate({ calls_left_today: 11 })))["Calls left today"]).toBe("11");
  });
});

describe("the research job of a series", () => {
  it("is the open one, else the newest that ended, of this series and language only", () => {
    expect(researchJobOf(queue(), 1, "vi")).toBeNull();
    expect(researchJobOf(undefined, 1, "vi")).toBeNull();
    expect(researchJobOf(queue({ done: [job()] }), null, "vi")).toBeNull();
    const old = job({ job_id: "old", created_at: 10 });
    const fresh = job({ job_id: "fresh", created_at: 50, error: "x" });
    expect(researchJobOf(queue({ done: [old], failed: [fresh] }), 1, "vi")).toEqual({ job: fresh, tab: "failed" });
    const open = job({ job_id: "open", created_at: 5 });
    expect(researchJobOf(queue({ done: [fresh], processing: [open] }), 1, "vi")).toEqual({ job: open, tab: "processing" });
    expect(researchJobOf(queue({ done: [job({ stored_series_id: 2 })] }), 1, "vi")).toBeNull(); // another series
    expect(researchJobOf(queue({ done: [job({ target_code: "fr" })] }), 1, "vi")).toBeNull(); // another language
    expect(researchJobOf(queue({ done: [job({ kind: "suggest" })] }), 1, "vi")).toBeNull(); // another kind
  });
});

describe("the strip under the button", () => {
  it("follows a run that is going: Stop is there while it can be stopped", () => {
    expect(stripFor(job(), "processing")).toEqual({ state: "running", text: "Looking terms up on the web…", stop: true, next: null });
    expect(stripFor(job(), "pending")).toMatchObject({ state: "waiting", stop: true });
    const waiting = stripFor(job({ wait_reason: "rate" }), "deferred");
    expect(waiting).toMatchObject({ state: "waiting", stop: true });
    expect(waiting.text).toContain("the search quota or the provider");
  });

  it("says what a finished run found and sends the person to Review", () => {
    const done = stripFor(job({ result: "proposed", report: { added: 3, auto_accepted: 2 } }), "done");
    expect(done).toEqual({ state: "done", text: "Done: 3 terms found, 2 applied by themselves.", stop: false, next: "review" });
    expect(stripFor(job({ result: "proposed", report: { added: 1 } }), "done").text).toBe("Done: 1 term found.");
    expect(stripFor(job({ result: "nothing-new" }), "done")).toMatchObject({ next: null, text: "Done: the web had nothing new for these terms." });
  });

  it("ends in a sentence with a next step, never a bare status", () => {
    expect(stripFor(job({ result: "budget-reached" }), "done").text).toMatch(/budget of this series is spent/);
    expect(stripFor(job({ result: "skipped-no-web-search" }), "done").text).toMatch(/no web search/);
    expect(stripFor(job({ error: "Traceback\nDaily quota exhausted" }), "failed")).toEqual({
      state: "failed",
      text: "Failed: Daily quota exhausted",
      stop: false,
      next: "retry",
    });
    expect(stripFor(job(), "cancelled")).toMatchObject({ state: "stopped", next: "retry" });
  });
});
