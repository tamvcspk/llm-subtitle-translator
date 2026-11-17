import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { episode, series, seriesPage } from "../../test/fixtures";
import { sent, serve, show } from "../../test/render";

const status = { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 };

const base = {
  "/api/status": {
    body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
  },
  "/api/catalog/status": { body: status },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog/series/1": { body: seriesPage([episode(1), episode(2)]) },
  "/api/catalog": {
    body: {
      status,
      languages: ["vi"],
      series: [series({ id: 1, title: "Arcane", series_id: 9 }), series({ id: 4, title: "Cyberpunk", series_id: 12 })],
      movies: { count: 0, missing: {}, failed: {}, ignored: {} },
    },
  },
};

const doc = {
  version: 1,
  target_lang: "vi",
  terms: [{ source: "Enforcer", target: "Cảnh vệ", aliases: ["Enforcers"], locked: true, note: "Piltover police", from: "S01E01" }],
  characters: [{ name: "Vi", target: "Vi" }],
  address_rules: [{ a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị", from: "S01E01" }],
};

const answer = { lang: "vi", exists: true, doc, yaml: "terms:\n- source: Enforcer\n", version: 4, stale_units: 0 };

function glossary(extra: Record<string, unknown> = {}, impact = { stale_now: 0, stale_after: 3, would_stale: 3 }) {
  return {
    ...base,
    "/api/series/9/glossary?lang=vi": (body: Record<string, unknown> | null) =>
      body === null ? { body: answer } : { body: { ok: true, saved: body.dry_run !== true, version: body.dry_run ? null : 5, ...impact } },
    ...extra,
  };
}

const puts = (path = "/api/series/9/glossary?lang=vi") => sent(path).filter((body) => body !== null);

afterEach(() => vi.unstubAllGlobals());

describe("Series Glossary tab", () => {
  it("shows the version, the terms and the forms of address of the stored glossary", async () => {
    serve(glossary());
    show("/series/1/glossary");
    expect(await screen.findByText("Version v4")).toBeInTheDocument();
    const term = screen.getByRole("region", { name: "Term 1" });
    expect(within(term).getByRole("textbox", { name: "Source" })).toHaveValue("Enforcer");
    expect(within(term).getByRole("textbox", { name: "Target" })).toHaveValue("Cảnh vệ");
    expect(within(term).getByRole("checkbox", { name: /Locked/ })).toBeChecked();
    expect(within(term).getByRole("textbox", { name: "From episode" })).toHaveValue("S01E01");
    expect(within(term).getByText("Also matches: Enforcers")).toBeInTheDocument();
    const rule = screen.getByRole("region", { name: "Form of address 1" });
    expect(within(rule).getByRole("textbox", { name: /A calls B/ })).toHaveValue("chị–em");
    expect(screen.getByText("No changes.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    // Characters have a tab of their own, not a sub-tab here.
    expect(screen.queryByRole("heading", { name: "Characters" })).not.toBeInTheDocument();
  });

  it("counts what a change flags, live, and saves the whole document only after a confirmation", async () => {
    serve(glossary());
    show("/series/1/glossary");
    const target = within(await screen.findByRole("region", { name: "Term 1" })).getByRole("textbox", { name: "Target" });
    await userEvent.clear(target);
    await userEvent.type(target, "Cảnh sát");
    expect(await screen.findByText("Saving flags 3 episodes stale.")).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    const dry = puts().at(-1) as { doc: typeof doc; dry_run: boolean };
    expect(dry.dry_run).toBe(true);
    // What the form does not edit (aliases, characters, version) is sent back as it was.
    expect(dry.doc.terms[0]).toMatchObject({ source: "Enforcer", target: "Cảnh sát", aliases: ["Enforcers"], locked: true });
    expect(dry.doc.characters).toEqual([{ name: "Vi", target: "Vi" }]);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText(/Saving flags 3 episodes stale. Nothing is retranslated./)).toBeInTheDocument();
    expect(puts().filter((body) => body?.dry_run !== true)).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(puts().filter((body) => body?.dry_run !== true)).toHaveLength(1));
    expect(puts().filter((body) => body?.dry_run !== true)[0]).toEqual({ doc: dry.doc });
  });

  it("saves at once when no translation is affected, and ignores a row nobody filled in", async () => {
    serve(glossary({}, { stale_now: 0, stale_after: 0, would_stale: 0 }));
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Add term" }));
    expect(screen.getByRole("region", { name: "Term 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText("No changes.")).toBeInTheDocument();

    const second = screen.getByRole("region", { name: "Term 2" });
    await userEvent.type(within(second).getByRole("textbox", { name: "Source" }), "Hextech");
    await userEvent.type(within(second).getByRole("textbox", { name: "Target" }), "Hextech");
    expect(await screen.findByText("No translation is affected.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(puts().filter((body) => body?.dry_run !== true)).toHaveLength(1));
  });

  it("deletes a term from the form and counts the effect of that", async () => {
    serve(glossary());
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Delete term 1" }));
    expect(screen.queryByRole("region", { name: "Term 1" })).not.toBeInTheDocument();
    expect(await screen.findByText("Saving flags 3 episodes stale.")).toBeInTheDocument();
    expect((puts().at(-1) as { doc: { terms?: unknown } }).doc.terms).toBeUndefined();
  });

  it("shows the path the worker names when the glossary is refused, and keeps Save off", async () => {
    serve({
      ...glossary(),
      "/api/series/9/glossary?lang=vi": (body: Record<string, unknown> | null) =>
        body === null ? { body: answer } : { status: 400, body: { error: "terms[0].from: expected 'S01E06', got '1x06'" } },
    });
    show("/series/1/glossary");
    const from = within(await screen.findByRole("region", { name: "Term 1" })).getByRole("textbox", { name: "From episode" });
    await userEvent.clear(from);
    await userEvent.type(from, "1x06");
    expect(within(screen.getByRole("region", { name: "Term 1" })).getByText("Use S01E06, or S01E06 00:32:10")).toBeInTheDocument();
    expect(await screen.findByText("terms[0].from: expected 'S01E06', got '1x06'")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("imports pasted YAML after counting its effect, and says where a broken one is wrong", async () => {
    serve({
      ...glossary(),
      "/api/series/9/glossary?lang=vi": (body: Record<string, unknown> | null) => {
        if (body === null) return { body: answer };
        const yaml = String(body.yaml ?? "");
        if (yaml.includes("broken")) return { status: 400, body: { error: "terms[0]: missing required field 'target'" } };
        return { body: { ok: true, saved: body.dry_run !== true, version: body.dry_run ? null : 5, stale_now: 0, stale_after: 5, would_stale: 5 } };
      },
    });
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Import YAML" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("button", { name: "Import" })).toBeDisabled();

    const box = within(sheet).getByRole("textbox", { name: "Or paste it here" });
    await userEvent.click(box);
    await userEvent.paste("terms:\n  - source: broken\n");
    expect(await within(sheet).findByText("terms[0]: missing required field 'target'")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Import" })).toBeDisabled();

    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste("terms:\n  - source: Enforcer\n    target: Cảnh vệ\n");
    expect(await within(sheet).findByText(/Importing flags 5 episodes stale. The current glossary is kept in History./)).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(puts().filter((body) => body?.dry_run !== true)).toHaveLength(1));
    expect(puts().filter((body) => body?.dry_run !== true)[0]).toEqual({ yaml: "terms:\n  - source: Enforcer\n    target: Cảnh vệ\n" });
  });

  it("lists the history, asks before it restores one, and restores it as a new version", async () => {
    serve(
      glossary({
        "/api/series/9/glossary/history?lang=vi": {
          body: {
            lang: "vi",
            items: [
              { version: 3, at: 1_800_000_000, note: "restore", terms: 2, characters: 1, address_rules: 0 },
              { version: 2, at: 1_700_000_000, note: "import", terms: 1, characters: 0, address_rules: 0 },
            ],
          },
        },
        "/api/series/9/glossary/restore": { body: { ok: true, version: 5, restored: 3 } },
      }),
    );
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "History" }));
    const sheet = await screen.findByRole("dialog");
    const list = await within(sheet).findByRole("list", { name: "Versions" });
    expect(within(list).getByText("(current)")).toBeInTheDocument();
    expect(within(list).getByText(/restored · 2 terms · 1 characters/)).toBeInTheDocument();
    expect(within(list).getByText(/imported · 1 terms/)).toBeInTheDocument();

    await userEvent.click(within(list).getByRole("button", { name: "Restore v3" }));
    expect(await screen.findByText(/The glossary goes back to v3 as a new version/)).toBeInTheDocument();
    expect(sent("/api/series/9/glossary/restore")).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(sent("/api/series/9/glossary/restore")).toEqual([{ lang: "vi", version: 3 }]));
  });

  it("copies the glossary of another series, merging by default", async () => {
    serve(glossary({ "/api/series/9/glossary/copy": { body: { ok: true, version: 5, from: "Cyberpunk" } } }));
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Copy from another series" }));
    const sheet = await screen.findByRole("dialog");
    // The series itself is not offered; the other is chosen by name and sent by the worker's own id.
    await userEvent.click(await within(sheet).findByRole("combobox", { name: "From" }));
    await userEvent.click(await screen.findByRole("option", { name: "Cyberpunk", hidden: true }));
    expect(screen.queryByRole("option", { name: "Arcane", hidden: true })).not.toBeInTheDocument();
    expect(within(sheet).getByRole("radio", { name: /Merge/ })).toBeChecked();
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(sent("/api/series/9/glossary/copy")).toEqual([{ lang: "vi", from_series: 12, merge: true }]));
  });

  it("says when the other series has no glossary", async () => {
    serve(glossary({ "/api/series/9/glossary/copy": { status: 404, body: { error: "the other series has no glossary in this language" } } }));
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Copy from another series" }));
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(await within(sheet).findByRole("combobox", { name: "From" }));
    await userEvent.click(await screen.findByRole("option", { name: "Cyberpunk", hidden: true }));
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy" }));
    expect(await within(sheet).findByText("the other series has no glossary in this language")).toBeInTheDocument();
  });

  it("exports through a link the server serves and a Copy button, never a blob", async () => {
    serve(glossary());
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Export YAML" }));
    const sheet = await screen.findByRole("dialog");
    const link = within(sheet).getByRole("link", { name: "Download glossary YAML" });
    expect(link).toHaveAttribute("href", "/api/series/9/glossary.yaml?lang=vi");
    expect(link).toHaveAttribute("download");
    expect(within(sheet).getByRole("textbox", { name: "YAML" })).toHaveValue(answer.yaml);
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy YAML" }));
    expect(writeText).toHaveBeenCalledWith(answer.yaml);
    expect(await within(sheet).findByText("Copied.")).toBeInTheDocument();
  });

  it("falls back to selecting the text when the clipboard is blocked", async () => {
    serve(glossary());
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("blocked")) }, configurable: true });
    show("/series/1/glossary");
    await userEvent.click(await screen.findByRole("button", { name: "Export YAML" }));
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("button", { name: "Copy YAML" }));
    expect(await within(sheet).findByText(/Copying was blocked here/)).toBeInTheDocument();
  });

  it("has nothing to export before a glossary is saved, and starts empty with a clear first step", async () => {
    serve({
      ...glossary(),
      "/api/series/9/glossary?lang=vi": { body: { lang: "vi", exists: false, doc: { version: 1 }, yaml: "", version: 0, stale_units: 0 } },
    });
    show("/series/1/glossary");
    expect(await screen.findByText("No glossary yet")).toBeInTheDocument();
    expect(screen.getByText(/No terms yet/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Export YAML" }));
    expect(await screen.findByText(/nothing to export yet/)).toBeInTheDocument();
  });

  it("says so for a series the worker keeps nothing of yet", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { series_id: null }) } });
    show("/series/1/glossary");
    expect(await screen.findByText(/keeps nothing for this series yet/)).toBeInTheDocument();
  });
});
