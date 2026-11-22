import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Insights } from "../api/types";
import { sent, serve, show } from "../test/render";

const report = (over: Partial<Insights> = {}): Insights => ({
  window: { days: 30, since: 1_700_000_000, until: 1_702_592_000 },
  operations: {
    queue: { counts: { pending: 3, processing: 1, deferred: 2, done: 9, failed: 1 }, depth: 6 },
    providers: [{ model: "gemini-3.1-flash-lite", state: "closed", reason: "", until: null, failures: 0, usable: true }],
    daily_quota_pause: null,
    wait_seconds: { jobs: 4, median: 30, p90: 100 },
    usage: { calls_today: 7, calls_last_minute: 2, limits: { rpm: 10, tpm: 0, rpd: 250, max_calls_per_day: 100 } },
    calls: { total: 11, by_outcome: { ok: 9, rate_limited: 1, unavailable: 1 }, failed: 2, rate_429: 0.0909, rate_503: 0.0909 },
    escalation: { jobs: 10, escalated: 4, rescued: 3, rate: 0.4, rescue_rate: 0.75, window_days: 30 },
    per_episode: { episodes: 3, tokens_in: 213.3, tokens_out: 100, cost: 0.03 },
    tokens: { in: 660, out: 330, cost: 0.11 },
    latency_ms: { calls: 9, mean: 1000, median: 1000, p90: 2400 },
  },
  quality: {
    cues_translated: 250,
    episodes: 3,
    repair: { calls: 1, translate_calls: 8, rate: 0.125, cues: 3, cue_rate: 0.012 },
    cue_edits: { cues: 3, rate: 0.012 },
    retranslations: { count: 2, rate: 0.6667, reverts: 1 },
    review: { by_kind: { cps: 6, line_length: 2 }, cps: 6, cps_rate: 0.024, too_long: 2, too_long_rate: 0.008 },
    validate: { missing: 1 },
  },
  by_model: [
    { model: "m-1", calls: 9, episodes: 2, cues: 200, tokens_in: 600, tokens_out: 300, cost: 0.09, repair_rate: 0.125, cue_edits: 3, cue_edit_rate: 0.015, retranslations: 2 },
    { model: "m-2", calls: 2, episodes: 1, cues: 50, tokens_in: 200, tokens_out: 100, cost: 0.02, repair_rate: 0, cue_edits: 0, cue_edit_rate: 0, retranslations: 0 },
  ],
  by_prompt_version: [
    { prompt_version: "p1", calls: 9, episodes: 2, cues: 200, tokens_in: 600, tokens_out: 300, cost: 0.09, repair_rate: 0.125, cue_edits: 3, cue_edit_rate: 0.015, retranslations: null },
  ],
  ab: [{ style: "casual", prompt_version: "p1", variant: 2, current: 1, tie: 1, judged: 4, score: 0.625 }],
  daily: [
    { day: "2026-11-19", calls: 0, failed: 0, tokens: 0, cost: 0, cues_translated: 0, cue_edits: 0 },
    { day: "2026-11-20", calls: 7, failed: 0, tokens: 700, cost: 0.07, cues_translated: 100, cue_edits: 3 },
  ],
  ...over,
});

const empty = report({
  operations: {
    ...report().operations,
    calls: { total: 0, by_outcome: {}, failed: 0, rate_429: null, rate_503: null },
    wait_seconds: { jobs: 0, median: null, p90: null },
    escalation: { jobs: 0, escalated: 0, rescued: 0, rate: null, rescue_rate: null, window_days: 30 },
    per_episode: { episodes: 0, tokens_in: null, tokens_out: null, cost: null },
    tokens: { in: 0, out: 0, cost: 0 },
    latency_ms: { calls: 0, mean: null, median: null, p90: null },
  },
  quality: {
    cues_translated: 0,
    episodes: 0,
    repair: { calls: 0, translate_calls: 0, rate: null, cues: 0, cue_rate: null },
    cue_edits: { cues: 0, rate: null },
    retranslations: { count: 0, rate: null, reverts: 0 },
    review: { by_kind: {}, cps: 0, cps_rate: null, too_long: 0, too_long_rate: null },
    validate: {},
  },
  by_model: [],
  by_prompt_version: [],
  ab: [],
  daily: [],
});

