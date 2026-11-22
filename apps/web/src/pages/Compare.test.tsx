import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CompareRow, JudgeSummary } from "../api/types";
import { judgeAsk, judgeNote, judgeStatus, styleLines, tally, verdicts, visibleRows } from "../lib/compare";
import { sent, serve, show } from "../test/render";
import { base, episode } from "../test/unit-fixtures";

const row = (n: number, differs: boolean): CompareRow => ({
  n,
  source: `Source ${n}`,
  current: `Current ${n}`,
  variant: differs ? `Variant ${n}` : `Current ${n}`,
  differs,
});

const rows = [row(1, true), row(2, false), row(3, true)];

const compare = (overrides: Record<string, unknown> = {}) => ({
  lang: "vi",
  version: 4,
  status: "candidate",
  params: { variant: { label: "gritty", preset: "drama", style: { formality: "casual", profanity: "keep" } } },
  model: "flash",
  aligned: true,
  differing: 2,
  rows,
  ...overrides,
});

const judgeSummary = (overrides: Partial<JudgeSummary> = {}): JudgeSummary => ({
  enabled: true,
  model: "big-1",
  prompt_version: "j1",
  judged: 0,
  variant: 0,
  current: 0,
  tie: 0,
  todo: 2,
  job: null,
  ...overrides,
});

const estimate = { jobs: 1, cues: 2, calls: 1, calls_left_today: null, paused_until: null, minutes: null, finish_at: null };

const versions = {
  lang: "vi",
  modified_outside: false,
  versions: [
    { version: 4, trigger: "variant", origin: "machine", status: "candidate", parent_version: 3, model: "flash", tokens_in: 1, tokens_out: 1, created_at: 1, current: false, params: {} },
    { version: 3, trigger: "auto", origin: "machine", status: "current", parent_version: null, model: "flash", tokens_in: 1, tokens_out: 1, created_at: 1, current: true, params: {} },
  ],
};

const routes = (extra: Record<string, unknown> = {}) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/versions/4/compare?lang=vi": { body: compare() },
  "/api/units/7/versions?lang=vi": { body: versions },
  ...extra,
});

describe("compare helpers", () => {
  it("show only the cues that differ, unless asked for all", () => {
    expect(visibleRows(rows, true).map((r) => r.n)).toEqual([1, 3]);
    expect(visibleRows(rows, false)).toHaveLength(3);
  });

  it("give a verdict on every differing cue: the picked ones to the variant, the rest to the current text", () => {
    expect(verdicts(rows, new Set([1]))).toEqual({ "1": "variant", "3": "current" });
    expect(tally(rows, new Set([1, 2]))).toEqual({ fromVariant: 1, kept: 2 }); // cue 2 does not differ: it cannot come from the variant
  });

  it("name what a variant sets, and leave out what it does not", () => {
    expect(styleLines("drama", { formality: "casual", allow_condense: false, tone: "dark" }, "Drama")).toEqual([
      ["Preset", "Drama"],
      ["Formality", "Casual"],
      ["Fast dialogue", "Don't"],
      ["Tone", "dark"],
    ]);
    expect(styleLines("", undefined)).toEqual([["Preset", "—"]]);
  });
});

describe("judge helpers", () => {
  it("say in words which side the judge prefers, with its reason when it gave one", () => {
    expect(judgeNote({ winner: "variant", why: "tighter" })).toBe("Judge prefers the variant: tighter");
    expect(judgeNote({ winner: "current", why: "" })).toBe("Judge prefers the current text");
    expect(judgeNote({ winner: "tie", why: "" })).toBe("Judge sees no real difference");
  });

  it("tell where the judge stands: not asked, working, or done with a tally", () => {
    expect(judgeStatus(judgeSummary())).toBe("The judge has not looked at this candidate yet.");
    expect(judgeStatus(judgeSummary({ job: { job_id: "j", state: "queued", stage: "" } }))).toBe("The judge is in line.");
    expect(judgeStatus(judgeSummary({ job: { job_id: "j", state: "running", stage: "" } }))).toBe("The judge is working.");
    expect(judgeStatus(judgeSummary({ judged: 1, current: 1, todo: 0 }))).toBe("The judge looked at 1 cue: 0 prefer the variant, 1 the current text, 0 tie.");
  });

  it("offer to ask about what is left, or again when nothing is left, and never while a job is open", () => {
    expect(judgeAsk(judgeSummary({ todo: 1 }))).toEqual({ label: "Ask the judge (1 cue)", again: false });
    expect(judgeAsk(judgeSummary({ todo: 1500, judged: 3 }))).toEqual({ label: "Ask the judge (1,500 cues)", again: false });
    expect(judgeAsk(judgeSummary({ todo: 0, judged: 3 }))).toEqual({ label: "Ask the judge again", again: true });
    expect(judgeAsk(judgeSummary({ todo: 0, judged: 0 }))).toBeNull();
    expect(judgeAsk(judgeSummary({ job: { job_id: "j", state: "queued", stage: "" } }))).toBeNull();
  });
});

