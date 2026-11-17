import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sent, serve, show } from "../test/render";

const defaults: Record<string, unknown> = {
  bazarr_url: "http://bazarr:6767",
  bazarr_api_key: "**********",
  bazarr_api_key_configured: true,
  bazarr_api_key_last4: "9a1f",
  bazarr_api_key_status: "ok",
  tmdb_api_key: "**********",
  tmdb_api_key_configured: true,
  tmdb_api_key_last4: "44b2",
  tmdb_api_key_status: "ok",
  gemini_api_key: "**********",
  gemini_api_key_configured: true,
  gemini_api_key_last4: "88c0",
  gemini_api_key_status: "exhausted",
  gemini_api_key2: "",
  gemini_api_key2_configured: false,
  gemini_api_key2_last4: "",
  gemini_api_key2_status: "",
  openai_compat_api_key: "",
  openai_compat_api_key_configured: false,
  openai_compat_api_key_last4: "",
  openai_compat_api_key_status: "",
  llm_provider: "gemini",
  llm_base_url: "",
  gst_model: "gemini-3.1-flash-lite",
  gst_fallback_model: "gemini-3.5-flash",
  research_model: "",
  gst_temperature: "0.7",
  gst_thinking_budget: "2048",
  gst_no_thinking: false,
  gst_paid_quota: false,
  llm_max_input_tokens: 128000,
  llm_max_output_tokens: 8192,
  source_languages: [{ code: "en", language: "English", enabled: true }],
  target_languages: [{ code: "vi", language: "Vietnamese", enabled: true }],
  media_roots: ["/media"],
  catalog_sync_interval: 300,
  catalog_shrink_guard: 20,
  auto_request: false,
  auto_request_daily_cap: 0,
  auto_request_bootstrap: 2,
  job_settle_seconds: 600,
  breaker_threshold: 3,
  max_crashes: 3,
  max_calls_per_job: 20,
  max_calls_per_day: 0,
  transient_retry_seconds: 30,
  quota_reset_tz: "America/Los_Angeles",
  memory_enabled: false,
  memory_model: "",
  summarize_model: "",
  memory_gap_wait_seconds: 1800,
  memory_budget_tokens: 8000,
  memory_recent_episodes: 3,
  few_shot_budget_tokens: 600,
  glossary_filter_threshold: 50,
  edit_coalesce_minutes: 10,
  suggest_from_facts: true,
  auto_accept_terms: true,
  auto_accept_address_rules: false,
  auto_accept_characters: false,
  log_level: "INFO",
  log_prompt_bodies: "all",
  log_body_max_bytes: 262144,
  log_max_mb: 500,
  log_retention_days: 30,
  events_retention_days: 90,
  llm_cost_in_per_mtok: 0,
  llm_cost_out_per_mtok: 0,
  retention_done_days: 30,
  retention_failed_days: 90,
  retention_versions_keep: 5,
  retention_glossary_keep: 20,
  key_warnings: [],
};

/** A worker that keeps what is saved: a POST changes what the next GET says. */
function worker(extra: Record<string, unknown> = {}, start: Record<string, unknown> = defaults) {
  let current = { ...start };
  return {
    "/api/status": {
      body: { queue: { pending: 0, processing: 0, deferred: 0, done: 0, failed: 0 }, daily_quota_pause: null, providers: [], settings: { key_warnings: [] } },
    },
    "/api/catalog/status": { body: { synced_at: null, attempted_at: null, status: "ok", warning: "", items: 3 } },
    "/api/auth/me": { body: { user: "admin", via: "session", can_change_password: true } },
    "/api/queue": { body: { counts: {}, providers: [] } },
    "/api/settings": (body: Record<string, unknown> | null) => {
      if (body !== null) current = { ...current, ...body };
      return { body: current };
    },
    "/api/gemini-models": { body: { items: [{ id: "gemini-3.1-flash-lite", name: "a" }, { id: "gemini-3.5-flash", name: "b" }, { id: "gemini-3.5-pro", name: "c" }] } },
    "/api/languages": {
      body: {
        items: [
          { code: "en", code3: "eng", name: "English", language: "English", enabled_in_bazarr: true },
          { code: "vi", code3: "vie", name: "Vietnamese", language: "Vietnamese", enabled_in_bazarr: true },
          { code: "ja", code3: "jpn", name: "Japanese", language: "Japanese", enabled_in_bazarr: true },
        ],
      },
    },
    ...extra,
  };
}

