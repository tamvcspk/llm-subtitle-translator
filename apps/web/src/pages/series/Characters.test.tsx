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

const doc = {
  version: 1,
  characters: [
    { name: "Vi", target: "Vi", gender: "f", description: "Brawler from Zaun" },
    { name: "Caitlyn", target: "Caitlyn" },
  ],
  address_rules: [{ a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị" }],
};

const overview = (cards: unknown[]) => ({
  series: { id: 9, title: "Arcane" },
  lang: "vi",
  preset: "",
  style: {},
  effective: { formality: "neutral", profanity: "soften", localization: "balanced" },
  presets: [],
  cards,
});

const vi = { name: "Vi", aliases: ["Violet"], speech: "Blunt, street slang", catchphrases: ["Cupcake"], samples: ["a", "b"], style: { profanity: "keep" }, always: true };

function characters(cards: unknown[] = [vi], glossaryDoc: unknown = doc, extra: Record<string, unknown> = {}) {
  return {
    ...base,
    "/api/series/9/glossary?lang=vi": (body: Record<string, unknown> | null) =>
      body === null
        ? { body: { lang: "vi", exists: true, doc: glossaryDoc, yaml: "", version: 2, stale_units: 0 } }
        : { body: { ok: true, saved: body.dry_run !== true, version: 3, stale_now: 0, stale_after: 2, would_stale: 2 } },
    "/api/series/9/style?lang=vi": { body: overview(cards) },
    "/api/series/9/cards?lang=vi": (body: Record<string, unknown> | null) => ({
      body: { ok: true, saved: body?.dry_run !== true, card: body?.card, stale_now: 0, stale_after: 3, would_stale: 3 },
    }),
    "/api/series/9/characters?lang=vi": { body: { ok: true, saved: true } },
    ...extra,
  };
}

const real = (path: string) => sent(path).filter((body) => body !== null && body.dry_run !== true);

describe("Series Characters tab", () => {
  it("shows each character with its Name and Voice, and the forms of address as read only", async () => {
    serve(characters());
    show("/series/1/characters");
    const card = await screen.findByRole("region", { name: "Vi" });
    expect(within(card).getByText("Brawler from Zaun")).toBeInTheDocument();
    expect(within(card).getByText("Blunt, street slang")).toBeInTheDocument();
    expect(within(card).getByText("Violet")).toBeInTheDocument();
    expect(within(card).getByText(/Swearing = Keep/)).toBeInTheDocument();
    expect(within(card).getByText("2 lines")).toBeInTheDocument();
    expect(within(card).getByText("Always in the prompt")).toBeInTheDocument();
    expect(within(card).getByText("with Powder: chị–em / em–chị")).toBeInTheDocument();
    // A character with a name but no voice says so.
    expect(within(screen.getByRole("region", { name: "Caitlyn" })).getByText(/No voice yet/)).toBeInTheDocument();
    expect(within(card).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("starts with a clear first step when there are no characters", async () => {
    serve(characters([], { version: 1 }));
    show("/series/1/characters");
    expect(await screen.findByText(/No character cards yet/)).toBeInTheDocument();
  });

  it("edits a voice, counts the effect of both writes, and saves the name and the card in one request after one question", async () => {
    serve(characters());
    show("/series/1/characters");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Vi" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit Vi" });
    expect(within(sheet).getByRole("textbox", { name: /Source name/ })).toHaveValue("Vi");
    expect(within(sheet).getByRole("textbox", { name: "Speech" })).toHaveValue("Blunt, street slang");
    // Forms of address are not an input here: there is a way to the place that edits them.
    expect(within(sheet).getByText("with Powder: chị–em / em–chị")).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: /Manage in Forms of address/ })).toHaveAttribute("href", "/series/1/glossary");
    expect(within(sheet).getByRole("button", { name: "Save" })).toBeDisabled();

    const speech = within(sheet).getByRole("textbox", { name: "Speech" });
    await userEvent.clear(speech);
    await userEvent.type(speech, "Calm");
    // 2 (the glossary half) + 3 (the card half): the effect of both is added, as the design says.
    expect(await within(sheet).findByText("Saving flags 5 episodes stale.")).toBeInTheDocument();
    expect((sent("/api/series/9/cards?lang=vi").at(-1) as { card: { speech: string }; dry_run: boolean }).dry_run).toBe(true);

    await userEvent.click(within(sheet).getByRole("button", { name: "Save" }));
    const ask = await screen.findByRole("dialog", { name: "Save character?" });
    expect(sent("/api/series/9/characters?lang=vi")).toEqual([]);
    await userEvent.click(within(ask).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/series/9/characters?lang=vi")).toHaveLength(1));
    // One request holds both halves; nothing else is written (the dry runs are the only other calls).
    expect(real("/api/series/9/cards?lang=vi")).toEqual([]);
    expect(real("/api/series/9/glossary?lang=vi")).toEqual([]);
    const written = sent("/api/series/9/characters?lang=vi")[0] as { glossary: typeof doc; card: unknown };
    expect(written.glossary.characters[0]).toEqual({ name: "Vi", target: "Vi", gender: "f", description: "Brawler from Zaun" });
    expect(written.glossary.address_rules).toEqual(doc.address_rules);
    expect(written.card).toEqual({
      card: { name: "Vi", aliases: ["Violet"], speech: "Calm", catchphrases: ["Cupcake"], samples: ["a", "b"], style: { profanity: "keep" }, always: true },
    });
  });

  it("adds a character with only a name to the glossary, and writes a card only once there is a voice", async () => {
    serve(characters());
    show("/series/1/characters");
    await userEvent.click(await screen.findByRole("button", { name: "Add character" }));
    const sheet = await screen.findByRole("dialog", { name: "Add character" });
    await userEvent.type(within(sheet).getByRole("textbox", { name: /Source name/ }), "Jayce");
    expect(await within(sheet).findByText("Saving flags 2 episodes stale.")).toBeInTheDocument();
    expect(sent("/api/series/9/cards?lang=vi")).toEqual([]);

    await userEvent.type(within(sheet).getByRole("textbox", { name: "Speech" }), "Measured");
    expect(await within(sheet).findByText("Saving flags 5 episodes stale.")).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("button", { name: "Save" }));
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Save character?" })).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/series/9/characters?lang=vi")).toHaveLength(1));
    const written = sent("/api/series/9/characters?lang=vi")[0] as { glossary: typeof doc; card: unknown };
    expect(written.card).toEqual({ card: { name: "Jayce", speech: "Measured" } });
    expect(written.glossary.characters.map((item) => item.name)).toEqual(["Vi", "Caitlyn", "Jayce"]);
  });

  it("refuses a name another character already has", async () => {
    serve(characters());
    show("/series/1/characters");
    await userEvent.click(await screen.findByRole("button", { name: "Add character" }));
    const sheet = await screen.findByRole("dialog", { name: "Add character" });
    await userEvent.type(within(sheet).getByRole("textbox", { name: /Source name/ }), "vi");
    expect(await within(sheet).findByText("There is already a character called vi.")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("renames a character in both places, naming the old card it replaces", async () => {
    serve(characters());
    show("/series/1/characters");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Vi" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit Vi" });
    const name = within(sheet).getByRole("textbox", { name: /Source name/ });
    await userEvent.clear(name);
    await userEvent.type(name, "Violet");
    await within(sheet).findByText("Saving flags 5 episodes stale.");
    await userEvent.click(within(sheet).getByRole("button", { name: "Save" }));
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Save character?" })).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/series/9/characters?lang=vi")).toHaveLength(1));
    const written = sent("/api/series/9/characters?lang=vi")[0] as { glossary: typeof doc; card: { replaces: string } };
    expect(written.card.replaces).toBe("Vi");
    expect(written.glossary.characters.map((item) => item.name)).toEqual(["Violet", "Caitlyn"]);
  });

  it("deletes the voice and the name in one request, after counting what leaving the glossary flags", async () => {
    serve(characters());
    show("/series/1/characters");
    await userEvent.click(await screen.findByRole("button", { name: "Delete Vi" }));
    const ask = await screen.findByRole("dialog", { name: "Delete Vi?" });
    expect(await within(ask).findByText(/This removes the name and the voice. Deleting flags 2 episodes stale. Nothing is retranslated./)).toBeInTheDocument();
    expect(sent("/api/series/9/characters?lang=vi")).toEqual([]);
    await userEvent.click(within(ask).getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(sent("/api/series/9/characters?lang=vi")).toHaveLength(1));
    const written = sent("/api/series/9/characters?lang=vi")[0] as { glossary: typeof doc; delete_card: string };
    expect(written.delete_card).toBe("Vi");
    expect(written.glossary.characters.map((item) => item.name)).toEqual(["Caitlyn"]);
  });

  it("shows the refusal of the worker and has sent nothing else when the one request fails", async () => {
    serve(characters([vi], doc, { "/api/series/9/characters?lang=vi": { status: 400, body: { error: "card.speech: expected text" } } }));
    show("/series/1/characters");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Vi" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit Vi" });
    await userEvent.type(within(sheet).getByRole("textbox", { name: "Speech" }), "!");
    await within(sheet).findByText("Saving flags 5 episodes stale.");
    await userEvent.click(within(sheet).getByRole("button", { name: "Save" }));
    await userEvent.click(within(await screen.findByRole("dialog", { name: "Save character?" })).getByRole("button", { name: "Confirm" }));
    expect((await screen.findAllByText("card.speech: expected text")).length).toBeGreaterThan(0);
    expect(sent("/api/series/9/characters?lang=vi")).toHaveLength(1);
    expect(real("/api/series/9/glossary?lang=vi")).toEqual([]);
  });

  it("says the worker keeps nothing of a series it has not seen", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { series_id: null }) } });
    show("/series/1/characters");
    expect(await screen.findByText(/keeps nothing for this series yet/)).toBeInTheDocument();
  });

  it("groups multi-stage forms of address by partner with episode windows", async () => {
    const stagedDoc = {
      ...doc,
      address_rules: [
        { a: "Powder", b: "Vi", a_to_b: "tôi–cô", b_to_a: "tôi–em", from: "S02E01" },
        { a: "Vi", b: "Powder", a_to_b: "chị–em", b_to_a: "em–chị", until: "S02E01" },
      ],
    };
    serve(characters([vi], stagedDoc));
    show("/series/1/characters");
    const card = await screen.findByRole("region", { name: "Vi" });
    expect(within(card).getByText("with Powder:")).toBeInTheDocument();
    expect(within(card).getByText("until S02E01: chị–em / em–chị")).toBeInTheDocument();
    expect(within(card).getByText("from S02E01: tôi–em / tôi–cô")).toBeInTheDocument();
  });
});
