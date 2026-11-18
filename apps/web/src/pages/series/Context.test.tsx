import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { episode, seriesPage } from "../../test/fixtures";
import { sent, serve, show } from "../../test/render";

const base = {
  "/api/status": {
    body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
  },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog/series/1": { body: seriesPage([episode(1), episode(2)]) },
};

/** An episode of the series: its id is 500 + its number in season 1. */
const unit = (id: number, has_source: boolean, has_facts: boolean) => ({
  id,
  label: `S01E0${id - 500}`,
  season: 1,
  episode: id - 500,
  has_source,
  has_facts,
  facts_hash: has_facts ? "abc" : "",
});

function memory(overrides: Record<string, unknown> = {}, units = [unit(501, true, true), unit(502, true, false), unit(503, false, false), unit(504, true, false)]) {
  return { series: { id: 9, title: "Arcane" }, policy: "", specials_after: "", global_enabled: true, units, ...overrides };
}

/** A background task of this series, running unless `over` says otherwise. */
const task = (over: Record<string, unknown> = {}) => ({
  id: "abc123abc123", kind: "extract-facts", title: "Extract facts · Arcane (2 episodes)", series_id: 9, state: "running",
  done: 1, total: 2, message: "Reading S01E04", result: null, error: "", created_at: 1, started_at: 2, finished_at: null,
  cancel_requested: false, ...over,
});

/** What starting an extraction answers: the task that was made. */
const extract = () => ({ "/api/units/504/context/extract": { body: { ok: true, task: task({ state: "queued", done: 0, total: 0, message: "" }) } } });

function serveMemory(initial = memory(), extra: Record<string, unknown> = {}) {
  serve({
    ...base,
    "/api/series/9/memory": (body: Record<string, unknown> | null) => ({ body: body === null ? initial : { ...initial, ...body } }),
    ...extra,
  });
}

