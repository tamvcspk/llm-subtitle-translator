import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { base, cue, cues, episode, target } from "../test/unit-fixtures";
import { sent, serve, show } from "../test/render";

describe("Unit page", () => {
  it("heads an episode with its code, state and version, and links back to its series", async () => {
    serve({ ...base, "/api/units/7": { body: episode() }, "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) } });
    show("/unit/7");
    expect(await screen.findByRole("heading", { name: "S01E08 · Oil and Water" })).toBeInTheDocument();
    expect(screen.getByText(/Edited · Missing units: none/)).toBeInTheDocument();
    expect(screen.getByText(/Current v3 · cue_edit · gemini-3.1-flash-lite/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Arcane/ })).toHaveAttribute("href", "/series/4/episodes");
    expect(screen.getByRole("tab", { name: "Subtitles" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Context" })).toBeInTheDocument();
  });

  it("gives a movie no Context tab and a way back to the Library", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode({ series: null, series_id: null, season: null, episode: null, title: "Heat", memory_applicable: false }) },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
    });
    show("/unit/7");
    expect(await screen.findByRole("heading", { name: "Heat" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Context" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: /Library/ })).toHaveAttribute("href", "/library");
  });

  it("says a missing episode is unknown", async () => {
    serve({ ...base, "/api/units/7": { status: 404, body: { error: "no unit 7" } } });
    show("/unit/7");
    expect(await screen.findByText("That episode is not known to the worker.")).toBeInTheDocument();
  });

  it("lists cues with their lock and edits one through a sheet that locks it", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode() },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1), cue(2, { locked: true })]) },
      "/api/units/7/corrections?lang=vi": { body: { items: [{ id: 31, kind: "cue_edit", payload: { cue: 2, text: "x" }, created_at: 1 }] } },
      "/api/units/7/corrections": { body: { version: 4, warnings: ["Tags differ from the source"] } },
      "/api/units/7/corrections/31/remove": { body: { ok: true } },
    });
    show("/unit/7/subtitles");
    const list = await screen.findByRole("list", { name: "Cues" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("Locked")).toBeInTheDocument();

    await userEvent.click(within(list).getByRole("button", { name: "Edit cue 1" }));
    const sheet = await screen.findByRole("dialog");
    const box = within(sheet).getByRole("textbox", { name: /Target/ });
    expect(box).toHaveValue("Target line 1");
    expect(within(sheet).getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.clear(box);
    await userEvent.type(box, "Better line");
    await userEvent.click(within(sheet).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sent("/api/units/7/corrections")).toHaveLength(1));
    expect(sent("/api/units/7/corrections")[0]).toEqual({
      lang: "vi",
      kind: "cue_edit",
      scope: "episode",
      payload: { cue: 1, text: "Better line" },
    });
    expect(await screen.findByText("Tags differ from the source")).toBeInTheDocument();
  });

  it("unlocks a locked cue from its sheet", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode() },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(2, { locked: true })]) },
      "/api/units/7/corrections?lang=vi": { body: { items: [{ id: 31, kind: "cue_edit", payload: { cue: 2, text: "x" }, created_at: 1 }] } },
      "/api/units/7/corrections/31/remove": { body: { ok: true } },
    });
    show("/unit/7/subtitles");
    await userEvent.click(await screen.findByRole("button", { name: "Edit cue 2" }));
    const sheet = await screen.findByRole("dialog");
    const unlock = within(sheet).getByRole("button", { name: "Unlock" });
    await waitFor(() => expect(unlock).toBeEnabled());
    await userEvent.click(unlock);
    await waitFor(() => expect(sent("/api/units/7/corrections/31/remove")).toHaveLength(1));
  });

  it("jumps to a player time and searches by text", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode() },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
      "/api/units/7/cues?lang=vi&offset=0&limit=50&at=00%3A14%3A20": { body: cues([cue(142)], { focus: 142, offset: 117, total: 531 }) },
      "/api/units/7/cues?lang=vi&offset=0&limit=50&q=stake": { body: cues([cue(5, { source: "what's at stake" })], { total: 1 }) },
    });
    show("/unit/7/subtitles");
    await screen.findByRole("list", { name: "Cues" });
    const jump = screen.getByRole("textbox", { name: "Jump to time" });
    await userEvent.type(jump, "nope");
    expect(screen.getByRole("button", { name: "Jump" })).toBeDisabled();
    await userEvent.clear(jump);
    await userEvent.type(jump, "00:14:20");
    await userEvent.click(screen.getByRole("button", { name: "Jump" }));
    expect(await screen.findByText(/Cue #142/)).toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox", { name: "Search text" }), "stake");
    expect(await screen.findByText(/what's at stake/)).toBeInTheDocument();
  });

  it("removes the output only after a confirmation", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode() },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
      "/api/units/7/output/remove": { body: { ok: true, version: 4 } },
    });
    show("/unit/7");
    await userEvent.click(await screen.findByRole("button", { name: "Remove output" }));
    const sheet = await screen.findByRole("dialog");
    expect(sent("/api/units/7/output/remove")).toEqual([]);
    expect(within(sheet).getByText(/stays in History/)).toBeInTheDocument();
    await userEvent.click(within(sheet).getByRole("button", { name: "Remove output" }));
    await waitFor(() => expect(sent("/api/units/7/output/remove")).toEqual([{ lang: "vi" }]));
  });

  it("offers no edit when there is no target subtitle yet", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode({ targets: [target({ exists: false, version: null, status: "untranslated", trigger: null })] }) },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1, { target: null })], { has_target: false }) },
    });
    show("/unit/7");
    expect(await screen.findByText(/no target subtitle yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit cue/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove output" })).not.toBeInTheDocument();
  });
});
