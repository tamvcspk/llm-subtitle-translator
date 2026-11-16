import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { episode, seriesPage } from "../../test/fixtures";
import { sent, serve, show } from "../../test/render";

const base = {
  "/api/status": {
    body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
  },
  "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
  "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
  "/api/catalog/series/1": { body: seriesPage([episode(1), episode(2, { has_source: false })]) },
  "/api/series/9/style/suggestions": { body: { genres: ["Animation", "Drama"], presets: [{ id: "drama", name: "Drama" }] } },
};

const overview = {
  series: { id: 9, title: "Arcane" },
  lang: "vi",
  preset: "",
  style: {},
  effective: { formality: "neutral", profanity: "soften", localization: "balanced", tone: "plain" },
  presets: [
    { id: "drama", name: "Drama", genres: ["Drama"], style: {}, builtin: true },
    { id: "sitcom", name: "Sitcom", genres: ["Comedy"], style: {}, builtin: true },
  ],
  cards: [],
};

function style(extra: Record<string, unknown> = {}) {
  return {
    ...base,
    "/api/series/9/style?lang=vi": (body: Record<string, unknown> | null) =>
      body === null
        ? { body: overview }
        : { body: { ok: true, saved: body.dry_run !== true, stale_now: 0, stale_after: 3, would_stale: 3 } },
    ...extra,
  };
}

describe("Series Style tab", () => {
  it("starts every axis at Inherit and shows what that is now", async () => {
    serve(style());
    show("/series/1/style");
    expect(await screen.findByRole("radio", { name: "Inherit · now Neutral" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Inherit · now Soften" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "Tone" })).toHaveAttribute("placeholder", "Inherit · now plain");
    expect(screen.getByText("No changes.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("counts what a change would flag, live, and saves only after a confirmation", async () => {
    serve(style());
    show("/series/1/style");
    await userEvent.click(await screen.findByRole("radio", { name: "Casual" }));
    expect(await screen.findByText("Saving flags 3 episodes stale.")).toBeInTheDocument();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    expect(sent("/api/series/9/style?lang=vi").filter((body) => body !== null)).toEqual([
      { preset: "", style: { formality: "casual" }, dry_run: true },
    ]);

    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByText(/Saving flags 3 episodes stale. Nothing is retranslated./)).toBeInTheDocument();
    expect(sent("/api/series/9/style?lang=vi").filter((body) => body?.dry_run !== true && body !== null)).toEqual([]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(sent("/api/series/9/style?lang=vi").filter((body) => body !== null && body.dry_run !== true)).toEqual([
        { preset: "", style: { formality: "casual" } },
      ]),
    );
  });

  it("saves at once when nothing is affected", async () => {
    serve({
      ...style(),
      "/api/series/9/style?lang=vi": (body: Record<string, unknown> | null) =>
        body === null ? { body: overview } : { body: { ok: true, saved: body.dry_run !== true, stale_now: 0, stale_after: 0, would_stale: 0 } },
    });
    show("/series/1/style");
    await userEvent.click(await screen.findByRole("radio", { name: "Formal" }));
    expect(await screen.findByText("No translation is affected.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(sent("/api/series/9/style?lang=vi").some((body) => body !== null && body.dry_run !== true)).toBe(true));
  });

  it("offers the preset TMDB suggests and applies it to the draft", async () => {
    serve(style());
    show("/series/1/style");
    expect(await screen.findByText(/Suggested for Animation, Drama/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: 'Apply "Drama"' }));
    expect(await screen.findByText("Unsaved changes")).toBeInTheDocument();
    await waitFor(() =>
      expect(sent("/api/series/9/style?lang=vi").filter((body) => body !== null)[0]).toEqual({ preset: "drama", style: {}, dry_run: true }),
    );
  });

  it("tries 20 lines on the draft, shows Now beside the preview, and writes nothing", async () => {
    serve(
      style({
        "/api/series/9/style/preview": {
          body: {
            rows: [{ n: 1, source: "You think you can walk in?", current: "Cậu nghĩ cậu bước vào à?", preview: "Mày tưởng bước vào được à?" }],
            model: "flash",
            tokens_in: 900,
            tokens_out: 200,
          },
        },
      }),
    );
    show("/series/1/style");
    await userEvent.click(await screen.findByRole("radio", { name: "Casual" }));
    await userEvent.click(screen.getByRole("button", { name: "Try 20 lines (≈ 1 call)" }));
    const list = await screen.findByRole("list", { name: "Preview" });
    expect(within(list).getByText("Mày tưởng bước vào được à?")).toBeInTheDocument();
    expect(within(list).getByText("Cậu nghĩ cậu bước vào à?")).toBeInTheDocument();
    expect(sent("/api/series/9/style/preview")).toEqual([{ lang: "vi", preset: "", style: { formality: "casual" } }]);
  });

  it("hides Now when there is no translation yet, and says when the preview failed", async () => {
    serve(
      style({
        "/api/series/9/style/preview": {
          body: { rows: [{ n: 1, source: "Welcome to the lanes", current: null, preview: "Chào mừng" }], model: "flash", tokens_in: 1, tokens_out: 1 },
        },
      }),
    );
    show("/series/1/style");
    await userEvent.click(await screen.findByRole("button", { name: "Try 20 lines (≈ 1 call)" }));
    const list = await screen.findByRole("list", { name: "Preview" });
    expect(within(list).queryByText("Now")).not.toBeInTheDocument();
    expect(within(list).getByText(/Preview/)).toBeInTheDocument();
  });

  it("shows the refusal of a preview that failed", async () => {
    serve(style({ "/api/series/9/style/preview": { status: 400, body: { error: "QuotaExhausted: spent" } } }));
    show("/series/1/style");
    await userEvent.click(await screen.findByRole("button", { name: "Try 20 lines (≈ 1 call)" }));
    expect(await screen.findByText("Preview failed: QuotaExhausted: spent")).toBeInTheDocument();
  });

  it("asks the price of a whole-episode comparison before it queues the variant", async () => {
    serve(
      style({
        "/api/units/501/variant": (body: Record<string, unknown> | null) =>
          body?.estimate_only
            ? { body: { ok: true, estimate_only: true, label: "gritty", estimate: { jobs: 1, cues: 340, calls: 9, calls_left_today: null, paused_until: null, minutes: null, finish_at: null } } }
            : { body: { ok: true, job_id: "v1", label: "gritty" } },
      }),
    );
    show("/series/1/style");
    await userEvent.click(await screen.findByRole("radio", { name: "Casual" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Label" }), "gritty");
    await userEvent.click(screen.getByRole("button", { name: "Compare on a whole episode" }));
    const sheet = await screen.findByRole("dialog");
    expect(await within(sheet).findByText("340")).toBeInTheDocument();
    // Only an episode with a source subtitle can be compared.
    expect(sent("/api/units/501/variant")[0]).toEqual({ lang: "vi", label: "gritty", preset: "", style: { formality: "casual" }, estimate_only: true });
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/1 job is in line/)).toBeInTheDocument();
    expect(sent("/api/units/501/variant")[1]).toEqual({ lang: "vi", label: "gritty", preset: "", style: { formality: "casual" } });
  });

  it("says so for a series the worker keeps nothing of yet", async () => {
    serve({ ...base, "/api/catalog/series/1": { body: seriesPage([episode(1)], { series_id: null }) } });
    show("/series/1/style");
    expect(await screen.findByText(/keeps nothing for this series yet/)).toBeInTheDocument();
  });
});
