import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { QueueAnswer, QueueJob } from "../api/types";
import { sent, serve, show } from "../test/render";

const job = (id: string, extra: Partial<QueueJob> = {}): QueueJob => ({
  job_id: id,
  title: "Arcane",
  label: "S01E04",
  unit_id: 504,
  target_code: "vi",
  source_code: "en",
  origin: "manual",
  mode: "fill",
  ...extra,
});

const providers = [
  { provider: "gemini", model: "gemini-3.1-flash-lite", state: "closed", reason: "", until: null, failures: 0, usable: true },
  { provider: "gemini", model: "gemini-3.5-flash", state: "exhausted", reason: "daily quota", until: 1_900_000_000, failures: 3, usable: false },
];

function queue(extra: Partial<QueueAnswer> = {}): QueueAnswer {
  return {
    counts: { pending: 1, processing: 1, deferred: 2, done: 441, failed: 3 },
    total: 448,
    daily_quota_pause: null,
    providers: providers as QueueAnswer["providers"],
    pending: [job("req-1", { origin: "policy" })],
    processing: [job("run-1", { progress_done: 120, progress_total: 340, stage: "translating" })],
    deferred: [
      job("wait-1", { label: "S01E05", wait_reason: "context", missing_units: ["S01E04"], origin: "policy" }),
      job("wait-2", { title: "Severance", label: "S01E02", unit_id: 702, wait_reason: "quota", retry_at: 1_900_000_000 }),
    ],
    failed: [
      job("fail-1", { label: "S01E07", error: "Traceback\nboom\nOutput token limit exceeded", attempts: 3 }),
      job("fail-2", { title: "", label: "", unit_id: undefined, subtitle_path: "/media/Old/Old.en.srt", error: "bad" }),
    ],
    done: [job("done-1", { result: "translated", output_path: "/media/Arcane/S01E01.vi.srt" })],
    ...extra,
  };
}

const status = {
  queue: { pending: 1, processing: 1, deferred: 2, done: 441, failed: 3 },
  daily_quota_pause: null,
  providers: [],
  settings: { key_warnings: [] },
  model_layers: {
    by_model: { "gemini-3.1-flash-lite": { done: 412, failed: 8 }, "gemini-3.5-flash": { done: 29, failed: 2 } },
    escalated: { total: 35, done: 29, failed: 2, waiting: 4 },
    window: { done_days: 30, failed_days: 90 },
  },
};

const estimate = (jobs: number) => ({
  body: {
    estimate_only: true,
    estimate: { jobs, cues: jobs * 340, calls: jobs * 9, calls_left_today: null, paused_until: null, minutes: null, finish_at: null },
    planned_total: jobs,
    skipped_total: 0,
    skipped_by_reason: {},
  },
});

