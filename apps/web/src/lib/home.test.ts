import { describe, expect, it } from "vitest";
import { movie, series, target } from "../test/fixtures";
import { buildRows, filterRows, movieRow, seriesRow } from "./home";

describe("seriesRow", () => {
  it("offers View failed before anything else", () => {
    const row = seriesRow(series({ missing: { vi: 3 }, failed: { vi: 1 } }));
    expect(row.action).toEqual({ kind: "view-failed", to: "/series/1/episodes?status=failed" });
    expect(row.facts).toEqual(["18 eps", "Missing 3", "Failed 1"]);
  });

  it("offers Translate missing when missing items have no open job", () => {
    const row = seriesRow(series({ id: 2, title: "Severance", missing: { vi: 5 }, open_jobs: { waiting: 2 } }));
    expect(row.action).toEqual({ kind: "translate", selector: { series: 2 }, title: "Severance" });
    expect(row.facts).toContain("Waiting 2");
  });

  it("offers no Translate when open jobs already cover what is missing", () => {
    const row = seriesRow(series({ missing: { vi: 2 }, open_jobs: { requested: 1, running: 1 } }));
    expect(row.action.kind).toBe("improve");
    expect(row.running).toBe(true);
  });

  it("offers Improve when nothing is missing", () => {
    expect(seriesRow(series()).action).toEqual({ kind: "improve", to: "/series/1/style" });
  });

  it("says a series Bazarr stopped listing is gone, offers no Translate and does not count it as missing", () => {
    const row = seriesRow(series({ gone: true, episodes: 2, missing: { vi: 2 } }));
    expect(row.facts).toEqual(["2 eps", "No longer in Bazarr"]);
    expect(row.action).toEqual({ kind: "improve", to: "/series/1/style" });
    expect(row.missing).toBe(false);
  });

  it("sums the counts over every target language", () => {
    const row = seriesRow(series({ missing: { vi: 1, fr: 2 }, failed: { vi: 0, fr: 0 } }));
    expect(row.facts).toContain("Missing 3");
  });
});

describe("movieRow", () => {
  it("offers Translate missing for a missing movie with a source and no job", () => {
    const row = movieRow(movie());
    expect(row.action).toEqual({ kind: "translate", selector: { items: [100] }, title: "Oppenheimer" });
    expect(row.to).toBe("/unit/55");
  });

  it("has no button while a job is open", () => {
    const row = movieRow(movie({ targets: [target({ job: "waiting", wait: "quota", text: "Missing · Waiting · daily quota" })] }));
    expect(row.action).toEqual({ kind: "none" });
    expect(row.facts).toEqual(["Missing · Waiting · daily quota"]);
    expect(row.running).toBe(true);
  });

  it("offers Improve for a translated movie", () => {
    const row = movieRow(movie({ targets: [target({ content: "translated", text: "Translated" })] }));
    expect(row.action).toEqual({ kind: "improve", to: "/unit/55/style" });
  });

  it("offers View failed, and does not offer Translate without a source subtitle", () => {
    expect(movieRow(movie({ targets: [target({ job: "failed", text: "Missing · Failed" })] })).action.kind).toBe("view-failed");
    const noSource = movieRow(movie({ has_source: false }));
    expect(noSource.action).toEqual({ kind: "none" });
    expect(noSource.facts).toContain("No source subtitle");
  });

  it("has no link while the worker keeps no unit for it", () => {
    expect(movieRow(movie({ unit_id: null })).to).toBeNull();
  });

  it("names the language when there are several", () => {
    const row = movieRow(movie({ targets: [target(), target({ lang: "fr", text: "Translated", content: "translated" })] }));
    expect(row.facts).toEqual(["VI: Missing", "FR: Translated"]);
  });
});

describe("buildRows and filterRows", () => {
  const overview = {
    status: { synced_at: null, attempted_at: null, status: "" as const, warning: "", items: 0 },
    languages: ["vi"],
    series: [
      series({ id: 2, title: "Severance", missing: { vi: 5 } }),
      series({ id: 1, title: "Arcane", failed: { vi: 1 } }),
      series({ id: 3, title: "Shōgun", ignored: { vi: 1 } }),
    ],
    movies: { count: 1, missing: { vi: 1 }, failed: { vi: 0 }, ignored: { vi: 0 } },
  };
  const rows = buildRows(overview, [movie(), movie({ id: 101, title: "Gone", gone: true })]);

  it("sorts by title and keeps a movie Bazarr no longer lists, said as such and never missing", () => {
    expect(rows.map((row) => row.title)).toEqual(["Arcane", "Gone", "Oppenheimer", "Severance", "Shōgun"]);
    const gone = rows.find((row) => row.title === "Gone");
    expect(gone?.facts).toEqual(["No longer in Bazarr"]);
    expect(gone).toMatchObject({ missing: false, action: { kind: "none" } });
  });

  it("filters by chip", () => {
    expect(filterRows(rows, "", "failed").map((row) => row.title)).toEqual(["Arcane"]);
    expect(filterRows(rows, "", "ignored").map((row) => row.title)).toEqual(["Shōgun"]);
    expect(filterRows(rows, "", "missing").map((row) => row.title)).toEqual(["Oppenheimer", "Severance"]);
    expect(filterRows(rows, "", "all")).toHaveLength(5);
  });

  it("searches without regard to case or accents", () => {
    expect(filterRows(rows, "shogun", "all").map((row) => row.title)).toEqual(["Shōgun"]);
    expect(filterRows(rows, "  OPPEN ", "all").map((row) => row.title)).toEqual(["Oppenheimer"]);
    expect(filterRows(rows, "zzz", "all")).toEqual([]);
  });
});
