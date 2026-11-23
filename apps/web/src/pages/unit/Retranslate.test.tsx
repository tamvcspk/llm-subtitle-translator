import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Correction } from "../../api/types";
import { buildRange, overwriteLine } from "../../lib/retranslate";
import { sent, serve, show } from "../../test/render";
import { base, correction, cue, cues, episode } from "../../test/unit-fixtures";

const estimate = { jobs: 1, cues: 31, calls: 2, calls_left_today: null, paused_until: null, minutes: null, finish_at: null, extract_calls: 0, extract_model: "" };

const items: Correction[] = [
  correction(3, "term", { source: "Enforcer", target: "Cảnh vệ" }),
  correction(4, "note", { text: "Whispered" }),
  correction(5, "cue_edit", { cue: 120, text: "x" }),
];

const routes = (extra: Record<string, unknown> = {}) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
  "/api/units/7/cues?lang=vi&offset=0&limit=1": { body: cues([cue(1)], { total: 340 }) },
  "/api/units/7/corrections?lang=vi": { body: { items } },
  ...extra,
});

describe("range form", () => {
  it("is checked against the cue count before anything is asked", () => {
    expect(buildRange("episode", { from: "", to: "" }, 340)).toEqual({ range: undefined });
    expect(buildRange("cues", { from: "100", to: "150" }, 340)).toEqual({ range: { cues: [100, 150] } });
    expect(buildRange("cues", { from: "150", to: "100" }, 340)).toEqual({ problem: "The first cue must be 1 or more, and not after the last." });
    expect(buildRange("cues", { from: "1", to: "400" }, 340)).toEqual({ problem: "This subtitle has 340 cues." });
    expect(buildRange("cues", { from: "", to: "5" }, 340)).toEqual({ problem: "Give the first and last cue as whole numbers." });
    expect(buildRange("time", { from: "10:00", to: "15:00" }, null)).toEqual({ range: { time: ["10:00", "15:00"] } });
    expect(buildRange("time", { from: "ten", to: "15:00" }, null)).toEqual({ problem: "Give both times as 14:20 or 00:14:20." });
  });

  it("says what it overwrites and what stays locked", () => {
    expect(overwriteLine("episode", undefined, 340, [10, 20])).toBe("Will overwrite 338 cues and keep 2 locked.");
    expect(overwriteLine("cues", { cues: [100, 150] }, 340, [120, 300])).toBe("Will overwrite 50 cues and keep 1 locked.");
    expect(overwriteLine("episode", undefined, 340, [])).toBe("Will overwrite 340 cues.");
    expect(overwriteLine("episode", undefined, null, [])).toBeNull();
  });
});

describe("Retranslate tab", () => {
  it("shows the price of the whole episode with the corrections attached, then queues it on Confirm", async () => {
    serve(
      routes({
        "/api/units/7/retranslate": (body: Record<string, unknown> | null) =>
          body?.estimate_only ? { body: { ok: true, estimate_only: true, range: null, estimate } } : { body: { ok: true, job_id: "j1" } },
      }),
    );
    show("/unit/7/retranslate");
    expect(await screen.findByText("Will overwrite 339 cues and keep 1 locked.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Term "Enforcer"/ })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: /Cue edit/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: /Note Whispered/ }));
    await userEvent.click(screen.getByRole("button", { name: "Retranslate" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("31")).toBeInTheDocument();
    expect(sent("/api/units/7/retranslate")).toEqual([{ lang: "vi", correction_ids: [3], estimate_only: true }]);

    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/1 job is in line/)).toBeInTheDocument();
    expect(sent("/api/units/7/retranslate")[1]).toEqual({ lang: "vi", correction_ids: [3] });
  });

  it("asks for a cue range and names it in the box", async () => {
    serve(routes({ "/api/units/7/retranslate": { body: { ok: true, estimate_only: true, range: { first: 100, last: 150 }, estimate } } }));
    show("/unit/7/retranslate");
    await userEvent.click(await screen.findByRole("radio", { name: "Cue range" }));
    await userEvent.click(screen.getByRole("button", { name: "Retranslate" }));
    expect(await screen.findByText("Give the first and last cue as whole numbers.")).toBeInTheDocument();
    expect(sent("/api/units/7/retranslate")).toEqual([]);

    await userEvent.type(screen.getByRole("textbox", { name: "From cue #" }), "100");
    await userEvent.type(screen.getByRole("textbox", { name: "To cue #" }), "150");
    expect(await screen.findByText("Will overwrite 50 cues and keep 1 locked.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retranslate" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText(/cues 100–150/)).toBeInTheDocument();
    await waitFor(() => expect(sent("/api/units/7/retranslate")[0]).toMatchObject({ range: { cues: [100, 150] }, estimate_only: true }));
  });

  it("shows the worker's refusal and cannot confirm it", async () => {
    serve(
      routes({
        "/api/units/7/retranslate": { status: 400, body: { error: "A retranslation of this episode is already queued" } },
      }),
    );
    show("/unit/7/retranslate");
    await userEvent.click(await screen.findByRole("button", { name: "Retranslate" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("A retranslation of this episode is already queued")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Confirm" })).toBeDisabled();
  });
});
