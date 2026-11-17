import { describe, expect, it } from "vitest";
import type { LogEntry } from "../api/queries";
import { filterLogs, levelColor, logTime } from "./logs";

const entry = (level: string, message: string, details = ""): LogEntry => ({ timestamp: "2026-11-17T21:58:01Z", level, message, details });
const lines = [entry("DEBUG", "cache hit"), entry("INFO", "S01E04 batch 3/9 translated"), entry("WARNING", "Key 1 exhausted"), entry("ERROR", "S01E07 failed", "Traceback\nValueError: boom")];

describe("logs", () => {
  it("keeps the chosen level and worse", () => {
    expect(filterLogs(lines, "ALL", "")).toHaveLength(4);
    expect(filterLogs(lines, "DEBUG", "")).toHaveLength(4);
    expect(filterLogs(lines, "WARNING", "").map((line) => line.level)).toEqual(["WARNING", "ERROR"]);
    expect(filterLogs(lines, "ERROR", "").map((line) => line.level)).toEqual(["ERROR"]);
  });

  it("searches the line and its trace, ignoring case", () => {
    expect(filterLogs(lines, "ALL", "EXHAUSTED").map((line) => line.message)).toEqual(["Key 1 exhausted"]);
    expect(filterLogs(lines, "ALL", "valueerror").map((line) => line.message)).toEqual(["S01E07 failed"]);
    expect(filterLogs(lines, "ERROR", "exhausted")).toEqual([]);
  });

  it("counts a level it does not know as an information line", () => {
    expect(filterLogs([entry("NOTICE", "x")], "INFO", "")).toHaveLength(1);
    expect(filterLogs([entry("NOTICE", "x")], "WARNING", "")).toHaveLength(0);
    expect(filterLogs([entry("CRITICAL", "x")], "ERROR", "")).toHaveLength(1);
  });

  it("writes a time in the viewer's zone, and nothing for a line with none", () => {
    expect(logTime("2026-11-17T21:58:01Z")).toMatch(/^\d\d:\d\d:\d\d$/);
    expect(logTime("")).toBe("");
    expect(logTime("not a time")).toBe("");
  });

  it("colours the bad levels", () => {
    expect(levelColor("ERROR")).toBe("red");
    expect(levelColor("WARNING")).toBe("orange");
    expect(levelColor("INFO")).toBe("dimmed");
  });
});
