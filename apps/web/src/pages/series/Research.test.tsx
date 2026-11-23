import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { episode, series, seriesPage } from "../../test/fixtures";
import { sent, serve, show } from "../../test/render";

const status = { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 };

const doc = { version: 1, target_lang: "vi", terms: [], characters: [], address_rules: [] };

const base = {
  "/api/status": {
    body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
  },
  "/api/catalog/status": { body: status },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog/series/1": { body: seriesPage([episode(1), episode(2)]) },
  "/api/catalog": {
    body: { status, languages: ["vi"], series: [series({ id: 1, title: "Arcane", series_id: 9 })], movies: { count: 0, missing: {}, failed: {}, ignored: {} } },
  },
  "/api/series/9/glossary?lang=vi": { body: { lang: "vi", exists: false, doc, yaml: "", version: 0, stale_units: 0 } },
};

const estimate = (over: Record<string, unknown> = {}) => ({
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
  calls_left_today: 7,
  paused_until: null,
  minutes: null,
  finish_at: null,
  ...over,
});

const job = (over: Record<string, unknown> = {}) => ({
  job_id: "research:9:vi-1", kind: "research", title: "Arcane", label: "", stored_series_id: 9, target_code: "vi", origin: "manual", mode: "research", created_at: 100, ...over,
});

const queue = (lists: Record<string, unknown[]> = {}) => ({
  "/api/queue": {
    body: { counts: {}, total: 0, daily_quota_pause: null, providers: [], pending: [], processing: [], deferred: [], done: [], failed: [], cancelled: [], ...lists },
  },
});

/** What the worker answers at `/api/suggestions/research`: the price to a request with `estimate_only`, the job otherwise. */
const research = (price = estimate(), queued = { ok: true, job_ids: ["research:9:vi-1"], queued: 1, merged: 0 }) => ({
  "/api/suggestions/research": (body: Record<string, unknown> | null) => ({ body: body?.estimate_only ? price : queued }),
});

const asked = () => sent("/api/suggestions/research").filter((body) => body !== null && body.estimate_only !== true);

afterEach(() => vi.unstubAllGlobals());

describe("Research terms on the Glossary tab", () => {
  it("counts the terms in the button and opens a box that prices the run before anything is queued", async () => {
    serve({ ...base, ...queue(), ...research() });
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Research terms (3)" }));
    const sheet = await screen.findByRole("dialog", { name: "Research terms" });
    expect(within(sheet).getByText("Model calls (est.)").nextSibling).toHaveTextContent("2 (1 search, 1 read)");
    expect(within(sheet).getByText("Series budget").nextSibling).toHaveTextContent("4 → 6 of 10");
    expect(within(sheet).getByText("Calls left today").nextSibling).toHaveTextContent("7");
    expect(within(sheet).getByRole("checkbox", { name: /Auto-accept terms with 2\+ independent sources/ })).toBeChecked();
    expect(asked()).toEqual([]); // looking at the price spends and queues nothing
  });

  it("queues one job on Confirm, with the tick of the box for this run, and says where to follow it", async () => {
    serve({ ...base, ...queue(), ...research() });
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Research terms (3)" }));
    const sheet = await screen.findByRole("dialog", { name: "Research terms" });
    await userEvent.click(within(sheet).getByRole("checkbox", { name: /Auto-accept terms/ })); // untick: this run only
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/Queued\. Follow it in Activity/)).toBeInTheDocument();
    expect(asked()).toEqual([{ series_id: 9, lang: "vi", auto_accept: false }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("is off with its reason when there is nothing to look up, and leaves the glossary editor alone", async () => {
    serve({ ...base, ...queue(), ...research(estimate({ terms: 0, asked: 0, calls: 0, blocked: "no_terms" })) });
    show("/series/1/glossary");
    expect(await screen.findByRole("button", { name: "Research terms (0)" })).toBeDisabled();
    const card = screen.getByRole("region", { name: "Research terms" });
    expect(await within(card).findByText("No unmapped terms found")).toBeInTheDocument();
    expect(within(card).queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    expect(await screen.findByText("No glossary yet")).toBeInTheDocument();
  });

  it("says that research needs Gemini and that a spent budget is raised in Settings, each with a way there", async () => {
    serve({ ...base, ...queue(), ...research(estimate({ blocked: "no_web_search" })) });
    show("/series/1/glossary");
    const card = await screen.findByRole("region", { name: "Research terms" });
    expect(await within(card).findByText(/Web research requires Gemini/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
    expect(within(card).getByRole("button", { name: /^Research terms/ })).toBeDisabled();
  });

  it("follows the job in a strip: Stop while it runs, Open Review when it found something, Retry when it failed", async () => {
    serve({ ...base, ...queue({ processing: [job({ stage: "Looking terms up on the web" })] }), ...research(), "/api/queue/stop": { body: { ok: true, outcome: "stopping" } } });
    show("/series/1/glossary");
    const strip = await screen.findByRole("status", { name: "Research status" });
    expect(within(strip).getByText("Looking terms up on the web…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Research terms (3)" })).toBeDisabled(); // a run is going: a second click would only merge
    await userEvent.click(within(strip).getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(sent("/api/queue/stop")).toEqual([{ job_id: "research:9:vi-1" }]));
  });

  it("sends the person to Review when the run found terms", async () => {
    serve({ ...base, ...queue({ done: [job({ result: "proposed", report: { added: 2, auto_accepted: 1 } })] }), ...research() });
    show("/series/1/glossary");
    const strip = await screen.findByRole("status", { name: "Research status" });
    expect(within(strip).getByText("Done: 2 terms found, 1 applied by themselves.")).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: "Open Review →" })).toHaveAttribute("href", "/review");
    expect(screen.getByRole("button", { name: "Research terms (3)" })).toBeEnabled();
  });

  it("offers Retry after a failure and opens the same priced box", async () => {
    serve({ ...base, ...queue({ failed: [job({ error: "Provider unavailable: boom" })] }), ...research() });
    show("/series/1/glossary");
    const strip = await screen.findByRole("status", { name: "Research status" });
    expect(within(strip).getByText("Failed: Provider unavailable: boom")).toBeInTheDocument();
    await userEvent.click(within(strip).getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("dialog", { name: "Research terms" })).toBeInTheDocument();
  });

  it("keeps quiet about a series another language's job belongs to", async () => {
    serve({ ...base, ...queue({ failed: [job({ target_code: "fr", error: "x" })] }), ...research() });
    show("/series/1/glossary");
    await screen.findByRole("button", { name: "Research terms (3)" });
    expect(screen.queryByRole("status", { name: "Research status" })).not.toBeInTheDocument();
  });
});
