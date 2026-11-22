import { describe, expect, it } from "vitest";
import type { QueueJob } from "../api/types";
import { provider, queueCounts } from "../test/fixtures";
import { firstTab, jobLink, jobName, jobStatus, lastLine, layerRows, originLine, providerLine, reach, readsOnly, whenText } from "./activity";

const NOW = Date.UTC(2026, 10, 17, 12, 0, 0);
const job = (extra: Partial<QueueJob> = {}): QueueJob => ({ job_id: "j1", title: "Arcane", label: "S01E04", target_code: "vi", origin: "manual", mode: "fill", ...extra });

describe("Activity", () => {
  it("names a job by its series, episode and language, and an older one by its file", () => {
    expect(jobName(job())).toBe("Arcane · S01E04 · vi");
    expect(jobName(job({ title: "Heat", label: "" }))).toBe("Heat · vi");
    expect(jobName(job({ title: "", label: "", subtitle_path: "/media/Heat/Heat.en.srt" }))).toBe("Heat.en.srt · vi");
    expect(jobName(job({ title: "", label: "", subtitle_path: "", target_code: "" }))).toBe("j1");
  });

  it("names a job that reads for what it reads, and gives it no subtitle to export", () => {
    const extract = job({ kind: "extract", target_code: undefined, mode: "extract" });
    expect(jobName(extract)).toBe("Extract facts · Arcane · S01E04");
    expect(jobName(job({ kind: "suggest", label: "", target_code: "vi" }))).toBe("Look for proposals · Arcane · vi");
    expect(readsOnly(extract)).toBe(true);
    expect(readsOnly(job({ kind: "variant" }))).toBe(false);
    expect(readsOnly(job())).toBe(false);
    expect(jobStatus({ ...extract, result: "extracted" }, "done")).toBe("manual · extract · facts read");
    expect(jobStatus({ ...extract, result: "skipped-no-source" }, "done")).toBe("manual · extract · no source subtitle to read");
    expect(jobStatus({ ...extract, stage: "Reading S01E04" }, "running")).toBe("manual · extract · Reading S01E04");
  });

  it("names a judge job for the candidate it reads, leads to its comparison and has no subtitle to export", () => {
    const judge = job({ kind: "judge", unit_id: 7, version: 4, mode: "judge" });
    expect(jobName(judge)).toBe("Judge variant · Arcane · S01E04 · vi");
    expect(readsOnly(judge)).toBe(true);
    expect(jobLink(judge)).toBe("/unit/7/compare/4?lang=vi");
    expect(jobStatus({ ...judge, progress_done: 30, progress_total: 70 }, "running")).toBe("manual · judge · judging 30/70");
    expect(jobStatus({ ...judge, result: "judged" }, "done")).toBe("manual · judge · verdicts kept");
    expect(jobStatus({ ...judge, result: "skipped-not-a-candidate" }, "done")).toBe("manual · judge · the version is no longer a candidate");
  });

  it("leads a job to its facts, its comparison or its subtitles", () => {
    expect(jobLink(job({ kind: "extract", unit_id: 7 }))).toBe("/unit/7/context");
    expect(jobLink(job({ unit_id: 7 }))).toBe("/unit/7/subtitles?lang=vi");
    expect(jobLink(job({ unit_id: 7, target_code: "" }))).toBe("/unit/7/subtitles");
    expect(jobLink(job({ kind: "judge", unit_id: 7 }))).toBe("/unit/7/subtitles?lang=vi"); // an older judge job without a version
  });

  it("says who asked and how, calling a variant a variant", () => {
    expect(originLine(job())).toBe("manual · fill");
    expect(originLine(job({ origin: "retranslate", mode: "replace" }))).toBe("retranslate · replace");
    expect(originLine(job({ trigger: "variant", mode: "replace" }))).toBe("manual · variant");
  });

  it("shows progress for a running job and the stage when it has none yet", () => {
    expect(jobStatus(job({ progress_done: 120, progress_total: 340 }), "running")).toBe("manual · fill · translating 120/340");
    expect(jobStatus(job({ stage: "reading" }), "running")).toBe("manual · fill · reading");
    expect(jobStatus(job(), "requested")).toBe("manual · fill · Requested");
  });

  it("says what a waiting job waits for, with when it tries again", () => {
    const retry = NOW / 1000 + 3600; // an hour from now: the same day in every zone but the last hour, so only check the wording
    expect(jobStatus(job({ wait_reason: "context", missing_units: ["S01E03"] }), "waiting", NOW)).toBe("manual · fill · Waiting · context (needs S01E03)");
    expect(jobStatus(job({ wait_reason: "quota", retry_at: retry }), "waiting", NOW)).toMatch(/^manual · fill · Waiting · quota · retry at \S+/);
    expect(jobStatus(job({ wait_reason: "rate" }), "waiting", NOW)).toBe("manual · fill · Waiting · provider busy");
  });

  it("gives a failed job its last error line and its restarts", () => {
    expect(jobStatus(job({ error: "Traceback\n  File x\nOutput token limit exceeded\n", attempts: 3 }), "failed")).toBe(
      "manual · fill · Failed · Output token limit exceeded · restarts 3",
    );
    expect(lastLine("")).toBe("");
    expect(lastLine(undefined)).toBe("");
  });

  it("names the file a finished job wrote", () => {
    expect(jobStatus(job({ result: "translated", output_path: "/media/Arcane/S01E04.vi.srt" }), "done")).toBe("manual · fill · translated · wrote S01E04.vi.srt");
  });

  it("opens on what is running, then what is in line, then what needs a person", () => {
    const counts = queueCounts({ done: 5 });
    expect(firstTab(counts)).toBe("done");
    expect(firstTab({ ...counts, deferred: 2 })).toBe("waiting");
    expect(firstTab({ ...counts, deferred: 2, failed: 1 })).toBe("failed");
    expect(firstTab({ ...counts, failed: 1, pending: 1 })).toBe("requested");
    expect(firstTab({ ...counts, pending: 1, processing: 1 })).toBe("running");
  });

  it("ignores a time that is not a time", () => {
    expect(whenText(null)).toBe("");
    expect(whenText(0)).toBe("");
    expect(whenText(9e17)).toBe("");
  });

  it("describes a provider that is out, and one that is fine", () => {
    expect(providerLine(provider())).toEqual({ head: "closed · usable · failures 0", detail: "" });
    expect(providerLine(provider({ state: "half_open", reason: "probing", failures: 2, usable: false }))).toEqual({
      head: "half open · not usable · failures 2",
      detail: "probing",
    });
  });

  it("lists the models that did work, and says how far back the counts reach", () => {
    expect(layerRows({ by_model: { a: { done: 4, failed: 1 } }, escalated: { total: 0, done: 0, failed: 0, waiting: 0 }, window: { done_days: 30, failed_days: 0 } })).toEqual([
      { model: "a", done: 4, failed: 1 },
    ]);
    expect(layerRows(undefined)).toEqual([]);
    expect(reach(30)).toBe("last 30 days");
    expect(reach(0)).toBe("all time");
  });
});
