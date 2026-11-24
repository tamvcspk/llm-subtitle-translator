import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { episode, seriesPage, target } from "../test/fixtures";
import { sent, serve, show } from "../test/render";

const info = { job_id: "job-7", progress_done: 120, progress_total: 340, retry_at: null, error: "", missing_units: [] };

const base = {
  "/api/status": {
    body: {
      queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 },
      daily_quota_pause: null,
      providers: [],
      settings: { key_warnings: [] },
    },
  },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/library/series/9/units": { body: { languages: ["vi"], units: [] } },
};

const estimate = {
  estimate_only: true,
  estimate: { jobs: 2, cues: 900, calls: 5, calls_left_today: null, paused_until: null, minutes: null, finish_at: null, extract_calls: 0, extract_model: "" },
  planned_total: 2,
  skipped_total: 0,
  skipped_by_reason: {},
};

const page = (items: ReturnType<typeof episode>[], overrides = {}) => ({
  "/api/catalog/series/1": { body: seriesPage(items, overrides) },
});

function rowOf(code: string): HTMLElement {
  const item = screen.getByText(code).closest("li");
  if (!item) throw new Error(`no row for ${code}`);
  return item;
}

describe("Episodes", () => {
  it("opens on the Episodes tab and lists the episodes under their season", async () => {
    serve({
      ...base,
      ...page([
        episode(1),
        episode(2, { targets: [target({ job: "running", text: "Missing · Running", job_info: info })] }),
        episode(1, { id: 2001, season: 2, targets: [target({ content: "translated" })] }),
      ]),
    });
    show("/series/1");
    expect(await screen.findByRole("heading", { name: "Arcane" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Season 1" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Season 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Episodes" })).toHaveAttribute("aria-selected", "true");

    expect(within(rowOf("S01E01")).getByRole("button", { name: "Translate S01E01" })).toBeInTheDocument();
    const running = rowOf("S01E02");
    expect(within(running).getByText("Missing · Running 120/340")).toBeInTheDocument();
    expect(within(running).getByRole("button", { name: "Stop S01E02" })).toBeInTheDocument();
    expect(within(rowOf("S02E01")).getByRole("link", { name: "Improve S02E01" })).toHaveAttribute("href", "/series/1/style");
  });

  it("shows the price of one episode before it makes the job", async () => {
    serve({
      ...base,
      ...page([episode(1)]),
      "/api/jobs": (body) => (body?.estimate_only ? { body: estimate } : { body: { ...estimate, estimate_only: false, count: 1 } }),
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Translate S01E01" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("900")).toBeInTheDocument();
    expect(sent("/api/jobs")).toEqual([{ items: [1001], languages: ["vi"], estimate_only: true }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/1 job is in line/)).toBeInTheDocument();
    expect(sent("/api/jobs")[1]).toEqual({ items: [1001], languages: ["vi"] });
  });

  it("translates a season or the whole series, counting only what has nobody on it yet", async () => {
    serve({
      ...base,
      ...page([
        episode(1),
        episode(2),
        episode(3, { targets: [target({ job: "requested", job_info: info })] }),
        episode(1, { id: 2001, season: 2 }),
      ]),
      "/api/jobs": { body: estimate },
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Translate season 1 (2 missing)" }));
    await waitFor(() => expect(sent("/api/jobs")).toHaveLength(1));
    expect(sent("/api/jobs")[0]).toEqual({ series: 1, season: 1, languages: ["vi"], estimate_only: true });
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Translate whole series (3 missing)" })).toBeEnabled();
  });

  it("translates the episodes that were ticked, and a season box ticks them all", async () => {
    serve({ ...base, ...page([episode(1), episode(2), episode(3)]), "/api/jobs": { body: estimate } });
    show("/series/1/episodes");
    await screen.findByRole("region", { name: "Season 1" });
    expect(screen.getByRole("button", { name: "Translate selected" })).toBeDisabled();

    await userEvent.click(screen.getByRole("checkbox", { name: "Select S01E01" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select S01E03" }));
    expect(screen.getByText("Selected 2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Translate selected" }));
    await waitFor(() => expect(sent("/api/jobs")).toHaveLength(1));
    expect(sent("/api/jobs")[0]).toEqual({ items: [1001, 1003], languages: ["vi"], mode: "auto", estimate_only: true });
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));

    await userEvent.click(screen.getByRole("checkbox", { name: "Select all of Season 1" }));
    expect(screen.getByText("Selected 3")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Select all of Season 1" }));
    expect(screen.getByText("Selected 0")).toBeInTheDocument();
  });

  it("shows new vs retranslate breakdown and asks overwrite confirmation before creating retranslate jobs", async () => {
    const mixed = {
      ...estimate,
      mode: "auto",
      translate_total: 1,
      retranslate_total: 1,
      skipped_total: 1,
      skipped_by_reason: { "no source subtitle in a configured source language": 1 },
    };
    serve({
      ...base,
      ...page([episode(1), episode(2, { targets: [target({ content: "translated" })] }), episode(3)]),
      "/api/jobs": (body) => (body?.estimate_only ? { body: mixed } : { body: { ...mixed, estimate_only: false, count: 2 } }),
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select S01E01" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select S01E02" }));
    await userEvent.click(screen.getByRole("button", { name: "Translate selected" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText(/New translations: 1 episode \(no Vietnamese subtitle yet\)/)).toBeInTheDocument();
    expect(within(sheet).getByText(/Retranslations: 1 episode \(already translated; previous file kept as a version\)/)).toBeInTheDocument();
    expect(within(sheet).getByText(/Left out: 1 no source subtitle/)).toBeInTheDocument();

    // First click on Confirm opens the overwrite confirmation step; no jobs created yet.
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(sent("/api/jobs")).toHaveLength(1);
    expect(within(sheet).getByText(/Overwrite 1 translation\?/)).toBeInTheDocument();

    await userEvent.click(within(sheet).getByRole("button", { name: "Overwrite 1 translation" }));
    expect(await within(sheet).findByText(/2 jobs are in line/)).toBeInTheDocument();
    expect(sent("/api/jobs")[1]).toEqual({ items: [1001, 1002], languages: ["vi"], mode: "auto" });
  });

  it("ignores the selection at once, with no estimate, and clears it", async () => {
    serve({ ...base, ...page([episode(1), episode(2)]), "/api/catalog/ignore": { body: { ok: true, changed: 1 } } });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select S01E02" }));
    await userEvent.click(screen.getByRole("button", { name: "Ignore selected" }));
    await waitFor(() => expect(sent("/api/catalog/ignore")).toEqual([{ items: [1002], languages: ["vi"] }]));
    await waitFor(() => expect(screen.getByText("Selected 0")).toBeInTheDocument());
    expect(sent("/api/jobs")).toEqual([]);
  });

  it("cancels or stops a job without an estimate", async () => {
    serve({
      ...base,
      ...page([episode(1, { targets: [target({ job: "requested", job_info: info })] })]),
      "/api/queue/stop": { body: { ok: true, outcome: "cancelled" } },
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Cancel S01E01" }));
    await waitFor(() => expect(sent("/api/queue/stop")).toEqual([{ job_id: "job-7" }]));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("estimates a retry first and puts the failed job back in line only on Confirm", async () => {
    serve({
      ...base,
      ...page([episode(1, { targets: [target({ job: "failed", job_info: { ...info, error: "Output token limit exceeded" } })] })]),
      "/api/queue/retry": (body) => (body?.estimate_only ? { body: estimate } : { body: { ok: true } }),
    });
    show("/series/1/episodes");
    expect(await screen.findByText("Output token limit exceeded")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry S01E01" }));
    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("900");
    expect(sent("/api/queue/retry")).toEqual([{ job_id: "job-7", estimate_only: true }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/1 job is in line/)).toBeInTheDocument();
    expect(sent("/api/queue/retry")[1]).toEqual({ job_id: "job-7" });
  });

  it("lets only the failed episodes through when the address says status=failed", async () => {
    serve({ ...base, ...page([episode(1), episode(2, { targets: [target({ job: "failed", job_info: info })] })]) });
    show("/series/1/episodes?status=failed");
    await screen.findByText("S01E02");
    expect(screen.queryByText("S01E01")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Failed" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Stale" }));
    expect(await screen.findByText(/No episodes are stale here/)).toBeInTheDocument();
  });

  it("saves the auto-request choice the moment it is touched", async () => {
    serve({ ...base, ...page([episode(1)]), "/api/catalog/series/1/policy": { body: { ok: true } } });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("radio", { name: "On" }));
    await waitFor(() => expect(sent("/api/catalog/series/1/policy")).toEqual([{ auto_request: "on" }]));
  });

  it("reads a second target language when the series has two", async () => {
    const both = episode(1, { targets: [target({ lang: "vi" }), target({ lang: "fr", content: "translated" })] });
    serve({ ...base, "/api/catalog/series/1": { body: { ...seriesPage([both]), languages: ["vi", "fr"] } } });
    show("/series/1/episodes?lang=fr");
    expect(await within(await screen.findByText("S01E01").then((el) => el.closest("li")!)).findByText("Translated")).toBeInTheDocument();
  });

  it("says so when the series is not in the catalog", async () => {
    serve({ ...base, "/api/catalog/series/1": { status: 404, body: { error: "no series 1 in the catalog" } } });
    show("/series/1/episodes");
    expect(await screen.findByText("That series is not in the catalog.")).toBeInTheDocument();
  });
});
