import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Correction } from "../../api/types";
import { buildPayload, describeCorrection, scopesFor } from "../../lib/corrections";
import { sent, serve, show } from "../../test/render";
import { base, cue, cues, episode } from "../../test/unit-fixtures";

const routes = (items: Correction[] = [], extra: Record<string, unknown> = {}) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
  "/api/units/7/corrections?lang=vi": { body: { items } },
  ...extra,
});

describe("correction forms", () => {
  it("name what is still missing before anything is sent", () => {
    expect(buildPayload("term", { source: "Council" })).toEqual({ problem: "Give the term and its translation." });
    expect(buildPayload("address", { a: "Vi", b: "Cait" })).toEqual({ problem: "Say how one addresses the other." });
    expect(buildPayload("cue_edit", { cue: "0", text: "x" })).toEqual({ problem: "The cue number must be a whole number from 1." });
    expect(buildPayload("address", { a: "Vi", b: "Cait", a_to_b: "chị – em", from_cue: "12" })).toEqual({
      payload: { a: "Vi", b: "Cait", a_to_b: "chị – em", from_cue: 12 },
    });
    expect(buildPayload("term", { source: " Council ", target: " Hội Đồng " })).toEqual({
      payload: { source: "Council", target: "Hội Đồng" },
    });
  });

  it("offer a wider scope only to a term or a form of address", () => {
    expect(scopesFor("note", true).map((s) => s.value)).toEqual(["episode"]);
    expect(scopesFor("cue_edit", true).map((s) => s.value)).toEqual(["episode"]);
    expect(scopesFor("term", true).map((s) => s.value)).toEqual(["episode", "forward", "series"]);
    expect(scopesFor("term", false).map((s) => s.label)).toEqual(["This movie", "Movie glossary"]);
  });

  it("read as one line", () => {
    const make = (kind: Correction["kind"], payload: Record<string, unknown>): Correction => ({ id: 1, kind, payload, created_at: 1 });
    expect(describeCorrection(make("term", { source: "Enforcer", target: "Cảnh vệ" }))).toBe('"Enforcer" → "Cảnh vệ"');
    expect(describeCorrection(make("cue_edit", { cue: 143, text: "x" }))).toBe("Cue #143 locked");
    expect(describeCorrection(make("address", { a: "Vi", b: "Cait", a_to_b: "chị", b_to_a: "em" }))).toBe("Vi → Cait: chị · Cait → Vi: em");
  });
});

describe("Corrections tab", () => {
  it("lists what the episode has and takes one off", async () => {
    serve(
      routes(
        [
          { id: 3, kind: "term", payload: { source: "Enforcer", target: "Cảnh vệ" }, created_at: 1 },
          { id: 4, kind: "note", payload: { text: "Whispered scene" }, created_at: 1 },
        ],
        { "/api/units/7/corrections/3/remove": { body: { ok: true } } },
      ),
    );
    show("/unit/7/corrections");
    const list = await screen.findByRole("list", { name: "Corrections" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    await userEvent.click(within(list).getByRole("button", { name: /Remove Term/ }));
    await waitFor(() => expect(sent("/api/units/7/corrections/3/remove")).toHaveLength(1));
  });

  it("adds a term to the series and says how many translations it flags stale", async () => {
    serve(
      routes([], {
        "/api/units/7/corrections": { body: { ok: true, scope: "series", impact: { stale_now: 0, stale_after: 3, would_stale: 3 } } },
      }),
    );
    show("/unit/7/corrections");
    await screen.findByText("Add a correction");
    await userEvent.type(screen.getByRole("textbox", { name: "Term in the source" }), "Council");
    await userEvent.type(screen.getByRole("textbox", { name: "Translation" }), "Hội Đồng");
    await userEvent.click(screen.getByRole("radio", { name: "Whole series" }));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByText(/3 translations are flagged stale. Nothing is retranslated/)).toBeInTheDocument();
    expect(sent("/api/units/7/corrections")).toEqual([
      { lang: "vi", kind: "term", scope: "series", payload: { source: "Council", target: "Hội Đồng" } },
    ]);
  });

  it("does not send a form that is not filled in", async () => {
    serve(routes());
    show("/unit/7/corrections");
    await userEvent.click(await screen.findByRole("button", { name: "Add" }));
    expect(await screen.findByText("Give the term and its translation.")).toBeInTheDocument();
    expect(sent("/api/units/7/corrections")).toEqual([]);
  });

  it("does not carry what was typed for a note into a cue edit, which would overwrite a cue", async () => {
    serve(routes());
    show("/unit/7/corrections");
    await userEvent.click(await screen.findByRole("radio", { name: "Note" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Note for the translator" }), "Whispered scene");
    await userEvent.click(screen.getByRole("radio", { name: "Cue edit" }));
    expect(screen.getByRole("textbox", { name: "New line" })).toHaveValue("");
    // and back again: the note box does not keep it either
    await userEvent.click(screen.getByRole("radio", { name: "Note" }));
    expect(screen.getByRole("textbox", { name: "Note for the translator" })).toHaveValue("");
  });

  it("keeps a note to the episode", async () => {
    serve(routes());
    show("/unit/7/corrections");
    await userEvent.click(await screen.findByRole("radio", { name: "Note" }));
    expect(screen.getByRole("radio", { name: "Episode" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: "Whole series" })).not.toBeInTheDocument();
  });
});
