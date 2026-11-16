import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { sent, serve, show } from "../../test/render";
import { base, cue, cues, episode } from "../../test/unit-fixtures";

const seriesStyle = {
  series: { id: 9, title: "Arcane" },
  lang: "vi",
  preset: "drama",
  style: { formality: "formal" },
  effective: { formality: "formal", profanity: "keep" },
  presets: [],
  cards: [],
};

const routes = (own: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
  "/api/series/9/style?lang=vi": { body: seriesStyle },
  "/api/units/7/style": (body: Record<string, unknown> | null) =>
    body === null ? { body: { style: own } } : { body: { ok: true, saved: body.dry_run !== true, style: body.style, stale_now: 0, stale_after: 1, would_stale: 1 } },
  ...extra,
});

describe("Style tab of an episode", () => {
  it("says it overrides the series, and shows what the series gives on each axis", async () => {
    serve(routes({}));
    show("/unit/7/style");
    expect(await screen.findByText("Overrides the series style for this episode only.")).toBeInTheDocument();
    expect(await screen.findByRole("radio", { name: "Inherit · now Formal" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Inherit · now Keep" })).toBeChecked();
    expect(screen.queryByRole("combobox", { name: "Preset" })).not.toBeInTheDocument();
    expect(screen.getByText("This episode follows the series style.")).toBeInTheDocument();
  });

  it("counts the episode it would flag, then saves the override after a confirmation", async () => {
    serve(routes({}));
    show("/unit/7/style");
    await userEvent.click(await screen.findByRole("radio", { name: "Casual" }));
    expect(await screen.findByText("Saving flags 1 episode stale.")).toBeInTheDocument();
    expect(sent("/api/units/7/style").filter((body) => body !== null)).toEqual([{ lang: "vi", style: { formality: "casual" }, dry_run: true }]);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(sent("/api/units/7/style").filter((body) => body !== null && body.dry_run !== true)).toEqual([
        { lang: "vi", style: { formality: "casual" } },
      ]),
    );
  });

  it("clears the override into the draft, which is saved like any other change", async () => {
    serve(routes({ formality: "casual" }));
    show("/unit/7/style");
    expect(await screen.findByRole("radio", { name: "Casual" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Clear episode override" }));
    expect(await screen.findByRole("radio", { name: "Inherit · now Formal" })).toBeChecked();
    expect(await screen.findByText("Saving flags 1 episode stale.")).toBeInTheDocument();
    expect(sent("/api/units/7/style").filter((body) => body !== null)).toEqual([{ lang: "vi", style: {}, dry_run: true }]);
  });
});

describe("Style tab of a movie", () => {
  it("is the movie's own style, with a preset and Try 20 lines", async () => {
    serve({
      ...base,
      "/api/units/7": { body: episode({ series: null, series_id: null, season: null, episode: null, title: "Heat", memory_applicable: false }) },
      "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
      "/api/movies/7/style?lang=vi": { body: { movie: { id: 7, title: "Heat" }, lang: "vi", preset: "", style: {}, effective: { formality: "neutral" }, presets: [{ id: "drama", name: "Drama", genres: [], style: {}, builtin: true }], cards: [] } },
    });
    show("/unit/7/style");
    expect(await screen.findByRole("radio", { name: "Inherit · now Neutral" })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Preset" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try 20 lines (≈ 1 call)" })).toBeInTheDocument();
  });
});