describe("Compare page", () => {
  it("shows the two versions in the title and only the cues that differ", async () => {
    serve(routes());
    show("/unit/7/compare/4?lang=vi");
    expect(await screen.findByRole("heading", { name: 'Current v3 vs Candidate v4 "gritty"' })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Cues" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "Show only differing cues (2 of 3)" })).toBeChecked();
    expect(screen.getByText("Selected 0 from variant, 3 kept from current")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Show only differing/ }));
    expect(within(screen.getByRole("list", { name: "Cues" })).getAllByRole("listitem")).toHaveLength(3);
  });

  it("applies the picked cues and records the rest as kept", async () => {
    serve(routes({ "/api/units/7/versions/4/choose": { body: { ok: true, recorded: 2, applied: {} } } }));
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick variant for cue 1" }));
    expect(screen.getByText("Selected 1 from variant, 2 kept from current")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Apply picked cues" }));
    await waitFor(() =>
      expect(sent("/api/units/7/versions/4/choose")).toEqual([{ lang: "vi", picks: { "1": "variant", "3": "current" }, apply: true }]),
    );
    expect(await screen.findByText(/Done\./)).toBeInTheDocument();
  });

  it("cannot apply with nothing picked", async () => {
    serve(routes());
    show("/unit/7/compare/4?lang=vi");
    expect(await screen.findByRole("button", { name: "Apply picked cues" })).toBeDisabled();
  });

  it("takes the whole version with its style after a confirmation that counts what it flags", async () => {
    serve(
      routes({
        "/api/units/7/versions/4/adopt": { body: { ok: true, adopted: null, stale_now: 0, stale_after: 11, would_stale: 11, style: { preset: "drama", style: {} } } },
        "/api/units/7/versions/4/choose": { body: { ok: true, recorded: 1, applied: { adopted: 4 } } },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Use this version" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("checkbox", { name: "Also apply this variant's style to the series" })).toBeChecked();
    expect(within(sheet).getByText("Casual")).toBeInTheDocument();
    expect(await within(sheet).findByText(/Saving flags 11 episodes stale. Nothing is retranslated./)).toBeInTheDocument();
    expect(sent("/api/units/7/versions/4/adopt")).toEqual([{ lang: "vi", apply_style: true, dry_run: true }]);
    expect(sent("/api/units/7/versions/4/choose")).toEqual([]);

    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/units/7/versions/4/choose")).toEqual([{ lang: "vi", whole: "variant", apply: true, apply_style: true }]));
  });

  it("takes only the subtitle when the style box is unticked", async () => {
    serve(
      routes({
        "/api/units/7/versions/4/adopt": { body: { ok: true, adopted: null, stale_now: 0, stale_after: 0, would_stale: 0 } },
        "/api/units/7/versions/4/choose": { body: { ok: true, recorded: 1, applied: {} } },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Use this version" }));
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("checkbox", { name: "Also apply this variant's style to the series" }));
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/units/7/versions/4/choose")).toEqual([{ lang: "vi", whole: "variant", apply: true }]));
  });

  it("shows nothing about the judge while it is off", async () => {
    serve(routes({ "/api/units/7/versions/4/compare?lang=vi": { body: compare({ judge: judgeSummary({ enabled: false }) }) } }));
    show("/unit/7/compare/4?lang=vi");
    await screen.findByRole("heading", { name: /Candidate v4/ });
    expect(screen.queryByRole("region", { name: "Judge" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ask the judge/ })).not.toBeInTheDocument();
  });

  it("shows the judge's advice next to a cue in words, and keeps the pick with the admin", async () => {
    const judged = rows.map((r) =>
      r.differs ? { ...r, judge: { winner: r.n === 1 ? "variant" : "tie", why: r.n === 1 ? "tighter" : "" } } : r,
    );
    serve(
      routes({
        "/api/units/7/versions/4/compare?lang=vi": {
          body: compare({ rows: judged, judge: judgeSummary({ judged: 2, variant: 1, tie: 1, todo: 0 }) }),
        },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    const panel = await screen.findByRole("region", { name: "Judge" });
    expect(within(panel).getByRole("status")).toHaveTextContent("The judge looked at 2 cues: 1 prefer the variant, 0 the current text, 1 tie.");
    expect(within(panel).getByText("Advice from big-1, never a pick: only what you tick counts.")).toBeInTheDocument();
    expect(screen.getByText("Judge prefers the variant: tighter")).toBeInTheDocument();
    expect(screen.getByText("Judge sees no real difference")).toBeInTheDocument();
    expect(screen.getByText("Selected 0 from variant, 3 kept from current")).toBeInTheDocument(); // the advice ticked nothing
    expect(screen.getByRole("checkbox", { name: "Pick variant for cue 1" })).not.toBeChecked();
    expect(within(panel).getByRole("button", { name: "Ask the judge again" })).toBeInTheDocument();
  });

  it("asks the price of the judge before it spends anything, then queues it", async () => {
    serve(
      routes({
        "/api/units/7/versions/4/compare?lang=vi": { body: compare({ judge: judgeSummary() }) },
        "/api/units/7/versions/4/judge": (body: Record<string, unknown> | null) =>
          body?.estimate_only === true
            ? { body: { ok: true, estimate_only: true, cues: 2, calls: 1, capped: false, model: "big-1", estimate } }
            : { body: { ok: true, job_id: "judge-1", new: true, cues: 2, calls: 1 } },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Ask the judge (2 cues)" }));
    const sheet = await screen.findByRole("dialog", { name: "Ask the judge · Candidate v4" });
    expect(await within(sheet).findByText("Model calls (est.)")).toBeInTheDocument();
    expect(sent("/api/units/7/versions/4/judge")).toEqual([{ lang: "vi", estimate_only: true }]); // nothing is queued by looking
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/units/7/versions/4/judge")).toEqual([{ lang: "vi", estimate_only: true }, { lang: "vi" }]));
    expect(await within(sheet).findByText(/1 job is in line/)).toBeInTheDocument();
  });

  it("asks again about every cue only when it has seen them all", async () => {
    serve(
      routes({
        "/api/units/7/versions/4/compare?lang=vi": { body: compare({ judge: judgeSummary({ judged: 2, variant: 2, todo: 0 }) }) },
        "/api/units/7/versions/4/judge": { body: { ok: true, estimate_only: true, cues: 2, calls: 1, capped: false, model: "big-1", estimate } },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Ask the judge again" }));
    await waitFor(() => expect(sent("/api/units/7/versions/4/judge")).toEqual([{ lang: "vi", again: true, estimate_only: true }]));
  });

  it("says the judge is at work and offers no second request", async () => {
    const job = { job_id: "judge-1", state: "running", stage: "Judging cues 1 to 30" };
    serve(routes({ "/api/units/7/versions/4/compare?lang=vi": { body: compare({ judge: judgeSummary({ job }) }) } }));
    show("/unit/7/compare/4?lang=vi");
    const panel = await screen.findByRole("region", { name: "Judge" });
    expect(within(panel).getByRole("status")).toHaveTextContent("The judge is working: Judging cues 1 to 30.");
    expect(within(panel).queryByRole("button")).not.toBeInTheDocument();
  });

  it("has no judge for a version that is no longer a candidate", async () => {
    serve(routes({ "/api/units/7/versions/4/compare?lang=vi": { body: compare({ status: "superseded", judge: judgeSummary() }) } }));
    show("/unit/7/compare/4?lang=vi");
    await screen.findByText("This version is superseded, so it can only be looked at.");
    expect(screen.queryByRole("region", { name: "Judge" })).not.toBeInTheDocument();
  });

  it("only looks at a version that is no longer a candidate", async () => {
    serve(routes({ "/api/units/7/versions/4/compare?lang=vi": { body: compare({ status: "superseded" }) } }));
    show("/unit/7/compare/4?lang=vi");
    expect(await screen.findByText("This version is superseded, so it can only be looked at.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use this version" })).not.toBeInTheDocument();
  });
});
