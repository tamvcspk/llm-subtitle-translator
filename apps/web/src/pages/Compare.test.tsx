import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CompareRow } from "../api/types";
import { styleLines, tally, verdicts, visibleRows } from "../lib/compare";
import { sent, serve, show } from "../test/render";
import { base, episode } from "../test/unit-fixtures";

const row = (n: number, differs: boolean): CompareRow => ({
  n,
  source: `Source ${n}`,
  current: `Current ${n}`,
  variant: differs ? `Variant ${n}` : `Current ${n}`,
  differs,
});

const rows = [row(1, true), row(2, false), row(3, true)];

const compare = (overrides: Record<string, unknown> = {}) => ({
  lang: "vi",
  version: 4,
  status: "candidate",
  params: { variant: { label: "gritty", preset: "drama", style: { formality: "casual", profanity: "keep" } } },
  model: "flash",
  aligned: true,
  differing: 2,
  rows,
  ...overrides,
});

const versions = {
  lang: "vi",
  modified_outside: false,
  versions: [
    { version: 4, trigger: "variant", origin: "machine", status: "candidate", parent_version: 3, model: "flash", tokens_in: 1, tokens_out: 1, created_at: 1, current: false, params: {} },
    { version: 3, trigger: "auto", origin: "machine", status: "current", parent_version: null, model: "flash", tokens_in: 1, tokens_out: 1, created_at: 1, current: true, params: {} },
  ],
};

const routes = (extra: Record<string, unknown> = {}) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/versions/4/compare?lang=vi": { body: compare() },
  "/api/units/7/versions?lang=vi": { body: versions },
  ...extra,
});

describe("compare helpers", () => {
  it("show only the cues that differ, unless asked for all", () => {
    expect(visibleRows(rows, true).map((r) => r.n)).toEqual([1, 3]);
    expect(visibleRows(rows, false)).toHaveLength(3);
  });

  it("give a verdict on every differing cue: the picked ones to the variant, the rest to the current text", () => {
    expect(verdicts(rows, new Set([1]))).toEqual({ "1": "variant", "3": "current" });
    expect(tally(rows, new Set([1, 2]))).toEqual({ fromVariant: 1, kept: 2 }); // cue 2 does not differ: it cannot come from the variant
  });

  it("name what a variant sets, and leave out what it does not", () => {
    expect(styleLines("drama", { formality: "casual", allow_condense: false, tone: "dark" }, "Drama")).toEqual([
      ["Preset", "Drama"],
      ["Formality", "Casual"],
      ["Fast dialogue", "Don't"],
      ["Tone", "dark"],
    ]);
    expect(styleLines("", undefined)).toEqual([["Preset", "—"]]);
  });
});

describe("Compare page", () => {
  it("shows the two versions in the title and only the cues that differ", async () => {
    serve(routes());
    show("/unit/7/compare/4?lang=vi");
    expect(await screen.findByRole("heading", { name: 'Current v3 vs Candidate v4 "gritty"' })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Cues" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "Show only differing cues (2 of 3)" })).toBeChecked();
    expect(screen.getByText("Selected 0 from variant, 3 kept from current")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Show only differing/ }));
    expect(within(screen.getByRole("list", { name: "Cues" })).getAllByRole("listitem")).toHaveLength(3);
  });

  it("applies the picked cues and records the rest as kept", async () => {
    serve(routes({ "/api/units/7/versions/4/choose": { body: { ok: true, recorded: 2, applied: {} } } }));
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Pick variant for cue 1" }));
    expect(screen.getByText("Selected 1 from variant, 2 kept from current")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Apply picked cues" }));
    await waitFor(() =>
      expect(sent("/api/units/7/versions/4/choose")).toEqual([{ lang: "vi", picks: { "1": "variant", "3": "current" }, apply: true }]),
    );
    expect(await screen.findByText(/Done\./)).toBeInTheDocument();
  });

  it("cannot apply with nothing picked", async () => {
    serve(routes());
    show("/unit/7/compare/4?lang=vi");
    expect(await screen.findByRole("button", { name: "Apply picked cues" })).toBeDisabled();
  });

  it("takes the whole version with its style after a confirmation that counts what it flags", async () => {
    serve(
      routes({
        "/api/units/7/versions/4/adopt": { body: { ok: true, adopted: null, stale_now: 0, stale_after: 11, would_stale: 11, style: { preset: "drama", style: {} } } },
        "/api/units/7/versions/4/choose": { body: { ok: true, recorded: 1, applied: { adopted: 4 } } },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Use this version" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("checkbox", { name: "Also apply this variant's style to the series" })).toBeChecked();
    expect(within(sheet).getByText("Casual")).toBeInTheDocument();
    expect(await within(sheet).findByText(/Saving flags 11 episodes stale. Nothing is retranslated./)).toBeInTheDocument();
    expect(sent("/api/units/7/versions/4/adopt")).toEqual([{ lang: "vi", apply_style: true, dry_run: true }]);
    expect(sent("/api/units/7/versions/4/choose")).toEqual([]);

    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/units/7/versions/4/choose")).toEqual([{ lang: "vi", whole: "variant", apply: true, apply_style: true }]));
  });

  it("takes only the subtitle when the style box is unticked", async () => {
    serve(
      routes({
        "/api/units/7/versions/4/adopt": { body: { ok: true, adopted: null, stale_now: 0, stale_after: 0, would_stale: 0 } },
        "/api/units/7/versions/4/choose": { body: { ok: true, recorded: 1, applied: {} } },
      }),
    );
    show("/unit/7/compare/4?lang=vi");
    await userEvent.click(await screen.findByRole("button", { name: "Use this version" }));
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("checkbox", { name: "Also apply this variant's style to the series" }));
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(sent("/api/units/7/versions/4/choose")).toEqual([{ lang: "vi", whole: "variant", apply: true }]));
  });

  it("only looks at a version that is no longer a candidate", async () => {
    serve(routes({ "/api/units/7/versions/4/compare?lang=vi": { body: compare({ status: "superseded" }) } }));
    show("/unit/7/compare/4?lang=vi");
    expect(await screen.findByText("This version is superseded, so it can only be looked at.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use this version" })).not.toBeInTheDocument();
  });
});
