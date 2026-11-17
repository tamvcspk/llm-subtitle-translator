import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Suggestion, SuggestionList } from "../api/types";
import { sent, serve, show } from "../test/render";

const suggestion = (id: number, extra: Partial<Suggestion> = {}): Suggestion => ({
  id,
  series_id: 7,
  series: "Arcane",
  lang: "vi",
  kind: "term",
  source: "facts",
  payload: { source: "Kyber crystal", target: "tinh thể Kyber" },
  evidence: [{ episode: "S01E05", cue: 5, timecode: "00:00:08,000", text: "The Kyber crystal glows", unit_id: 505 }],
  sources: [],
  status: "pending",
  auto_accepted: false,
  decided_at: 0,
  created_at: 1_900_000_000,
  undoable: false,
  edited: false,
  ...extra,
});

const rule = suggestion(2, {
  kind: "address_rule",
  source: "memory",
  payload: { a: "Vi", b: "Jinx", a_to_b: "chị", b_to_a: "em" },
  evidence: ["Vi: “Come here, kid.”"],
});
const other = suggestion(3, { series_id: 9, series: "Severance", payload: { source: "Lumon", target: "Lumon" } });

function list(items: Suggestion[], counts = { pending: items.length, accepted: 0, rejected: 0 }): SuggestionList {
  const per = new Map<number, { series_id: number; series: string; pending: number }>();
  for (const item of items.filter((i) => i.status === "pending")) {
    const row = per.get(item.series_id) ?? { series_id: item.series_id, series: item.series, pending: 0 };
    row.pending += 1;
    per.set(item.series_id, row);
  }
  return { items, counts, pending: counts.pending, series: [...per.values()] };
}

