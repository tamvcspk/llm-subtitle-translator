import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bootstrapState, episode, seriesPage, target } from "../test/fixtures";
import { sent, serve, show } from "../test/render";

const base = {
  "/api/status": { body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } } },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/library/series/9/units": { body: { languages: ["vi"], units: [] } },
};

const held = target({ reason: "bootstrap", text: "Missing · Waiting for glossary review" });
const lockedWith = (overrides = {}) =>
  bootstrapState({ locked: true, limit: 2, pending: 0, held: 99, held_by_lang: { vi: 99 }, no_source: 0, glossary_empty: true, ...overrides });

const estimate = {
  jobs: 99, cues: 48210, calls: 99, calls_left_today: 1000, paused_until: null, minutes: 28, finish_at: 1_800_000_000,
  extract_calls: 99, extract_model: "lite-1", summarize_calls: 96, summarize_model: "lite-2", model: "big-1",
};
const price = (overrides = {}) => ({
  ok: true, released: false, estimate_only: true, estimate, planned_total: 99, skipped_total: 0, skipped_by_reason: {},
  glossary_empty: true, daily_cap: 0, cap_left: null, ...overrides,
});

afterEach(() => vi.unstubAllGlobals());

describe("A series whose auto-request is waiting for its glossary (FR-15.12)", () => {
  it("says how many episodes it holds in this language, what was requested, and still lets the admin translate the held one", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": {
        body: seriesPage([episode(1, { targets: [target({ job: "requested", text: "Missing · Requested" })] }), episode(3, { targets: [held] })], {
          bootstrap: lockedWith({ pending: 4, held: 1, held_by_lang: { vi: 1 }, no_source: 22 }),
        }),
      },
    });
    show("/series/1/episodes");
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Auto-request is holding 1 episode for the glossary");
    expect(notice).toHaveTextContent("Only the first 2 episodes with source subtitles were requested (22 other episodes have no source subtitle yet).");
    expect(notice).toHaveTextContent("4 suggestions wait for your review");
    expect(within(notice).getByRole("link", { name: "Review suggestions (4)" })).toHaveAttribute("href", "/series/1/suggestions");
    const row = screen.getByText("S01E03").closest("li") as HTMLElement;
    expect(within(row).getByText("Missing · Waiting for glossary review")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Translate S01E03" })).toBeInTheDocument(); // only the policy waits
  });

  it.each([
    ["a job is running", { open_jobs: 2 }, "The first episodes are being translated", "View in Activity", "/activity"],
    ["Story memory is off", { memory_on: false }, "Story memory is off for this series", "Open Glossary", "/series/1/glossary"],
    ["there is nothing to review", {}, "There are no suggestions to review", "Open Glossary", "/series/1/glossary"],
  ])("says the truth when %s, with the button that fits", async (_name, state, sentence, button, href) => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(3, { targets: [held] })], { bootstrap: lockedWith(state) }) } });
    show("/series/1/episodes");
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent(sentence);
    expect(notice).not.toHaveTextContent("until the suggestions are reviewed"); // the old promise it could not keep
    expect(within(notice).getByRole("link", { name: button })).toHaveAttribute("href", href);
    expect(within(notice).queryByRole("link", { name: /Review suggestions/ })).toBeNull();
  });

  it("asks the price first, warns about an empty glossary, and only Confirm lets the rest go", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": { body: seriesPage([episode(3, { targets: [held] })], { bootstrap: lockedWith() }) },
      "/api/catalog/series/1/bootstrap": (body: Record<string, unknown> | null) =>
        body?.estimate_only === true ? { body: price({ daily_cap: 50, cap_left: 40 }) } : { body: { ok: true, released: true, started: true } },
    });
    show("/series/1/episodes");
    const notice = await screen.findByRole("status");
    await userEvent.click(within(notice).getByRole("button", { name: "Auto-request 99 without glossary…" }));
    const sheet = await screen.findByRole("dialog", { name: "Auto-request remaining episodes · Arcane" });
    expect(await within(sheet).findByText("Episodes to release")).toBeInTheDocument();
    expect(within(sheet).getByText("Translate (est.)").nextSibling).toHaveTextContent("99 calls · big-1");
    expect(within(sheet).getByText("Extract facts").nextSibling).toHaveTextContent("99 calls · lite-1");
    expect(within(sheet).getByText("Condense story").nextSibling).toHaveTextContent("96 calls · lite-2");
    expect(within(sheet).getByText("Total calls (est.)").nextSibling).toHaveTextContent("294");
    expect(within(sheet).getByText("up to 40 today · daily cap 50")).toBeInTheDocument();
    expect(within(sheet).getByText("Glossary is empty")).toBeInTheDocument();
    expect(sent("/api/catalog/series/1/bootstrap")).toEqual([{ released: true, estimate_only: true }]); // looking costs nothing and changes nothing
    await userEvent.click(within(sheet).getByRole("button", { name: "Release & auto-request (99)" }));
    await waitFor(() => expect(sent("/api/catalog/series/1/bootstrap")).toEqual([{ released: true, estimate_only: true }, { released: true }]));
    expect(await within(sheet).findByText("Released. The rest is being asked for now; follow it in Activity.")).toBeInTheDocument();
  });

  it("does not warn when something is approved, and says when the rest waits for the next sync", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": { body: seriesPage([episode(3, { targets: [held] })], { bootstrap: lockedWith({ glossary_empty: false }) }) },
      "/api/catalog/series/1/bootstrap": (body: Record<string, unknown> | null) =>
        body?.estimate_only === true ? { body: price({ glossary_empty: false }) } : { body: { ok: true, released: true, started: false } },
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Auto-request remaining 99…" }));
    const sheet = await screen.findByRole("dialog");
    await within(sheet).findByText("Episodes to release");
    expect(within(sheet).queryByText("Glossary is empty")).toBeNull();
    await userEvent.click(within(sheet).getByRole("button", { name: /Release & auto-request/ }));
    expect(await within(sheet).findByText("Released. Starts with the next library sync (a few minutes).")).toBeInTheDocument();
  });

  it("shows nothing when the series is not held", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)]) } });
    show("/series/1/episodes");
    await screen.findByRole("heading", { name: "Arcane" });
    expect(screen.queryByText(/Auto-request is holding/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Hold again" })).toBeNull();
  });
});