describe("Series header: Story memory and Specials after", () => {
  it("shows the series' switch as Default, with Apply only once something changed", async () => {
    serveMemory();
    show("/series/1/episodes");
    const group = await screen.findByRole("radiogroup", { name: "Story memory" });
    expect(within(group).getByRole("radio", { name: "Default" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: /Specials after/ })).toHaveValue("");
    expect(screen.getByText(/Existing translations are never redone/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });

  it("saves the switch and the anchor together with Apply, and nothing before", async () => {
    serveMemory();
    show("/series/1/episodes");
    // "On" is also a choice of Auto-request: pick the one of Story memory.
    const memory = await screen.findByRole("radiogroup", { name: "Story memory" });
    await userEvent.click(within(memory).getByRole("radio", { name: "On" }));
    await userEvent.type(screen.getByRole("textbox", { name: /Specials after/ }), "s01e02");
    expect(sent("/api/series/9/memory").filter((body) => body !== null)).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(sent("/api/series/9/memory").filter((body) => body !== null)).toEqual([{ policy: "on", specials_after: "S01E02" }]));
  });

  it("refuses an anchor the worker would refuse, before it is sent", async () => {
    serveMemory();
    show("/series/1/episodes");
    await userEvent.type(await screen.findByRole("textbox", { name: /Specials after/ }), "2x05");
    expect(screen.getByText("Use a form like S02E05")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("puts the changes back with Discard", async () => {
    serveMemory(memory({ policy: "off", specials_after: "S01E09" }));
    show("/series/1/episodes");
    const group = await screen.findByRole("radiogroup", { name: "Story memory" });
    expect(within(group).getByRole("radio", { name: "Off" })).toBeChecked();
    await userEvent.click(within(group).getByRole("radio", { name: "On" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(within(group).getByRole("radio", { name: "Off" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: /Specials after/ })).toHaveValue("S01E09");
  });

  it("is quiet about a series the worker does not keep", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { series_id: null }) } });
    show("/series/1/episodes");
    await screen.findByRole("heading", { name: "Arcane" });
    expect(screen.queryByRole("radiogroup", { name: "Story memory" })).not.toBeInTheDocument();
  });
});

describe("Series Context tab", () => {
  it("lists the episodes in story order with their source and facts, and counts the ones without facts", async () => {
    serveMemory();
    show("/series/1/context");
    const list = await screen.findByRole("list", { name: "Episodes" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getByRole("link", { name: "1 · S01E01" })).toHaveAttribute("href", "/unit/501/context?lang=vi");
    expect(within(rows[0]!).getByText("Source ✓ · Facts ✓")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Source ✗ · Facts ✗")).toBeInTheDocument();
    expect(screen.getByText("2 episodes have a source subtitle but no facts.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Extract facts (2 episodes)" })).toBeEnabled();
  });

  it("asks the price of Extract facts, starts it as a background task and follows it until it is stored", async () => {
    const finished = task({ state: "done", done: 2, message: "", finished_at: 9, result: { extracted: ["S01E02", "S01E04"], results: { S01E02: "extracted", S01E04: "extracted" } } });
    serveMemory(memory(), { ...extract(), "/api/tasks": { body: { items: [finished] } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    const sheet = await screen.findByRole("dialog", { name: "Extract facts" });
    expect(within(sheet).getByText("Model calls (est.)").nextSibling).toHaveTextContent("2");
    expect(within(sheet).getByText(/One call per episode/)).toBeInTheDocument();
    expect(sent("/api/units/504/context/extract")).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText("The facts are stored.")).toBeInTheDocument();
    expect(within(sheet).getByText("S01E02 read · S01E04 read")).toBeInTheDocument();
    expect(sent("/api/units/504/context/extract")).toEqual([{ unit_ids: [502, 504], background: true }]);
    expect(within(sheet).getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("shows how far a running task is, and Run in background closes the box while it goes on", async () => {
    serveMemory(memory(), { ...extract(), "/api/tasks": { body: { items: [task()] } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    const sheet = await screen.findByRole("dialog", { name: "Extract facts" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText("Reading S01E04 · 1 of 2")).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // the series' own card still shows it, with Stop
    const card = screen.getByRole("region", { name: "Story memory" });
    expect(await within(card).findByText("Reading S01E04 · 1 of 2")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /^Stop / })).toBeInTheDocument();
  });

  it("Run in background in the price box starts the task and closes the box at once", async () => {
    serveMemory(memory(), { ...extract(), "/api/tasks": { body: { items: [task()] } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    const sheet = await screen.findByRole("dialog", { name: "Extract facts" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(sent("/api/units/504/context/extract")).toEqual([{ unit_ids: [502, 504], background: true }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("reads only the episodes that were ticked, and only ones that can be read can be ticked", async () => {
    serveMemory(memory(), extract());
    show("/series/1/context");
    expect(await screen.findByRole("checkbox", { name: "Read the facts of S01E01" })).toBeDisabled(); // it has its facts
    expect(screen.getByRole("checkbox", { name: "Read the facts of S01E03" })).toBeDisabled(); // it has no source subtitle
    expect(screen.queryByRole("button", { name: /Extract ticked/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Read the facts of S01E04" }));
    await userEvent.click(screen.getByRole("button", { name: "Extract ticked (1)" }));
    const sheet = await screen.findByRole("dialog", { name: "Extract facts" });
    expect(within(sheet).getByText("Model calls (est.)").nextSibling).toHaveTextContent("1");
    await userEvent.click(within(sheet).getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(sent("/api/units/504/context/extract")).toEqual([{ unit_ids: [504], background: true }]));
  });

  it("clears the ticks", async () => {
    serveMemory(memory(), extract());
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Read the facts of S01E02" }));
    expect(screen.getByRole("button", { name: "Extract ticked (1)" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear ticks" }));
    expect(screen.queryByRole("button", { name: /Extract ticked/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Read the facts of S01E02" })).not.toBeChecked();
  });

  it("says which episode failed", async () => {
    const finished = task({ state: "done", done: 2, finished_at: 9, result: { extracted: [], results: { S01E02: "failed", S01E04: "no-source" } } });
    serveMemory(memory(), { ...extract(), "/api/tasks": { body: { items: [finished] } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Some episodes could not be read.")).toBeInTheDocument();
    expect(screen.getByText("S01E02 failed · S01E04 no source subtitle")).toBeInTheDocument();
  });

  it("shows why a task failed (the provider refused) in the box", async () => {
    const refused = task({ state: "failed", done: 0, error: "The provider could not finish (QuotaExhausted: spent)", finished_at: 9 });
    serveMemory(memory(), { ...extract(), "/api/tasks": { body: { items: [refused] } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("The provider could not finish (QuotaExhausted: spent)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("opens the facts of an episode and says what they hold", async () => {
    const facts = {
      label: "S01E01", has_source: true, stored: true,
      facts: {
        characters: [{ name: "Lois", aliases: ["Mom"], gender: "f", age_hint: "40s", role: "the mother" }],
        relationship_events: [{ a: "Lois", b: "Hal", relation: "romantic", intimacy: 4, hierarchy: "equal", cue: 12, evidence: "I love you", confidence: 0.9, detail: "husband and wife" }],
        terms: [{ source: "Marlin Academy", kind: "place" }],
        summary: "Malcolm is tested.",
      },
    };
    serveMemory(memory(), { "/api/units/501/facts": { body: facts } });
    show("/series/1/context");
    expect(screen.queryByRole("button", { name: "Show the facts of S01E02" })).not.toBeInTheDocument(); // nothing read yet
    await userEvent.click(await screen.findByRole("button", { name: "Show the facts of S01E01" }));
    const panel = await screen.findByLabelText("Facts of S01E01");
    expect(within(panel).getByText("Malcolm is tested.")).toBeInTheDocument();
    expect(within(panel).getByText("Lois")).toBeInTheDocument();
    expect(within(panel).getByText(/also Mom/)).toBeInTheDocument();
    expect(within(panel).getByText("Lois – Hal")).toBeInTheDocument();
    expect(within(panel).getByText(/husband and wife/)).toBeInTheDocument();
    expect(within(panel).getByText(/“I love you”/)).toBeInTheDocument();
    expect(within(panel).getByText("Marlin Academy · place")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Hide the facts of S01E01" }));
    expect(screen.getByRole("button", { name: "Show the facts of S01E01" })).toHaveAttribute("aria-expanded", "false");
  });

  it("shows an error of the worker where the facts would be", async () => {
    serveMemory(memory(), { "/api/units/501/facts": { status: 400, body: { error: "boom" } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Show the facts of S01E01" }));
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });

  it("shows the error of a provider that refuses at once and keeps the box open to cancel", async () => {
    serveMemory(memory(), { "/api/units/504/context/extract": { status: 400, body: { error: "QuotaExhausted: spent" } } });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    const sheet = await screen.findByRole("dialog", { name: "Extract facts" });
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText("QuotaExhausted: spent")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("has nothing to extract when every episode with a source has its facts", async () => {
    serveMemory(memory({}, [unit(501, true, true), unit(502, false, false)]));
    show("/series/1/context");
    expect(await screen.findByText("Every episode with a source subtitle has its facts.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Extract facts (0 episodes)" })).toBeDisabled();
  });

  it("says when story memory is off for the series", async () => {
    serveMemory(memory({ global_enabled: false }));
    show("/series/1/context");
    expect(await screen.findByText(/Story memory is off for this series \(it follows the global setting, which is off\)/)).toBeInTheDocument();
  });

  it("does not say it is off when the series switches it on", async () => {
    serveMemory(memory({ global_enabled: false, policy: "on" }));
    show("/series/1/context");
    await screen.findByRole("list", { name: "Episodes" });
    expect(screen.queryByText(/Story memory is off/)).not.toBeInTheDocument();
  });

  it("says the worker keeps nothing of a series it has not seen", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { series_id: null }) } });
    show("/series/1/context");
    expect(await screen.findByText(/keeps nothing for this series yet/)).toBeInTheDocument();
  });
});
