import { describe, expect, it } from "vitest";
import type { QueueJob } from "../api/types";
import { firstTab, jobName, jobStatus, lastLine, layerRows, originLine, providerLine, reach, whenText } from "./activity";

const NOW = Date.UTC(2026, 10, 17, 12, 0, 0);
const job = (extra: Partial<QueueJob> = {}): QueueJob => ({ job_id: "j1", title: "Arcane", label: "S01E04", target_code: "vi", origin: "manual", mode: "fill", ...extra });

describe("Activity", () => {
  it("names a job by its series, episode and language, and an older one by its file", () => {
    expect(jobName(job())).toBe("Arcane · S01E04 · vi");
    expect(jobName(job({ title: "Heat", label: "" }))).toBe("Heat · vi");
    expect(jobName(job({ title: "", label: "", subtitle_path: "/media/Heat/Heat.en.srt" }))).toBe("Heat.en.srt · vi");
    expect(jobName(job({ title: "", label: "", subtitle_path: "", target_code: "" }))).toBe("j1");
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
    const counts = { pending: 0, processing: 0, deferred: 0, done: 5, failed: 0 };
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
    expect(providerLine({ model: "m", state: "closed", reason: "", until: null, failures: 0, usable: true })).toEqual({ head: "closed · usable · failures 0", detail: "" });
    expect(providerLine({ model: "m", state: "half_open", reason: "probing", until: null, failures: 2, usable: false })).toEqual({
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
