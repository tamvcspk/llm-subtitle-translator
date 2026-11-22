import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Version } from "../../api/types";
import { versionActions } from "../../lib/history";
import { sent, serve, show } from "../../test/render";
import { base, cue, cues, episode } from "../../test/unit-fixtures";

const version = (n: number, overrides: Partial<Version> = {}): Version => ({
  version: n,
  trigger: "retranslate",
  origin: "machine",
  status: "superseded",
  parent_version: n > 1 ? n - 1 : null,
  model: "gemini-3.1-flash-lite",
  sha256: "abc123",
  tokens_in: 100,
  tokens_out: 14200,
  created_at: 1_800_000_000,
  current: false,
  params: {},
  ...overrides,
});

const list = [
  version(4, { trigger: "variant", status: "candidate", params: { variant: { label: "gritty", preset: "", style: {} } } }),
  version(3, { trigger: "cue_edit", origin: "edit", status: "current", current: true }),
  version(2),
  version(1, { trigger: "auto" }),
];

const routes = (extra: Record<string, unknown> = {}) => ({
  ...base,
  "/api/units/7": { body: episode() },
  "/api/units/7/cues?lang=vi&offset=0&limit=50": { body: cues([cue(1)]) },
  "/api/units/7/versions?lang=vi": { body: { lang: "vi", modified_outside: false, versions: list } },
  ...extra,
});

describe("what a line of History offers", () => {
  it("follows the status of the version", () => {
    expect(versionActions(version(4, { trigger: "variant", status: "candidate" }))).toEqual(["diff", "compare", "use"]);
    expect(versionActions(version(5, { status: "candidate", trigger: "retranslate" }))).toEqual(["diff", "use"]);
    expect(versionActions(version(3, { status: "current", current: true }))).toEqual(["diff"]);
    expect(versionActions(version(2))).toEqual(["diff", "restore"]);
    expect(versionActions(version(2, { status: "removed" }))).toEqual(["diff", "restore"]);
    expect(versionActions(version(1))).toEqual(["restore"]);
    expect(versionActions(version(2, { status: "pruned" }))).toEqual([]);
  });
});

describe("History tab", () => {
  it("lists the versions newest first with their badges and the variant's label", async () => {
    serve(routes());
    show("/unit/7/history");
    const rows = within(await screen.findByRole("list", { name: "Versions" })).getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(within(rows[0]!).getByText("Candidate")).toBeInTheDocument();
    expect(within(rows[0]!).getByText('Variant "gritty"')).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Current")).toBeInTheDocument();
    expect(within(rows[0]!).getByRole("link", { name: "Compare v4" })).toHaveAttribute("href", "/unit/7/compare/4?lang=vi");
    expect(within(rows[1]!).queryByRole("button", { name: /Restore/ })).not.toBeInTheDocument();
  });

  it("shows what a version changed, and reads it only when asked", async () => {
    serve(
      routes({
        "/api/units/7/versions/2/diff?lang=vi": {
          body: {
            version: 2,
            against: 1,
            changed_cues: 1,
            changes: [{ op: "replace", before: [{ n: 5, index: 5, time: "00:00:05", text: "Old line" }], after: [{ n: 5, index: 5, time: "00:00:05", text: "New line" }] }],
          },
        },
      }),
    );
    show("/unit/7/history");
    await screen.findByRole("list", { name: "Versions" });
    expect(sent("/api/units/7/versions/2/diff?lang=vi")).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Diff v2" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("− Old line")).toBeInTheDocument();
    expect(within(sheet).getByText("+ New line")).toBeInTheDocument();
    expect(within(sheet).getByText(/1 cue differs from v1/)).toBeInTheDocument();
  });

  it("restores an older version only after a confirmation", async () => {
    serve(routes({ "/api/units/7/versions/2/revert": { body: { ok: true, version: 5, restored: 2 } } }));
    show("/unit/7/history");
    await userEvent.click(await screen.findByRole("button", { name: "Restore v2" }));
    const sheet = await screen.findByRole("dialog");
    expect(sent("/api/units/7/versions/2/revert")).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(sent("/api/units/7/versions/2/revert")).toEqual([{ lang: "vi" }]));
  });

  it("adopts a candidate after a confirmation and reports a refusal", async () => {
    serve(routes({ "/api/units/7/versions/4/adopt": { status: 400, body: { error: "the file changed again" } } }));
    show("/unit/7/history");
    await userEvent.click(await screen.findByRole("button", { name: "Use v4" }));
    const sheet = await screen.findByRole("dialog");
    await userEvent.click(within(sheet).getByRole("button", { name: "Use this version" }));
    expect(await within(sheet).findByText("the file changed again")).toBeInTheDocument();
    expect(sent("/api/units/7/versions/4/adopt")).toEqual([{ lang: "vi" }]);
  });

  it("says so when nothing has been translated yet", async () => {
    serve(routes({ "/api/units/7/versions?lang=vi": { body: { lang: "vi", modified_outside: false, versions: [] } } }));
    show("/unit/7/history");
    expect(await screen.findByText(/No version yet/)).toBeInTheDocument();
  });
});
