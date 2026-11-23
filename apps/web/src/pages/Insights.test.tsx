import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Insights } from "../api/types";
import { sent, serve, show } from "../test/render";

const report = (over: Partial<Insights> = {}): Insights => ({
  window: { days: 30, since: 1_700_000_000, until: 1_702_592_000 },
  operations: {
    queue: { counts: { pending: 3, processing: 1, deferred: 2, done: 9, failed: 1, cancelled: 0 }, depth: 6 },
    providers: [{ provider: "gemini", model: "gemini-3.1-flash-lite", state: "closed", reason: "", until: null, failures: 0, usable: true }],
    daily_quota_pause: null,
    wait_seconds: { jobs: 4, median: 30, p90: 100 },
    usage: { calls_today: 7, calls_last_minute: 2, limits: { rpm: 10, tpm: 0, rpd: 250, max_calls_per_day: 100 } },
    calls: { total: 11, by_outcome: { ok: 9, rate_limited: 1, unavailable: 1 }, failed: 2, rate_429: 0.0909, rate_503: 0.0909 },
    escalation: { jobs: 10, escalated: 4, rescued: 3, rate: 0.4, rescue_rate: 0.75, window_days: 30 },
    per_episode: { episodes: 3, tokens_in: 213.3, tokens_out: 100, cost: 0.03 },
    tokens: { in: 660, out: 330, cost: 0.11 },
    latency_ms: { calls: 9, mean: 1000, median: 1000, p90: 2400 },
    calls_by_task: { translate: 9, extract: 2 },
    research_cost: 0,
    quota_pauses: 2,
    breaker_opens: 1,
    jobs: { done: 8, failed: 2, success_rate: 0.8, retries_per_episode: 0.67 },
  },
  previous: { cue_edit_rate: 0.018, repair_rate: 0.1, retranslate_rate: null, cps_rate: 0.024, too_long_rate: 0.012 },
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
    { model: "m-1", calls: 9, episodes: 2, cues: 200, tokens_in: 600, tokens_out: 300, cost: 0.09, repair_rate: 0.125, cue_edits: 3, cue_edit_rate: 0.015, cps_rate: 0.03, too_long_rate: 0.01, retranslations: 2 },
    { model: "m-2", calls: 2, episodes: 1, cues: 50, tokens_in: 200, tokens_out: 100, cost: 0.02, repair_rate: 0, cue_edits: 0, cue_edit_rate: 0, cps_rate: 0, too_long_rate: 0, retranslations: 0 },
  ],
  by_prompt_version: [
    { prompt_version: "p1", calls: 9, episodes: 2, cues: 200, tokens_in: 600, tokens_out: 300, cost: 0.09, repair_rate: 0.125, cue_edits: 3, cue_edit_rate: 0.015, cps_rate: 0.03, too_long_rate: 0.01, retranslations: null },
  ],
  by_model_prompt: [
    { model: "m-1", prompt_version: "p1", calls: 9, episodes: 2, cues: 200, tokens_in: 600, tokens_out: 300, cost: 0.09, repair_rate: 0.125, cue_edits: 3, cue_edit_rate: 0.015, cps_rate: 0.03, too_long_rate: 0.01, retranslations: null },
    { model: "m-2", prompt_version: "p2", calls: 2, episodes: 1, cues: 50, tokens_in: 200, tokens_out: 100, cost: 0.02, repair_rate: 0, cue_edits: 0, cue_edit_rate: 0, cps_rate: 0, too_long_rate: 0, retranslations: null },
  ],
  ab: [{ style: "casual", prompt_version: "p1", variant: 2, current: 1, tie: 1, judged: 4, score: 0.625 }],
  daily: [
    { day: "2026-11-19", episodes: 0, calls: 0, failed: 0, tokens: 0, cost: 0, cues_translated: 0, cue_edits: 0 },
    { day: "2026-11-20", episodes: 2, calls: 7, failed: 0, tokens: 700, cost: 0.07, cues_translated: 100, cue_edits: 3 },
  ],
  corrections: [{ series_id: 7, lang: "vi", edits: 14 }],
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
    calls_by_task: {},
    research_cost: 0,
    quota_pauses: 0,
    breaker_opens: 0,
    jobs: { done: 0, failed: 0, success_rate: null, retries_per_episode: null },
  },
  previous: { cue_edit_rate: null, repair_rate: null, retranslate_rate: null, cps_rate: null, too_long_rate: null },
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
  by_model_prompt: [],
  ab: [],
  daily: [],
  corrections: [],
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

  it("gives each model with its prompt version one card, with the review rates", async () => {
    serve(pageRoutes());
    show("/insights");
    const pairs = await screen.findByRole("list", { name: "By model and prompt version" });
    const cards = within(pairs).getAllByRole("listitem");
    expect(cards.map((card) => card.querySelector("p")?.textContent)).toEqual(["m-1 · prompt p1", "m-2 · prompt p2"]);
    const first = within(cards[0]!);
    expect(first.getByRole("group", { name: "Cues you corrected" })).toHaveTextContent("1.5 %");
    expect(first.getByRole("group", { name: "Read too fast" })).toHaveTextContent("3.0 %");
    expect(first.getByRole("group", { name: "Lines too long" })).toHaveTextContent("1.0 %");
  });

  it("says how each quality rate moved against the window before, and when there is nothing to compare with", async () => {
    serve(pageRoutes());
    show("/insights");
    const quality = await screen.findByRole("region", { name: "Quality" });
    expect(within(quality).getByRole("group", { name: "Cues you corrected" })).toHaveTextContent("▼ 0.6 pts"); // 1.2 % against 1.8 %
    expect(within(quality).getByRole("group", { name: "Repairs" })).toHaveTextContent("▲ 2.5 pts"); // 12.5 % against 10 %
    expect(within(quality).getByRole("group", { name: "Retranslated" })).toHaveTextContent("— no previous data");
  });

  it("shows the quota, breaker, job and research figures the wireframe asks for", async () => {
    serve(pageRoutes());
    show("/insights");
    const cost = await screen.findByRole("region", { name: "Queue, quota and cost" });
    expect(within(cost).getByRole("group", { name: "Quota pauses" })).toHaveTextContent("2");
    expect(within(cost).getByRole("group", { name: "Calls by task" })).toHaveTextContent("Translate 9 · Extract 2");
    expect(within(cost).getByRole("group", { name: "Research cost" })).toHaveTextContent("No research yet.");
    const reliability = screen.getByRole("region", { name: "Reliability" });
    expect(within(reliability).getByRole("group", { name: "Job success rate" })).toHaveTextContent("80 %");
    expect(within(reliability).getByRole("group", { name: "Retries per episode" })).toHaveTextContent("0.67");
    expect(within(reliability).getByRole("group", { name: "Breaker opens" })).toHaveTextContent("1");
  });

  it("shows the style trials with their score", async () => {
    serve(pageRoutes());
    show("/insights");
    const trials = await screen.findByRole("list", { name: "Style trials" });
    expect(trials).toHaveTextContent("casual · prompt p1");
    expect(trials).toHaveTextContent("Score 63 %");
  });

  it("lays the sections out as the wireframe does, the corrections last", async () => {
    serve(pageRoutes());
    show("/insights");
    await screen.findByRole("region", { name: "Quality" });
    const names = screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"));
    expect(names).toEqual([
      "Quality",
      "By model and prompt version",
      "Style A/B scores",
      "Queue, quota and cost",
      "Reliability",
      "Daily",
      "Your corrections",
    ]);
    expect(screen.getByText("read-only · no model calls")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Your corrections ↓" })).toBeInTheDocument();
  });

  it("says a style with under ten comparisons is not a conclusion yet", async () => {
    serve(pageRoutes({ "/api/metrics?days=30": { body: report({ ab: [{ style: "casual", prompt_version: "p1", variant: 8, current: 4, tie: 0, judged: 12, score: 0.667 }, { style: "plain", prompt_version: "p1", variant: 1, current: 2, tie: 0, judged: 3, score: 0.333 }] }) } }));
    show("/insights");
    const trials = await screen.findByRole("list", { name: "Style trials" });
    const [enough, few] = within(trials).getAllByRole("listitem");
    expect(enough).not.toHaveTextContent("Too few comparisons");
    expect(few).toHaveTextContent("Too few comparisons to conclude (3 of 10 needed)");
  });

  it("gives each day its edit rate, and its cost once the tokens are priced", async () => {
    serve(pageRoutes());
    show("/insights");
    const days = await screen.findByRole("list", { name: "Days" });
    const [newest, oldest] = within(days).getAllByRole("listitem");
    expect(newest).toHaveTextContent("2 eps · 0 failed · edit 3.0 % · 0.070");
    expect(oldest).toHaveTextContent("edit —");
  });

  it("draws the lists as tables from 1024 px", async () => {
    const real = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: true, media: query, onchange: null, addEventListener: () => undefined, removeEventListener: () => undefined, addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
    try {
      serve(pageRoutes());
      show("/insights");
      const models = await screen.findByRole("table", { name: "By model and prompt version" });
      expect(within(models).getByRole("columnheader", { name: "Prompt" })).toBeInTheDocument();
      expect(within(models).getByRole("row", { name: /m-1/ })).toHaveTextContent("1.5 % (3)");
      expect(within(models).getByRole("row", { name: /m-1/ })).toHaveTextContent("3.0 %"); // read too fast
      expect(screen.getByRole("table", { name: "Style trials" })).toHaveTextContent("casual · prompt p1");
      expect(within(screen.getByRole("table", { name: "Days" })).getAllByRole("row")).toHaveLength(3); // head and two days
    } finally {
      window.matchMedia = real;
    }
  });

  it("shows today's calls against the limit that bites first, and the models as working", async () => {
    serve(pageRoutes());
    show("/insights");
    const now = await screen.findByRole("region", { name: "Queue, quota and cost" });
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
    expect(screen.getByText("Nothing has been translated in this window.")).toBeInTheDocument();
    expect(within(quality).getByRole("group", { name: "Cues you corrected" })).not.toHaveTextContent("no previous data");
    expect(within(screen.getByRole("region", { name: "Reliability" })).getByRole("group", { name: "Job success rate" })).toHaveTextContent("—");
    expect(screen.getByText("No variant has been judged cue by cue yet.")).toBeInTheDocument();
  });

  it("asks again for another window", async () => {
    serve(pageRoutes({ "/api/metrics?days=7": { body: report({ window: { days: 7, since: 1, until: 2 } }) } }));
    show("/insights");
    await screen.findByRole("region", { name: "Quality" });
    await userEvent.click(screen.getByRole("radio", { name: "7d" }));
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input) === "/api/metrics?days=7")).toBe(true));
  });

  it("links a series' name to its page by the catalog's id, not the worker's", async () => {
    serve(pageRoutes({ "/api/catalog": { body: { status: {}, languages: ["vi"], series: [{ id: 3, title: "Arcane", series_id: 7 }], movies: {} } } }));
    show("/insights");
    expect(await screen.findByRole("link", { name: "Arcane" })).toHaveAttribute("href", "/series/3/glossary");
  });

  it("says how many cues of each language were corrected", async () => {
    serve(pageRoutes());
    show("/insights");
    expect(await screen.findByText("vi · 14 edits")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open the vi golden set of Arcane" })).toBeEnabled();
  });

  it("switches both buttons off for a language nobody corrected, and lists the most corrected series first", async () => {
    serve(
      pageRoutes({
        "/api/metrics?days=30": { body: report({ corrections: [{ series_id: 9, lang: "vi", edits: 5 }] }) },
        "/api/library/series": { body: { items: [{ id: 7, key: "bazarr-7", title: "Arcane", units: 3 }, { id: 9, key: "bazarr-9", title: "Severance", units: 2 }] } },
        "/api/library/series/9/units": units,
      }),
    );
    show("/insights");
    expect(await screen.findByText("vi · 5 edits")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Series" });
    expect(within(list).getAllByRole("listitem").map((item) => item.getAttribute("aria-label"))).toEqual(["Severance", "Arcane"]);
    expect(screen.getByRole("button", { name: "Look at my vi edits of Arcane" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Open the vi golden set of Arcane" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Open the vi golden set of Severance" })).toBeEnabled();
  });

  it("looks at the edits of a series for free", async () => {
    serve(
      pageRoutes({
        "/api/suggestions/from-edits": {
          body: { cues: 12, without_before: 0, patterns: 1, proposed: 1, unnamed: 0, report: { added: 1, pending_already: 0, accepted_before: 0, rejected_before: 0, in_glossary: 0 } },
        },
      }),
    );
    show("/insights");
    await userEvent.click(await screen.findByRole("button", { name: "Look at my vi edits of Arcane" }));
    expect(await screen.findByText(/Looked at 12 corrected cues\. 1 repeated change; 1 new suggestion in Review\./)).toBeInTheDocument();
    expect(sent("/api/suggestions/from-edits")).toEqual([{ series_id: 7, lang: "vi" }]);
  });

  describe("the golden set sheet", () => {
    const golden = { golden_version: 1, series: "bazarr-7", lang: "vi", cues: [{ season: 1, episode: 1, cue: 3, source: "Hi", text: "Chào" }] };
    const routes = () => pageRoutes({ "/api/series/7/golden?lang=vi": { body: golden } });

    it("opens from the Golden set button and offers the file as a link the worker serves, not a blob", async () => {
      serve(routes());
      show("/insights");
      await userEvent.click(await screen.findByRole("button", { name: "Open the vi golden set of Arcane" }));
      const link = await screen.findByRole("link", { name: "Download bazarr-7.vi.golden.json" });
      expect(link).toHaveAttribute("href", "/api/series/7/golden?lang=vi&download=1");
      expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).startsWith("/api/series/7/golden"))).toBe(false); // opening downloads nothing
    });

    it("copies the same text to the clipboard and says how many cues", async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
      serve(routes());
      show("/insights");
      await userEvent.click(await screen.findByRole("button", { name: "Open the vi golden set of Arcane" }));
      await userEvent.click(await screen.findByRole("button", { name: "Copy" }));
      expect(await screen.findByText("Copied 1 cues.")).toBeInTheDocument();
      expect(writeText).toHaveBeenCalledWith(JSON.stringify(golden, null, 2));
    });

    it("shows a box to copy by hand when the clipboard is refused", async () => {
      Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) }, configurable: true });
      serve(routes());
      show("/insights");
      await userEvent.click(await screen.findByRole("button", { name: "Open the vi golden set of Arcane" }));
      await userEvent.click(await screen.findByRole("button", { name: "Copy" }));
      const box = await screen.findByRole("textbox", { name: "Golden set" });
      expect((box as HTMLTextAreaElement).value).toContain('"golden_version": 1');
    });

    it("says so, in red, when the set cannot be read", async () => {
      serve(pageRoutes({ "/api/series/7/golden?lang=vi": { status: 400, body: { error: "no such language" } } }));
      show("/insights");
      await userEvent.click(await screen.findByRole("button", { name: "Open the vi golden set of Arcane" }));
      await userEvent.click(await screen.findByRole("button", { name: "Copy" }));
      expect(await screen.findByText("Could not copy: no such language")).toBeInTheDocument();
    });
  });

  it("tells the admin when the figures cannot be read", async () => {
    serve(pageRoutes({ "/api/metrics?days=30": { status: 500, body: { error: "the database is busy" } } }));
    show("/insights");
    // A failure that is not the client's is tried once more before it is shown.
    expect(await screen.findByText("the database is busy", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});
