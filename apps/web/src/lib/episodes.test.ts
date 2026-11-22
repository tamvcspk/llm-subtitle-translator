import { describe, expect, it } from "vitest";
import type { JobInfo } from "../api/types";
import { episode, seriesPage, target } from "../test/fixtures";
import { target as unitTarget } from "../test/unit-fixtures";
import { buildSeasons, filterSeasons, parseFilter, rowLabel, type EpisodeRow } from "./episodes";

const info = (overrides: Partial<JobInfo> = {}): JobInfo => ({
  job_id: "job-1",
  progress_done: null,
  progress_total: null,
  retry_at: null,
  error: "",
  missing_units: [],
  ...overrides,
});

function rowOf(item: ReturnType<typeof episode>, units = undefined as Parameters<typeof buildSeasons>[1]): EpisodeRow {
  const [season] = buildSeasons(seriesPage([item]), units, "vi", 0);
  return season!.rows[0]!;
}

const labels = (row: EpisodeRow) => row.actions.map((action) => action.label);

describe("row labels", () => {
  it("lay content, job and decision side by side", () => {
    expect(rowLabel(target())).toBe("Missing");
    expect(rowLabel(target({ content: "translated", stale: true, job: "waiting", wait: "quota" }))).toBe(
      "Translated · Stale · Waiting · quota",
    );
    expect(rowLabel(target({ job: "running", job_info: info({ progress_done: 120, progress_total: 340 }) }))).toBe(
      "Missing · Running 120/340",
    );
    expect(rowLabel(target({ removed: true, ignored: true }))).toBe("Missing · Removed · Ignored");
    expect(rowLabel(target({ content: "outside" }))).toBe("Outside profile");
  });
});

describe("the one button of a row", () => {
  it("follows the job first", () => {
    expect(labels(rowOf(episode(1, { targets: [target({ job: "requested", job_info: info() })] })))).toEqual(["Cancel"]);
    expect(labels(rowOf(episode(1, { targets: [target({ job: "running", job_info: info() })] })))).toEqual(["Stop"]);
    expect(labels(rowOf(episode(1, { targets: [target({ job: "failed", job_info: info({ error: "boom" }) })] })))).toEqual(["Retry"]);
  });

  it("gives waiting rows a button only when there is something to do", () => {
    const waiting = (wait: string, extra: Partial<JobInfo> = {}) =>
      labels(rowOf(episode(1, { targets: [target({ job: "waiting", wait, job_info: info(extra) })] })));
    expect(waiting("quota", { retry_at: 1 })).toEqual([]);
    expect(waiting("settle")).toEqual([]);
    expect(waiting("rate")).toEqual([]);
    expect(waiting("provider")).toEqual(["Open Connections"]);
  });

  it("asks for the earlier episode a context wait names, and for nothing it cannot find", () => {
    const items = [
      episode(4),
      episode(5, { targets: [target({ job: "waiting", wait: "context", job_info: info({ missing_units: ["S01E04"] }) })] }),
    ];
    const [season] = buildSeasons(seriesPage(items), undefined, "vi", 0);
    const row = season!.rows[1]!;
    expect(row.details).toEqual(["needs S01E04"]);
    expect(row.actions[0]).toMatchObject({ label: "Request that episode", request: { selector: { items: [1004], languages: ["vi"] } } });

    const lost = rowOf(episode(5, { targets: [target({ job: "waiting", wait: "context", job_info: info({ missing_units: ["S09E09"] }) })] }));
    expect(lost.actions).toEqual([]);
  });

  it("follows the content when no job is open", () => {
    const one = (overrides: Partial<ReturnType<typeof target>>, item: Partial<ReturnType<typeof episode>> = {}) =>
      labels(rowOf(episode(1, { ...item, targets: [target(overrides)] })));
    expect(one({})).toEqual(["Translate"]);
    expect(one({ content: "outside" })).toEqual(["Translate"]);
    expect(one({ content: "translated" })).toEqual(["Improve"]);
    expect(one({ content: "translated", stale: true })).toEqual(["Retranslate"]);
    expect(one({ content: "edited" })).toEqual(["History"]);
    expect(one({ content: "external" })).toEqual(["Retranslate"]); // a subtitle made elsewhere can be replaced (the old file is kept)
    expect(one({ content: "external" }, { has_source: false })).not.toContain("Retranslate");
    expect(one({ removed: true })).toEqual(["History", "Translate again"]);
    expect(one({ ignored: true })).toEqual(["Stop ignoring"]);
  });

  it("offers no Translate without a source subtitle, and says why", () => {
    const row = rowOf(episode(1, { has_source: false }));
    expect(row.actions).toEqual([]);
    expect(row.details).toEqual(["no source subtitle"]);
    expect(row.askable).toBe(false);
  });

  it("retranslates by replacing, for that language only", () => {
    const row = rowOf(episode(1, { targets: [target({ content: "translated", stale: true })] }));
    expect(row.actions[0]).toMatchObject({ request: { selector: { items: [1001], languages: ["vi"], mode: "replace" } } });
  });

  it("gives an episode that is gone from Bazarr no button and no checkbox target", () => {
    const row = rowOf(episode(1, { gone: true }));
    expect(row.actions).toEqual([]);
    expect(row.askable).toBe(false);
  });

  it("does not call a gone episode Missing: it is Not translated, and not counted as missing", () => {
    const row = rowOf(episode(1, { gone: true }));
    expect(row.label).toBe("Not translated");
    expect(row.missing).toBe(false);
    expect(row.details).toContain("no longer in Bazarr");
  });
});