describe("Hold again (FR-15.12, D-07)", () => {
  const released = (overrides = {}) => bootstrapState({ released: true, relockable: true, queued_policy_jobs: 14, ...overrides });

  it("is offered after the series was let go with nothing approved, and says how many auto-requests are in line", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { bootstrap: released() }) } });
    show("/series/1/episodes");
    expect(await screen.findByText("Glossary hold is off (14 auto-requests in line).")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hold again" })).toBeInTheDocument();
  });

  it("is not offered when holding would hold nothing (the server says it is not relockable)", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { bootstrap: released({ relockable: false }) }) } });
    show("/series/1/episodes");
    await screen.findByRole("heading", { name: "Arcane" });
    expect(screen.queryByRole("button", { name: "Hold again" })).toBeNull();
  });

  it("asks what to do with the queued jobs, cancelling them by default, and says it was done", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": { body: seriesPage([episode(1)], { bootstrap: released() }) },
      "/api/catalog/series/1/bootstrap": { body: { ok: true, released: false, cancelled: 14 } },
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Hold again" }));
    const sheet = await screen.findByRole("dialog", { name: "Hold auto-request for Arcane?" });
    const box = within(sheet).getByRole("checkbox", { name: /Also cancel 14 queued auto-request jobs/ });
    expect(box).toBeChecked();
    expect(sent("/api/catalog/series/1/bootstrap")).toEqual([]); // not yet
    await userEvent.click(within(sheet).getByRole("button", { name: "Hold again" }));
    await waitFor(() => expect(sent("/api/catalog/series/1/bootstrap")).toEqual([{ released: false, cancel_queued_policy: true }]));
  });

  it("keeps the queued jobs when the box is unticked", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": { body: seriesPage([episode(1)], { bootstrap: released() }) },
      "/api/catalog/series/1/bootstrap": { body: { ok: true, released: false, cancelled: 0 } },
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Hold again" }));
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("checkbox"));
    await userEvent.click(within(sheet).getByRole("button", { name: "Hold again" }));
    await waitFor(() => expect(sent("/api/catalog/series/1/bootstrap")).toEqual([{ released: false, cancel_queued_policy: false }]));
  });

  it("holds at once, with no sheet, when nothing is queued", async () => {
    serve({
      ...base,
      "/api/catalog/series/1": { body: seriesPage([episode(1)], { bootstrap: released({ queued_policy_jobs: 0 }) }) },
      "/api/catalog/series/1/bootstrap": { body: { ok: true, released: false, cancelled: 0 } },
    });
    show("/series/1/episodes");
    await userEvent.click(await screen.findByRole("button", { name: "Hold again" }));
    await waitFor(() => expect(sent("/api/catalog/series/1/bootstrap")).toEqual([{ released: false, cancel_queued_policy: false }]));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