const shell = {
  "/api/status": { body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } } },
  "/api/catalog/status": { body: { synced_at: Date.now() / 1000, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/suggestions/summary": { body: { counts: { pending: 0, accepted: 0, rejected: 0 }, pending: 0, series: [] } },
};

const series = { body: { items: [{ id: 7, key: "bazarr-7", title: "Arcane", units: 3 }] } };
const units = { body: { languages: ["vi"], units: [] } };

function pageRoutes(extra: Record<string, unknown> = {}) {
  return {
    ...shell,
    "/api/metrics?days=30": { body: report() },
    "/api/library/series": series,
    "/api/library/series/7/units": units,
    ...extra,
  } as Parameters<typeof serve>[0];
}

afterEach(() => vi.unstubAllGlobals());

describe("Insights", () => {
  it("is in the main navigation", async () => {
    serve(pageRoutes());
    show("/insights");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Insights" })).toHaveAttribute("href", "/insights");
    await screen.findByRole("heading", { name: "Insights" });
  });

  it("leads with the share of cues you corrected, and says out of how many", async () => {
    serve(pageRoutes());
    show("/insights");
    const quality = await screen.findByRole("region", { name: "Quality" });
    const stat = within(quality).getByRole("group", { name: "Cues you corrected" });
    expect(stat).toHaveTextContent("1.2 %");
    expect(stat).toHaveTextContent("3 of 250 cues translated");
    expect(within(quality).getByRole("group", { name: "Repairs" })).toHaveTextContent("13 %");
    expect(quality).toHaveTextContent("Read too fast 6");
  });

  it("gives each model and each prompt version its own card", async () => {
    serve(pageRoutes());
    show("/insights");
    const models = await screen.findByRole("list", { name: "By model" });
    const cards = within(models).getAllByRole("listitem");
    expect(cards.map((card) => card.querySelector("p")?.textContent)).toEqual(["m-1", "m-2"]);
    expect(cards[0]).toHaveTextContent("2 retranslated after you read them.");
    const prompts = screen.getByRole("list", { name: "By prompt version" });
    expect(within(prompts).getByText("p1")).toBeInTheDocument();
    expect(prompts).not.toHaveTextContent("retranslated after");
  });

  it("shows the style trials with their score", async () => {
    serve(pageRoutes());
    show("/insights");
    const trials = await screen.findByRole("list", { name: "Style trials" });
    expect(trials).toHaveTextContent("casual · prompt p1");
    expect(trials).toHaveTextContent("Score 63 %");
  });

  it("shows today's calls against the limit that bites first, and the models as working", async () => {
    serve(pageRoutes());
    show("/insights");
    const now = await screen.findByRole("region", { name: "Now" });
    expect(within(now).getByRole("group", { name: "Calls today" })).toHaveTextContent("7 of 100 calls");
    expect(within(now).getByRole("group", { name: "Calls in the last minute" })).toHaveTextContent("2 of 10 calls");
    expect(within(now).getByRole("group", { name: "Queue" })).toHaveTextContent("1 running, 3 requested, 2 waiting");
    expect(within(now).getByText("gemini-3.1-flash-lite: working")).toBeInTheDocument();
  });

  it("writes no data as a dash, never as 0 %", async () => {
    serve(pageRoutes({ "/api/metrics?days=30": { body: empty } }));
    show("/insights");
    const quality = await screen.findByRole("region", { name: "Quality" });
    expect(within(quality).getByRole("group", { name: "Cues you corrected" })).toHaveTextContent("—");
    expect(quality).not.toHaveTextContent("0 %");
    expect(screen.getAllByText("Nothing has been translated in this window.")).toHaveLength(2); // by model, by prompt version
    expect(screen.getByText("No variant has been judged cue by cue yet.")).toBeInTheDocument();
  });

  it("asks again for another window", async () => {
    serve(pageRoutes({ "/api/metrics?days=7": { body: report({ window: { days: 7, since: 1, until: 2 } }) } }));
    show("/insights");
    await screen.findByRole("region", { name: "Quality" });
    await userEvent.click(screen.getByRole("radio", { name: "7 days" }));
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/api/metrics?days=7")).toBe(true));
  });

  it("offers each series' golden set as a link the worker serves, and looks at the edits for free", async () => {
    serve(
      pageRoutes({
        "/api/suggestions/from-edits": {
          body: { cues: 12, without_before: 0, patterns: 1, proposed: 1, unnamed: 0, report: { added: 1, pending_already: 0, accepted_before: 0, rejected_before: 0, in_glossary: 0 } },
        },
      }),
    );
    show("/insights");
    const link = await screen.findByRole("link", { name: "Download the vi golden set of Arcane" });
    expect(link).toHaveAttribute("href", "/api/series/7/golden?lang=vi&download=1");
    await userEvent.click(screen.getByRole("button", { name: "Look at my vi edits of Arcane" }));
    expect(await screen.findByText(/Looked at 12 corrected cues\. 1 repeated change; 1 new suggestion in Review\./)).toBeInTheDocument();
    expect(sent("/api/suggestions/from-edits")).toEqual([{ series_id: 7, lang: "vi" }]);
  });

  it("tells the admin when the figures cannot be read", async () => {
    serve(pageRoutes({ "/api/metrics?days=30": { status: 500, body: { error: "the database is busy" } } }));
    show("/insights");
    // A failure that is not the client's is tried once more before it is shown.
    expect(await screen.findByText("the database is busy", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