describe("what a row says under its label", () => {
  it("gives the time a quota wait ends, the error of a failure and the version of a file", () => {
    const quota = rowOf(episode(1, { targets: [target({ job: "waiting", wait: "quota", job_info: info({ retry_at: 1_800_000_000 }) })] }));
    expect(quota.details[0]).toMatch(/^resumes \d{1,2}:\d{2}/);
    expect(rowOf(episode(1, { targets: [target({ job: "failed", job_info: info({ error: "Output token limit exceeded" }) })] })).details).toEqual([
      "Output token limit exceeded",
    ]);

    const now = 1_800_000_000_000;
    const [season] = buildSeasons(
      seriesPage([episode(1, { targets: [target({ content: "translated" })] })]),
      [{ id: 501, season: 1, episode: 1, targets: [unitTarget({ version: 2, model: "flash", updated_at: 1_800_000_000 - 7200 })] }],
      "vi",
      now,
    );
    expect(season!.rows[0]!.details).toEqual(["v2 · flash · 2h ago"]);
  });

  it("warns about a file changed outside the worker", () => {
    const [season] = buildSeasons(
      seriesPage([episode(1, { targets: [target({ content: "edited" })] })]),
      [{ id: 501, season: 1, episode: 1, targets: [unitTarget({ version: 3, model: "", updated_at: null, modified_outside: true })] }],
      "vi",
      0,
    );
    expect(season!.rows[0]!.warning).toBe("File changed outside the worker");
  });
});

describe("seasons", () => {
  it("group by season in story order and count what a Translate would take", () => {
    const items = [
      episode(2, { season: 2 }),
      episode(10),
      episode(2, { id: 2000, season: 0, episode: 1 }),
      episode(9, { targets: [target({ job: "requested", job_info: info() })] }),
      episode(1, { targets: [target({ content: "translated" })] }),
    ];
    const seasons = buildSeasons(seriesPage(items), undefined, "vi", 0);
    expect(seasons.map((season) => season.title)).toEqual(["Specials", "Season 1", "Season 2"]);
    expect(seasons[1]!.rows.map((row) => row.code)).toEqual(["S01E01", "S01E09", "S01E10"]);
    expect(seasons.map((season) => season.askable)).toEqual([1, 1, 1]);
  });

  it("filter by status and drop the seasons left empty", () => {
    const items = [
      episode(1, { targets: [target({ job: "failed", job_info: info() })] }),
      episode(2, { season: 2 }),
    ];
    const seasons = buildSeasons(seriesPage(items), undefined, "vi", 0);
    expect(filterSeasons(seasons, "failed").map((season) => season.title)).toEqual(["Season 1"]);
    expect(filterSeasons(seasons, "all")).toHaveLength(2);
    expect(filterSeasons(seasons, "stale")).toEqual([]);
  });

  it("read the status from the address and ignore anything else", () => {
    expect(parseFilter("failed")).toBe("failed");
    expect(parseFilter("bogus")).toBe("all");
    expect(parseFilter(null)).toBe("all");
  });
});