function activity(extra: Record<string, unknown> = {}, answer: QueueAnswer = queue()) {
  return {
    "/api/status": { body: status },
    "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 1240 } },
    "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
    "/api/queue": { body: answer },
    ...extra,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Activity", () => {
  it("shows each provider's health and why one is out", async () => {
    serve(activity());
    show("/activity");
    const card = await screen.findByRole("region", { name: "Providers" });
    expect(within(card).getByText("closed · usable · failures 0")).toBeInTheDocument();
    expect(within(card).getByText("exhausted · not usable · failures 3")).toBeInTheDocument();
    expect(within(card).getByText(/daily quota · until/)).toBeInTheDocument();
  });

  it("shows how the two model layers are doing and how far back the counts reach", async () => {
    serve(activity());
    show("/activity");
    const card = await screen.findByRole("region", { name: "Model layers" });
    expect(await within(card).findByText("done 412 · failed 8")).toBeInTheDocument();
    expect(within(card).getByText("Done last 30 days, failed last 90 days. Read only.")).toBeInTheDocument();
    expect(within(card).getByText("Escalated 35: done 29 · failed 2 · waiting 4")).toBeInTheDocument();
  });

  it("lists the background work with its progress and stops one on request", async () => {
    const task = {
      id: "abc123abc123", kind: "extract-facts", title: "Extract facts · Arcane (3 episodes)", series_id: 9, state: "running",
      done: 1, total: 3, message: "Reading S01E02", result: null, error: "", created_at: 1, started_at: 2, finished_at: null, cancel_requested: false,
    };
    const failed = { ...task, id: "def456def456", title: "Look for proposals · Arcane", state: "failed", error: "QuotaExhausted: spent", finished_at: 5 };
    serve(activity({ "/api/tasks": { body: { items: [task, failed] } }, "/api/tasks/abc123abc123/cancel": { body: { ...task, cancel_requested: true } } }));
    show("/activity");
    const card = await screen.findByRole("region", { name: "Background work" });
    expect(within(card).getByText("Extract facts · Arcane (3 episodes)")).toBeInTheDocument();
    expect(within(card).getByText("Reading S01E02 · 1 of 3")).toBeInTheDocument();
    expect(within(card).getByText("Failed · 1 of 3")).toBeInTheDocument();
    expect(within(card).getByText("QuotaExhausted: spent")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "Stop Look for proposals · Arcane" })).not.toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Stop Extract facts · Arcane (3 episodes)" }));
    await waitFor(() => expect(sent("/api/tasks/abc123abc123/cancel")).toHaveLength(1));
  });

  it("has no card for background work when there is none, or when the worker does not know it", async () => {
    serve(activity());
    show("/activity");
    await screen.findByRole("region", { name: "Providers" });
    expect(screen.queryByRole("region", { name: "Background work" })).not.toBeInTheDocument();
  });

  it("shows the catalog and syncs it on request", async () => {
    serve(activity({ "/api/catalog/sync": { body: { ok: true } } }));
    show("/activity");
    const card = await screen.findByRole("region", { name: "Catalog" });
    expect(await within(card).findByText(/Synced never · ok · 1,240 items/)).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(sent("/api/catalog/sync")).toHaveLength(1));
  });

  it("offers to accept a read the shrink guard set aside, saying how many items it drops, and only sends it after Confirm", async () => {
    serve(activity({
      "/api/catalog/status": {
        body: { synced_at: 1, attempted_at: 2, status: "kept", warning: "set aside", items: 10, shrink: { listed: 7, dropped: 3 } },
      },
      "/api/catalog/sync": { body: { ok: true } },
    }));
    show("/activity");
    const card = await screen.findByRole("region", { name: "Catalog" });
    await userEvent.click(await within(card).findByRole("button", { name: "Accept smaller library" }));
    expect(await screen.findByText(/listed 7 items, 3 fewer than before/)).toBeInTheDocument();
    expect(sent("/api/catalog/sync")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Read again and accept" }));
    await waitFor(() => expect(sent("/api/catalog/sync")).toEqual([{ accept_shrink: true }]));
  });

  it("has no such button when the catalog was not kept because of a shrink", async () => {
    serve(activity());
    show("/activity");
    const card = await screen.findByRole("region", { name: "Catalog" });
    await within(card).findByText(/items/);
    expect(within(card).queryByRole("button", { name: "Accept smaller library" })).not.toBeInTheDocument();
  });

  it("labels the tabs with the real counts and opens on what is running", async () => {
    serve(activity());
    show("/activity");
    expect(await screen.findByRole("tab", { name: "Running 1" })).toHaveAttribute("aria-selected", "true");
    for (const name of ["Requested 1", "Waiting 2", "Failed 3", "Done 441"]) expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Running jobs" });
    expect(within(list).getByRole("link", { name: "Arcane · S01E04 · vi" })).toHaveAttribute("href", "/unit/504/subtitles?lang=vi");
    expect(within(list).getByText("manual · fill · translating 120/340")).toBeInTheDocument();
  });

  it("stops a running job and cancels a requested one, with no price box", async () => {
    serve(activity({ "/api/queue/stop": { body: { ok: true, outcome: "stopping" } } }));
    show("/activity");
    await userEvent.click(await screen.findByRole("button", { name: "Stop Arcane · S01E04 · vi" }));
    await waitFor(() => expect(sent("/api/queue/stop")).toEqual([{ job_id: "run-1" }]));
    await userEvent.click(screen.getByRole("tab", { name: "Requested 1" }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel Arcane · S01E04 · vi" }));
    await waitFor(() => expect(sent("/api/queue/stop")).toHaveLength(2));
    expect(sent("/api/queue/stop")[1]).toEqual({ job_id: "req-1" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("says what each waiting job waits for", async () => {
    serve(activity());
    show("/activity?tab=waiting");
    const list = await screen.findByRole("list", { name: "Waiting jobs" });
    expect(within(list).getByText("policy · fill · Waiting · context (needs S01E04)")).toBeInTheDocument();
    expect(within(list).getByText(/manual · fill · Waiting · quota · retry at/)).toBeInTheDocument();
    // Only a context wait has a button.
    expect(within(list).getAllByRole("button")).toHaveLength(1);
  });

  it("asks the price of the earlier episode before it requests it", async () => {
    serve(
      activity({
        "/api/queue/request-prerequisites": (body: Record<string, unknown> | null) =>
          body?.estimate_only ? estimate(1) : { body: { requested: ["new-job"], unavailable: [] } },
      }),
    );
    show("/activity?tab=waiting");
    await userEvent.click(await screen.findByRole("button", { name: /Request that episode for/ }));
    const sheet = await screen.findByRole("dialog", { name: "Request S01E04" });
    expect(await within(sheet).findByText("340")).toBeInTheDocument();
    expect(sent("/api/queue/request-prerequisites")).toEqual([{ job_id: "wait-1", estimate_only: true }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/1 job is in line/)).toBeInTheDocument();
    expect(sent("/api/queue/request-prerequisites")[1]).toEqual({ job_id: "wait-1" });
  });

  it("shows why a job failed, names an older job by its file, and retries through the price box", async () => {
    serve(
      activity({
        "/api/queue/retry": (body: Record<string, unknown> | null) => (body?.estimate_only ? estimate(1) : { body: { ok: true } }),
      }),
    );
    show("/activity?tab=failed");
    const list = await screen.findByRole("list", { name: "Failed jobs" });
    expect(within(list).getByText("manual · fill · Failed · Output token limit exceeded · restarts 3")).toBeInTheDocument();
    expect(within(list).getByText("Old.en.srt · vi")).toBeInTheDocument();
    expect(within(list).getByText("Full error")).toBeInTheDocument();

    await userEvent.click(within(list).getByRole("button", { name: "Retry Arcane · S01E07 · vi" }));
    const sheet = await screen.findByRole("dialog", { name: "Retry · Arcane · S01E07 · vi" });
    expect(await within(sheet).findByText("340")).toBeInTheDocument();
    expect(sent("/api/queue/retry")).toEqual([{ job_id: "fail-1", estimate_only: true }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/queue/retry")[1]).toEqual({ job_id: "fail-1" }));
  });

  it("retries every failed job after the price, and says how many are back in line", async () => {
    serve(
      activity({
        "/api/queue/retry": (body: Record<string, unknown> | null) => (body?.estimate_only ? estimate(3) : { body: { ok: true, count: 3, skipped: 0 } }),
      }),
    );
    show("/activity");
    await userEvent.click(await screen.findByRole("button", { name: "Retry all failed" }));
    const sheet = await screen.findByRole("dialog", { name: "Retry all failed" });
    expect(await within(sheet).findByText("1,020")).toBeInTheDocument();
    expect(sent("/api/queue/retry")).toEqual([{ all: true, estimate_only: true }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/3 jobs are in line/)).toBeInTheDocument();
    expect(sent("/api/queue/retry")[1]).toEqual({ all: true });
  });

  it("has no Retry all failed when nothing failed", async () => {
    serve(activity({}, queue({ counts: { pending: 0, processing: 0, deferred: 0, done: 1, failed: 0 }, failed: [], pending: [], processing: [], deferred: [] })));
    show("/activity");
    expect(await screen.findByRole("button", { name: "Retry all failed" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Done 1" })).toHaveAttribute("aria-selected", "true");
  });

  it("says when only the latest jobs are listed", async () => {
    serve(activity());
    show("/activity?tab=done");
    expect(await screen.findByText("Showing the latest 1 of 441.")).toBeInTheDocument();
    expect(screen.getByText("manual · fill · translated · wrote S01E01.vi.srt")).toBeInTheDocument();
  });

  it("exports a job spec as a server link and as text to copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    serve(activity({ "/api/jobs/fail-1/spec": { body: { cues: [{ n: 1 }], model: "m" } } }));
    show("/activity?tab=failed");
    await userEvent.click(await screen.findByRole("button", { name: "Export job spec of Arcane · S01E07 · vi" }));
    const sheet = await screen.findByRole("dialog", { name: "Export job spec" });
    const link = await within(sheet).findByRole("link", { name: "Download job spec" });
    expect(link).toHaveAttribute("href", "/api/jobs/fail-1/spec");
    expect(link).toHaveAttribute("download", "job-fail-1.json");
    expect((within(sheet).getByRole("textbox", { name: "Job spec" }) as HTMLTextAreaElement).value).toContain('"model": "m"');
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy spec" }));
    expect(writeText).toHaveBeenCalledOnce();
    expect(await within(sheet).findByText("Copied.")).toBeInTheDocument();
  });

  it("says so when a job has not translated yet and so has no spec", async () => {
    serve(activity({ "/api/jobs/fail-1/spec": { status: 404, body: { error: "this job has no bundle yet: it has not translated" } } }));
    show("/activity?tab=failed");
    await userEvent.click(await screen.findByRole("button", { name: "Export job spec of Arcane · S01E07 · vi" }));
    expect(await screen.findByText("this job has no bundle yet: it has not translated")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download job spec" })).not.toBeInTheDocument();
  });
});