const posts = () => sent("/api/settings").filter((body) => body !== null) as Array<Record<string, unknown>>;

async function open(name: string) {
  // The control also says "Unsaved changes" once its group has some: match on how it starts.
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(`^${name.replace(".", "\\.")}`) }));
}

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("Settings", () => {
  it("has seven groups, the first two open", async () => {
    serve(worker());
    show("/settings");
    for (const name of ["1. Connections", "2. Translation", "3. Library", "4. Queue and quota", "5. Story and style", "6. Maintenance", "7. Security"]) {
      expect(await screen.findByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "1. Connections" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "2. Translation" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "3. Library" })).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findByRole("textbox", { name: "Bazarr URL" })).toHaveValue("http://bazarr:6767");
    expect(screen.getByRole("textbox", { name: "Temperature" })).toHaveValue("0.7");
  });

  it("saves a group by itself, with only its own keys, and says it was saved", async () => {
    serve(worker());
    show("/settings");
    const budget = await screen.findByRole("textbox", { name: "Max output tokens" });
    await userEvent.clear(budget);
    await userEvent.type(budget, "16000");
    const group = screen.getByRole("button", { name: /^2\. Translation/ });
    expect(within(group).getByText("Unsaved changes")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save 2. Translation" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    const body = posts()[0]!;
    expect(body.llm_max_output_tokens).toBe(16000);
    expect(body.gst_temperature).toBe("0.7"); // the two kept as text stay text
    expect(Object.keys(body).sort()).toEqual(
      [
        "gst_model",
        "gst_fallback_model",
        "research_model",
        "gst_temperature",
        "gst_thinking_budget",
        "gst_no_thinking",
        "gst_paid_quota",
        "llm_max_input_tokens",
        "llm_max_output_tokens",
        "source_languages",
        "target_languages",
      ].sort(),
    );
    expect(await screen.findByText("✓ Saved")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /^2\. Translation/ })).queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("refuses a number the worker would clamp and says why", async () => {
    serve(worker());
    show("/settings");
    await open("4. Queue and quota");
    const breaker = await screen.findByRole("textbox", { name: "Breaker threshold" });
    await userEvent.clear(breaker);
    await userEvent.type(breaker, "0");
    expect(screen.getByText("At least 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save 4. Queue and quota" })).toBeDisabled();
    await userEvent.clear(breaker);
    expect(screen.getByText("Enter a number")).toBeInTheDocument();
    await userEvent.type(breaker, "5");
    expect(screen.getByRole("button", { name: "Save 4. Queue and quota" })).toBeEnabled();
  });

  it("saves a switch as a true or false and shows what the worker answered", async () => {
    serve(worker());
    show("/settings");
    await open("3. Library");
    await userEvent.click(await screen.findByRole("switch", { name: /^Auto-request/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save 3. Library" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toMatchObject({ auto_request: true, catalog_sync_interval: 300, catalog_shrink_guard: 20, media_roots: ["/media"] });
    expect(await screen.findByRole("switch", { name: /^Auto-request/ })).toBeChecked();
  });

  it("shows the reason when a save is refused", async () => {
    serve(worker({ "/api/settings": (body: Record<string, unknown> | null) => (body === null ? { body: defaults } : { status: 400, body: { error: "settings must be a JSON object" } }) }));
    show("/settings");
    await open("5. Story and style");
    await userEvent.click(await screen.findByRole("switch", { name: "Story memory" }));
    await userEvent.click(screen.getByRole("button", { name: "Save 5. Story and style" }));
    expect(await screen.findByText("Save failed: settings must be a JSON object")).toBeInTheDocument();
  });

  it("puts a group back with Discard", async () => {
    serve(worker());
    show("/settings");
    await open("5. Story and style");
    await userEvent.click(await screen.findByRole("switch", { name: "Story memory" }));
    expect(screen.getByRole("switch", { name: "Story memory" })).toBeChecked();
    await userEvent.click(screen.getAllByRole("button", { name: "Discard" }).find((button) => !button.hasAttribute("disabled"))!);
    expect(screen.getByRole("switch", { name: "Story memory" })).not.toBeChecked();
    expect(posts()).toEqual([]);
  });

  it("saves the log settings as the enums they are", async () => {
    serve(worker());
    show("/settings");
    await open("6. Maintenance");
    const bodies = await screen.findByRole("radiogroup", { name: "Keep prompt bodies" });
    await userEvent.click(within(bodies).getByRole("radio", { name: "Errors only" }));
    await userEvent.click(screen.getByRole("button", { name: "Save 6. Maintenance" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toMatchObject({ log_prompt_bodies: "errors", log_level: "INFO", llm_cost_in_per_mtok: 0, retention_glossary_keep: 20 });
  });

  it("keeps what was changed, when the page is loaded again after signing in, and forgets it once saved", async () => {
    serve(worker());
    const first = show("/settings");
    await open("5. Story and style");
    await userEvent.click(await screen.findByRole("switch", { name: "Story memory" }));
    expect(sessionStorage.getItem("gst-draft:settings:story")).toContain('"memory_enabled":true');

    first.unmount();
    cleanup();
    serve(worker());
    show("/settings");
    await open("5. Story and style");
    expect(await screen.findByRole("switch", { name: "Story memory" })).toBeChecked();
    expect(screen.getByText(/kept while you signed in again/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Save 5. Story and style" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    await waitFor(() => expect(sessionStorage.getItem("gst-draft:settings:story")).toBeNull());
  });

  it("does not keep a draft that was changed back by hand", async () => {
    serve(worker());
    show("/settings");
    await open("5. Story and style");
    const memory = await screen.findByRole("switch", { name: "Story memory" });
    await userEvent.click(memory);
    await userEvent.click(memory);
    expect(sessionStorage.getItem("gst-draft:settings:story")).toBeNull();
  });
});

describe("Settings: Connections", () => {
  it("says what each stored key may say about itself, and never its value", async () => {
    serve(worker());
    show("/settings");
    expect(await screen.findByText("••••9a1f · ok")).toBeInTheDocument();
    expect(screen.getByText("••••88c0 · exhausted")).toBeInTheDocument();
    expect(screen.getAllByText("(not set)")).toHaveLength(2);
    expect(screen.queryByText("**********")).not.toBeInTheDocument();
  });

  it("sends only the key that was typed, with the group, and forgets it after", async () => {
    serve(worker());
    show("/settings");
    const box = await screen.findByLabelText("New key for Gemini 2");
    await userEvent.type(box, "AIza-new-key");
    expect(within(screen.getByRole("button", { name: /^1\. Connections/ })).getByText("Unsaved changes")).toBeInTheDocument();
    // A typed key is never kept as a draft.
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("AIza-new-key");
    await userEvent.click(screen.getByRole("button", { name: "Save 1. Connections" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual({ bazarr_url: "http://bazarr:6767", llm_provider: "gemini", llm_base_url: "", gemini_api_key2: "AIza-new-key" });
    await waitFor(() => expect(screen.getByLabelText("New key for Gemini 2")).toHaveValue(""));
  });

  it("tests the stored key when the box is blank, and says whether it was accepted", async () => {
    serve(
      worker({
        "/api/test-connection": (body: Record<string, unknown> | null) =>
          body?.kind === "tmdb_api_key"
            ? { body: { ok: true, kind: "tmdb_api_key", message: "TMDB API key is valid" } }
            : { body: { ok: false, kind: String(body?.kind), message: "API key not valid", auth_failed: true } },
      }),
    );
    show("/settings");
    await userEvent.click(await screen.findByRole("button", { name: "Test TMDB" }));
    expect(await screen.findByText("✓ TMDB API key is valid")).toBeInTheDocument();
    expect(sent("/api/test-connection")[0]).toEqual({ kind: "tmdb_api_key", bazarr_url: "http://bazarr:6767" });

    await userEvent.type(screen.getByLabelText("New key for Gemini 1"), "typed");
    await userEvent.click(screen.getByRole("button", { name: "Test Gemini 1" }));
    expect(await screen.findByText("✗ Rejected: API key not valid")).toBeInTheDocument();
    expect(sent("/api/test-connection")[1]).toEqual({ kind: "gemini_api_key", bazarr_url: "http://bazarr:6767", gemini_api_key: "typed" });
    // Testing does not throw away what was typed.
    expect(screen.getByLabelText("New key for Gemini 1")).toHaveValue("typed");
  });

  it("asks for the server's address when the provider is OpenAI-compatible", async () => {
    serve(worker());
    show("/settings");
    expect(screen.queryByRole("textbox", { name: "Base URL" })).not.toBeInTheDocument();
    const provider = await screen.findByRole("radiogroup", { name: "LLM provider" });
    await userEvent.click(within(provider).getByRole("radio", { name: "OpenAI-compatible" }));
    const address = await screen.findByRole("textbox", { name: "Base URL" });
    expect(screen.getByRole("button", { name: "Save 1. Connections" })).toBeDisabled();
    expect(screen.getByText("Give the address of the OpenAI-compatible server.")).toBeInTheDocument();
    await userEvent.type(address, "http://ollama:11434/v1");
    await userEvent.click(screen.getByRole("button", { name: "Save 1. Connections" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toMatchObject({ llm_provider: "openai_compat", llm_base_url: "http://ollama:11434/v1" });
  });
});

describe("Settings: Translation lists", () => {
  it("adds a language Bazarr knows, and sends the list with its code and name", async () => {
    serve(worker());
    show("/settings");
    const add = await screen.findByRole("combobox", { name: "Add to Source languages" });
    await waitFor(() => expect(add).toBeEnabled());
    await userEvent.click(add);
    // Both lists offer Japanese: take it from the list that was opened.
    const opened = document.getElementById(add.getAttribute("aria-controls") ?? "") as HTMLElement;
    await userEvent.click(await within(opened).findByRole("option", { name: "Japanese (ja)", hidden: true }));
    expect(screen.getByRole("checkbox", { name: "Japanese (ja)" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Save 2. Translation" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]?.source_languages).toEqual([
      { code: "en", language: "English", enabled: true },
      { code: "ja", language: "Japanese", enabled: true },
    ]);
  });

  it("keeps at least one language of each list ticked", async () => {
    serve(worker());
    show("/settings");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Vietnamese (vi)" }));
    expect(screen.getByText("Keep at least one target language ticked.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save 2. Translation" })).toBeDisabled();
  });

  it("says when Bazarr's languages cannot be listed", async () => {
    serve(worker({ "/api/languages": { status: 502, body: { error: "Bazarr is not reachable" } } }));
    show("/settings");
    expect((await screen.findAllByText(/list of languages is not available now/)).length).toBe(2);
  });
});

describe("Settings: Library, maintenance, security", () => {
  it("adds and removes media folders, and always keeps one", async () => {
    serve(worker());
    show("/settings");
    await open("3. Library");
    const first = await screen.findByRole("textbox", { name: "Media root 1" });
    expect(first).toHaveValue("/media");
    expect(screen.getByRole("button", { name: "Remove media root 1" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Add path" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Media root 2" }), "/movies");
    await userEvent.click(screen.getByRole("button", { name: "Save 3. Library" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]?.media_roots).toEqual(["/media", "/movies"]);
  });

  it("leaves a blank folder out of what it saves", async () => {
    serve(worker());
    show("/settings");
    await open("3. Library");
    await userEvent.click(await screen.findByRole("button", { name: "Add path" }));
    await userEvent.click(screen.getByRole("switch", { name: /^Auto-request/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save 3. Library" }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]?.media_roots).toEqual(["/media"]);
    // The worker normalised the list (the blank row is gone), so the group is clean again and nothing is kept for a sign-in.
    expect(await screen.findByText("✓ Saved")).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: /^3\. Library/ })).queryByText("Unsaved changes")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Media root 2" })).not.toBeInTheDocument();
    expect(sessionStorage.getItem("gst-draft:settings:library")).toBeNull();
  });

  it("previews the cleanup, and deletes only after the confirmation", async () => {
    serve(
      worker({
        "/api/cleanup": (body: Record<string, unknown> | null) => ({
          body: {
            dry_run: body?.dry_run !== false,
            at: 1,
            groups: [
              { name: "jobs_done", enabled: true, window: 30, files: 0, rows: 412, bytes: 1832040, error: "" },
              { name: "versions", enabled: true, window: 5, files: 87, rows: 0, bytes: 5402112, error: "" },
              { name: "temp", enabled: true, window: null, files: 0, rows: 0, bytes: 0, error: "" },
            ],
            total: { files: 87, rows: 412, bytes: 7234152 },
            media_temp: [],
          },
        }),
      }),
    );
    show("/settings");
    await open("6. Maintenance");
    await userEvent.click(await screen.findByRole("button", { name: "Clean up now" }));
    const sheet = await screen.findByRole("dialog", { name: "Clean up now" });
    expect(await within(sheet).findByText(/Preview: removes 87 files and 412 database rows, and frees 6\.9 MB/)).toBeInTheDocument();
    expect(within(sheet).getByText("Finished jobs: 412 (1.7 MB)")).toBeInTheDocument();
    expect(within(sheet).getByText("Media folders are never touched.")).toBeInTheDocument();
    expect(sent("/api/cleanup")).toEqual([{ dry_run: true }]);
    await userEvent.click(within(sheet).getByRole("button", { name: "Confirm" }));
    expect(await within(sheet).findByText(/Removed 87 files and 412 database rows, freeing 6\.9 MB/)).toBeInTheDocument();
    expect(sent("/api/cleanup")[1]).toEqual({ dry_run: false });
  });

  it("links to Backups and Logs", async () => {
    serve(worker());
    show("/settings");
    await open("6. Maintenance");
    expect(await screen.findByRole("link", { name: "Backups →" })).toHaveAttribute("href", "/backups");
    expect(screen.getByRole("link", { name: "Logs →" })).toHaveAttribute("href", "/logs");
  });

  it("changes the password, and asks for eight characters", async () => {
    serve(worker({ "/api/auth/password": { body: { ok: true } } }));
    show("/settings");
    await open("7. Security");
    await userEvent.type(await screen.findByLabelText("Current password"), "old-secret");
    const next = screen.getByLabelText("New password (8+)");
    await userEvent.type(next, "short");
    expect(screen.getByText("Use at least 8 characters")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update password" })).toBeDisabled();
    await userEvent.type(next, "-and-longer");
    await userEvent.click(screen.getByRole("button", { name: "Update password" }));
    await waitFor(() => expect(sent("/api/auth/password")).toEqual([{ current: "old-secret", new: "short-and-longer" }]));
    expect(await screen.findByText(/Password updated/)).toBeInTheDocument();
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it("shows why a password change is refused", async () => {
    serve(worker({ "/api/auth/password": { status: 403, body: { error: "The current password is wrong" } } }));
    show("/settings");
    await open("7. Security");
    await userEvent.type(await screen.findByLabelText("Current password"), "nope");
    await userEvent.type(screen.getByLabelText("New password (8+)"), "long-enough");
    await userEvent.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByText("The current password is wrong")).toBeInTheDocument();
  });

  it("has no password to change when sign-in is off", async () => {
    serve(worker({ "/api/auth/me": { body: { user: null, via: "off", can_change_password: false } } }));
    show("/settings");
    await open("7. Security");
    expect(await screen.findByText(/sign-in switched off/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update password" })).not.toBeInTheDocument();
  });
});
