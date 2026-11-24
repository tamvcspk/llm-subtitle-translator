import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { saveHomeScroll } from "../lib/navigation";
import { movie, series, target } from "../test/fixtures";
import { sent, serve, show } from "../test/render";

const status = { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 };

const base = {
  "/api/status": {
    body: {
      queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 },
      daily_quota_pause: null,
      providers: [],
      settings: { key_warnings: [] },
    },
  },
  "/api/catalog/status": { body: status },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
};

function catalog(rows: ReturnType<typeof series>[], movies: ReturnType<typeof movie>[] = []) {
  return {
    ...base,
    "/api/catalog": {
      body: { status, languages: ["vi"], series: rows, movies: { count: movies.length, missing: {}, failed: {}, ignored: {} } },
    },
    "/api/catalog/movies": { body: { status, languages: ["vi"], items: movies } },
  };
}

const estimate = {
  estimate_only: true,
  estimate: { jobs: 5, cues: 2740, calls: 16, calls_left_today: 42, paused_until: null, minutes: 4, finish_at: null, extract_calls: 0, extract_model: "" },
  planned_total: 5,
  skipped_total: 1,
  skipped_by_reason: { "already requested": 1 },
};

describe("Home", () => {
  it("lists series and movies by name with one button each", async () => {
    serve(catalog([series({ title: "Severance", missing: { vi: 5 } }), series({ id: 2, title: "Shogun" })], [movie()]));
    show("/library");
    const list = await screen.findByRole("list", { name: "Library" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((item) => item.querySelector("a")?.textContent)).toEqual(["Oppenheimer", "Severance", "Shogun"]);
    expect(within(items[1]!).getByRole("button", { name: "Translate missing" })).toBeInTheDocument();
    expect(within(items[2]!).getByRole("link", { name: "Improve" })).toHaveAttribute("href", "/series/2/style");
  });

  it("narrows by search and by chip, and can clear them", async () => {
    serve(catalog([series({ title: "Severance", missing: { vi: 5 } }), series({ id: 2, title: "Shogun" })]));
    show("/library");
    await screen.findByRole("list", { name: "Library" });
    await userEvent.click(screen.getByRole("radio", { name: "Missing Vietnamese" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    await userEvent.type(screen.getByRole("textbox", { name: "Search by title" }), "shogun");
    expect(screen.getByText(/No items match "shogun" in Missing Vietnamese/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filter" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("says so when the catalog is empty", async () => {
    serve(catalog([]));
    show("/library");
    expect(await screen.findByText("No series or movies in the catalog yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open Connections" })).toBeInTheDocument();
  });

  it("shows the price first, and only then makes the jobs", async () => {
    serve({
      ...catalog([series({ title: "Severance", missing: { vi: 5 } })]),
      "/api/jobs": (body) =>
        body?.estimate_only ? { body: estimate } : { body: { ...estimate, estimate_only: false, created: ["a", "b"], count: 2 } },
    });
    show("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Translate missing" }));

    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("2,740")).toBeInTheDocument();
    expect(within(sheet).getByText("Calls left today")).toBeInTheDocument();
    expect(within(sheet).getByText(/Left out: 1 already requested/)).toBeInTheDocument();
    // Nothing was made by merely asking.
    expect(sent("/api/jobs")).toEqual([{ series: 1, estimate_only: true }]);

    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/2 jobs are in line/)).toBeInTheDocument();
    expect(sent("/api/jobs")[1]).toEqual({ series: 1 });
  });

  it("cannot confirm an estimate of zero jobs", async () => {
    serve({
      ...catalog([], [movie()]),
      "/api/jobs": { body: { ...estimate, estimate: { ...estimate.estimate, jobs: 0 } } },
    });
    show("/library");
    await userEvent.click(await screen.findByRole("button", { name: "Translate missing" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("Nothing to translate.")).toBeInTheDocument();
    expect(within(sheet).getByRole("button", { name: "Confirm" })).toBeDisabled();
  });

  it("has no Translate button on a movie whose job is open", async () => {
    serve(catalog([], [movie({ targets: [target({ job: "running", text: "Missing · Running" })] })]));
    show("/library");
    await screen.findByText("Missing · Running");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Translate missing" })).not.toBeInTheDocument());
  });

  it("initialises search and filter from URL query params and restores saved scroll", async () => {
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    saveHomeScroll(320);
    serve(catalog([series({ title: "Severance", missing: { vi: 5 } }), series({ id: 2, title: "Shogun" })]));
    show("/library?q=sev&filter=missing");
    const list = await screen.findByRole("list", { name: "Library" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: "Search by title" })).toHaveValue("sev");
    expect(screen.getByRole("radio", { name: "Missing Vietnamese" })).toBeChecked();
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 320));
  });
});
