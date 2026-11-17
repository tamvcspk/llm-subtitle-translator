import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { base, cue, cues, episode } from "../test/unit-fixtures";
import { sent, serve, show } from "../test/render";

const heat = episode({ id: 55, series: null, series_id: null, season: null, episode: null, title: "Heat", memory_applicable: false });
const doc = { version: 1, terms: [{ source: "Waingro", target: "Waingro", from: "S01E01" }], address_rules: [{ a: "Neil", b: "Vincent", a_to_b: "tôi–anh", from: "S01E01" }] };

const movie = {
  ...base,
  "/api/units/55": { body: heat },
  "/api/units/55/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
  "/api/movies/55/glossary?lang=vi": (body: Record<string, unknown> | null) =>
    body === null
      ? { body: { lang: "vi", exists: true, doc, yaml: "terms:\n", version: 2, stale_units: 0 } }
      : { body: { ok: true, saved: body.dry_run !== true, version: 3, stale_now: 0, stale_after: 1, would_stale: 1 } },
};

describe("Movie Glossary", () => {
  it("is reached from the header of a movie, and not offered on an episode", async () => {
    serve(movie);
    show("/unit/55/subtitles");
    expect(await screen.findByRole("link", { name: "Movie Glossary →" })).toHaveAttribute("href", "/unit/55/glossary?lang=vi");
  });

  it("is not offered on an episode", async () => {
    serve({ ...base, "/api/units/7": { body: episode() }, "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) } });
    show("/unit/7/subtitles");
    await screen.findByRole("heading", { name: "S01E08 · Oil and Water" });
    expect(screen.queryByRole("link", { name: /Movie Glossary/ })).not.toBeInTheDocument();
  });

  it("edits the movie's own glossary through the movie's routes, without an episode window or a copy", async () => {
    serve(movie);
    show("/unit/55/glossary?lang=vi");
    expect(await screen.findByRole("heading", { name: "Glossary · Heat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Heat/ })).toHaveAttribute("href", "/unit/55/subtitles?lang=vi");
    const term = await screen.findByRole("region", { name: "Term 1" });
    // One film has no story order: no From/Until, and a glossary is only copied between series.
    expect(within(term).queryByRole("textbox", { name: "From episode" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy from another series" })).not.toBeInTheDocument();

    const target = within(term).getByRole("textbox", { name: "Target" });
    await userEvent.clear(target);
    await userEvent.type(target, "Waingro!");
    expect(await screen.findByText("Saving flags 1 movie stale.")).toBeInTheDocument();
    // What the form does not show (the window an import left on a term) is not lost.
    expect((sent("/api/movies/55/glossary?lang=vi").at(-1) as { doc: typeof doc }).doc.terms[0]).toEqual({ source: "Waingro", target: "Waingro!", from: "S01E01" });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/movies/55/glossary?lang=vi").filter((body) => body !== null && body.dry_run !== true)).toHaveLength(1));
  });

  it("exports through the movie's own link", async () => {
    serve(movie);
    show("/unit/55/glossary?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Export YAML" }));
    expect(await screen.findByRole("link", { name: "Download glossary YAML" })).toHaveAttribute("href", "/api/movies/55/glossary.yaml?lang=vi");
  });

  it("sends an episode to its series' glossary instead", async () => {
    serve({ ...base, "/api/units/7": { body: episode() }, "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) } });
    show("/unit/7/glossary");
    expect(await screen.findByRole("heading", { name: "S01E08 · Oil and Water" })).toBeInTheDocument();
  });
});