const shell = {
  "/api/status": { body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } } },
  "/api/catalog/status": { body: { synced_at: Date.now() / 1000, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
};

const impact = (would: number, saved = false) => ({ body: { ok: true, saved, stale_now: 0, stale_after: would, would_stale: would } });

/** The worker's side of the inbox: a dry run answers with a count and never writes; a real accept does. */
function inbox(extra: Record<string, unknown> = {}, items: Suggestion[] = [suggestion(1), rule, other]) {
  return {
    ...shell,
    "/api/suggestions/summary": { body: list(items) },
    "/api/suggestions?status=pending": { body: list(items) },
    "/api/suggestions?status=accepted": { body: list([], { pending: 0, accepted: 0, rejected: 0 }) },
    "/api/suggestions?status=rejected": { body: list([], { pending: 0, accepted: 0, rejected: 0 }) },
    "/api/suggestions/1/accept": (body: Record<string, unknown> | null) =>
      body?.dry_run
        ? impact(body.payload ? 2 : 12)
        : { body: { ...impact(12, true).body, suggestion: suggestion(1, { status: "accepted", decided_at: 1, undoable: true }) } },
    "/api/suggestions/2/accept": () => impact(0),
    "/api/suggestions/3/accept": () => impact(4),
    ...extra,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Review", () => {
  it("groups what is waiting by series, with its kind, source and evidence", async () => {
    serve(inbox());
    show("/review");
    const arcane = await screen.findByRole("region", { name: "Arcane" });
    expect(within(arcane).getByRole("link", { name: "Arcane" })).toHaveAttribute("href", "/series/7/glossary");
    expect(within(arcane).getByText("2 suggestions waiting")).toBeInTheDocument();
    const term = within(arcane).getByRole("listitem", { name: "Term: Kyber crystal → tinh thể Kyber" });
    expect(within(term).getByText("Story facts")).toBeInTheDocument();
    expect(within(term).getByText("The Kyber crystal glows")).toBeInTheDocument();
    expect(within(term).getByRole("link", { name: "S01E05 · cue 5 · 00:00:08,000" })).toHaveAttribute("href", "/unit/505/subtitles");
    const address = within(arcane).getByRole("listitem", { name: "Form of address: Vi ↔ Jinx" });
    expect(within(address).getByText("Vi calls Jinx “chị”")).toBeInTheDocument();
    expect(within(address).getByText("Vi: “Come here, kid.”")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Severance" })).getByText("1 suggestion waiting")).toBeInTheDocument();
  });

  it("says how many translations accepting each one flags stale, from the worker's dry run, with no Check button", async () => {
    serve(inbox());
    show("/review");
    const term = await screen.findByRole("listitem", { name: "Term: Kyber crystal → tinh thể Kyber" });
    expect(await within(term).findByText("Accepting flags 12 translations stale. Nothing is redone.")).toBeInTheDocument();
    expect(await within(screen.getByRole("listitem", { name: "Form of address: Vi ↔ Jinx" })).findByText("No translation is affected.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /check/i })).toBeNull();
    expect(sent("/api/suggestions/1/accept")[0]).toEqual({ dry_run: true });
  });

  it("accepts with one tap, says what it did, and Undo puts it back", async () => {
    serve(inbox({ "/api/suggestions/1/undo": { body: { ...impact(12, true).body, suggestion: suggestion(1) } } }));
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Accept Kyber crystal → tinh thể Kyber" }));
    const notice = await screen.findByText(/Accepted “Kyber crystal → tinh thể Kyber”\. 12 translations are now Stale; none is redone\./);
    await waitFor(() => expect(sent("/api/suggestions/1/accept").filter((body) => !body?.dry_run)).toEqual([{}]));
    await userEvent.click(within(notice.closest("[role=status]") as HTMLElement).getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(sent("/api/suggestions/1/undo")).toHaveLength(1));
    expect(await screen.findByText(/Undone: “Kyber crystal → tinh thể Kyber” is back in the inbox\./)).toBeInTheDocument();
  });

  it("edits then accepts: the count follows the edit and the edited text is what is sent", async () => {
    serve(inbox());
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Kyber crystal → tinh thể Kyber" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit, then accept" });
    const target = within(sheet).getByRole("textbox", { name: "Translation" });
    await userEvent.clear(target);
    await userEvent.type(target, "đá Kyber");
    expect(await within(sheet).findByText("Accepting flags 2 translations stale. Nothing is redone.")).toBeInTheDocument();
    expect(sent("/api/suggestions/1/accept").at(-1)).toEqual({ dry_run: true, payload: { source: "Kyber crystal", target: "đá Kyber" } });
    await userEvent.click(within(sheet).getByRole("button", { name: "Accept edited" }));
    await waitFor(() =>
      expect(sent("/api/suggestions/1/accept").filter((body) => !body?.dry_run)).toEqual([{ payload: { source: "Kyber crystal", target: "đá Kyber" } }]),
    );
    expect(await screen.findByText(/Accepted “Kyber crystal → tinh thể Kyber”/)).toBeInTheDocument();
  });

  it("will not accept an edit that leaves a box empty", async () => {
    serve(inbox());
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Vi ↔ Jinx" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit, then accept" });
    await userEvent.clear(within(sheet).getByRole("textbox", { name: "First calls second" }));
    await userEvent.clear(within(sheet).getByRole("textbox", { name: "Second calls first" }));
    expect(within(sheet).getByText("Say how at least one of them addresses the other.")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Accept edited" })).toBeDisabled();
  });

  it("rejects, and says it will not be proposed again", async () => {
    serve(inbox({ "/api/suggestions/3/reject": { body: { ok: true, suggestion: suggestion(3, { status: "rejected" }) } } }));
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Reject Lumon → Lumon" }));
    expect(await screen.findByText("Rejected “Lumon → Lumon”. It will not be proposed again.")).toBeInTheDocument();
    expect(sent("/api/suggestions/3/reject")).toHaveLength(1);
  });

  it("accepts all of one series after showing the number and what it would flag, and sends the number it showed", async () => {
    serve(
      inbox({
        "/api/suggestions/bulk": (body: Record<string, unknown> | null) =>
          body?.dry_run
            ? { body: { ok: true, saved: false, count: 2, would_stale: 12 } }
            : { body: { ok: true, saved: true, count: 2, would_stale: 12, failed: [], series: "Arcane" } },
      }),
    );
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Accept all in Arcane" }));
    const sheet = await screen.findByRole("dialog", { name: "Accept all · Arcane" });
    expect(within(sheet).getByText(/Accept 2 suggestions for Arcane\?/)).toBeInTheDocument();
    expect(await within(sheet).findByText("Accepting flags 12 translations stale. Nothing is redone.")).toBeInTheDocument();
    expect(sent("/api/suggestions/bulk")[0]).toEqual({ series_id: 7, dry_run: true });
    await userEvent.click(within(sheet).getByRole("button", { name: "Accept 2" }));
    await waitFor(() => expect(sent("/api/suggestions/bulk")).toContainEqual({ series_id: 7, expect: 2 }));
    expect(await screen.findByText(/Accepted 2 suggestions for Arcane\. 12 translations are now Stale; none is redone\./)).toBeInTheDocument();
  });

  it("stops the accept-all when the list has changed since the number was shown", async () => {
    serve(inbox({ "/api/suggestions/bulk": { body: { ok: true, saved: false, count: 3, would_stale: 1 } } }));
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Accept all in Arcane" }));
    const sheet = await screen.findByRole("dialog", { name: "Accept all · Arcane" });
    expect(await within(sheet).findByText(/The list has changed: 3 suggestions are waiting now/)).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Accept 2" })).toBeDisabled();
  });

  it("keeps what was decided in History, marks the ones auto-accepted, and undoes them", async () => {
    const auto = suggestion(5, { status: "accepted", auto_accepted: true, undoable: true, decided_at: 1_900_000_100, source: "research" });
    const edited = suggestion(6, { status: "accepted", edited: true, undoable: true, decided_at: 1_900_000_050, payload: { source: "Gem", target: "Ngọc" } });
    const no = suggestion(7, { status: "rejected", decided_at: 1_900_000_000, payload: { source: "Zed", target: "Zed" } });
    serve(
      inbox({
        "/api/suggestions/summary": { body: list([], { pending: 0, accepted: 2, rejected: 1 }) },
        "/api/suggestions?status=accepted": { body: list([auto, edited], { pending: 0, accepted: 2, rejected: 1 }) },
        "/api/suggestions?status=rejected": { body: list([no], { pending: 0, accepted: 2, rejected: 1 }) },
        "/api/suggestions/5/undo": { body: { ...impact(3, true).body, suggestion: { ...auto, status: "pending", auto_accepted: false } } },
      }),
    );
    show("/review?tab=history");
    const item = await screen.findByRole("listitem", { name: "Term: Kyber crystal → tinh thể Kyber" });
    expect(within(item).getByText("Auto")).toBeInTheDocument();
    expect(within(item).getByText("Research")).toBeInTheDocument();
    expect(screen.getByText("Edited")).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "Term: Zed → Zed" })).toHaveTextContent("not proposed again");
    const undos = screen.getAllByRole("button", { name: /^Undo/ });
    expect(undos).toHaveLength(2); // only the accepted ones
    await userEvent.click(undos[0] as HTMLElement);
    await waitFor(() => expect(sent("/api/suggestions/5/undo")).toHaveLength(1));
    expect(await screen.findByText(/is back in the inbox\. 3 translations are Stale again; none is redone\./)).toBeInTheDocument();
  });

  it("says so when nothing is waiting", async () => {
    serve(inbox({}, []));
    show("/review");
    expect(await screen.findByText("Nothing is waiting for review.")).toBeInTheDocument();
  });

  it("shows the error when a decision is refused", async () => {
    serve(inbox({ "/api/suggestions/3/reject": { status: 400, body: { error: "this suggestion was already accepted" } } }));
    show("/review");
    await userEvent.click(await screen.findByRole("button", { name: "Reject Lumon → Lumon" }));
    expect(await screen.findByText("this suggestion was already accepted")).toBeInTheDocument();
  });
});
