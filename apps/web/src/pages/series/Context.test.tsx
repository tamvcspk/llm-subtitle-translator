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

  it("asks the price of Extract facts, and reads every missing episode with one request from the last of them", async () => {
    serveMemory(memory(), {
      "/api/units/504/context/extract": { body: { ok: true, extracted: ["S01E02", "S01E04"], results: { S01E02: "extracted", S01E04: "extracted" } } },
    });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    const sheet = await screen.findByRole("dialog", { name: "Extract facts" });
    expect(within(sheet).getByText("Model calls (est.)").nextSibling).toHaveTextContent("2");
    expect(within(sheet).getByText(/One call per episode/)).toBeInTheDocument();
    expect(sent("/api/units/504/context/extract")).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText("The facts are stored.")).toBeInTheDocument();
    expect(within(sheet).getByText("S01E02 read · S01E04 read")).toBeInTheDocument();
    expect(sent("/api/units/504/context/extract")).toHaveLength(1);
  });

  it("says which episode failed and shows a refusal of the provider", async () => {
    serveMemory(memory(), {
      "/api/units/504/context/extract": { body: { ok: true, extracted: [], results: { S01E02: "failed", S01E04: "no-source" } } },
    });
    show("/series/1/context");
    await userEvent.click(await screen.findByRole("button", { name: "Extract facts (2 episodes)" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Some episodes could not be read.")).toBeInTheDocument();
    expect(screen.getByText("S01E02 failed · S01E04 no source subtitle")).toBeInTheDocument();
  });

  it("shows the error of a provider that refuses and keeps the box open to cancel", async () => {
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
