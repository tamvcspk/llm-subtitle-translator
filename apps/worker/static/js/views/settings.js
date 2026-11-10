// Settings form. Inputs are named after the POST /api/settings fields, so the
// form is read and filled generically; languages are kept in `selected`.
import { get, post } from "../api.js";
import { languageLabel, loadLanguages, loadModels, refreshStatus, store } from "../store.js";
import { h, icon, runAction, toast } from "../ui.js";

const SECRET_MASK = "**********";
const DEFAULT_MODEL = "gemini-flash-latest";

const selected = { source: [], target: [] };
let form;
let dirty = false;
let loaded = false;

function setDirty(value) {
  dirty = value;
  document.getElementById("settings-savebar").hidden = !dirty;
}

function readForm() {
  const data = {};
  for (const input of form.elements) {
    if (!input.name) continue;
    if (input.type === "checkbox") data[input.name] = input.checked;
    else if (input.dataset.type === "int") data[input.name] = input.value.trim() === "" ? Number(input.dataset.default) : Number(input.value);
    else if (input.dataset.type === "lines") data[input.name] = input.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    else data[input.name] = input.value.trim();
  }
  data.source_languages = selected.source;
  data.target_languages = selected.target;
  return data;
}

function fillForm(settings) {
  renderModelOptions(settings.gst_model, settings.gst_fallback_model);
  for (const input of form.elements) {
    if (!input.name || !(input.name in settings)) continue;
    const value = settings[input.name];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else if (input.dataset.type === "lines") input.value = (value || []).join("\n");
    else input.value = value ?? "";
  }
  form.querySelectorAll("[data-secret-status]").forEach((note) => {
    const configured = settings[`${note.dataset.secretStatus}_configured`];
    note.className = `hint ${configured ? "hint-ok" : ""}`;
    note.textContent = configured ? "Configured. Type a new key to replace it." : "Not configured.";
  });
  for (const kind of ["source", "target"]) {
    selected[kind] = (settings[`${kind}_languages`] || []).map((item) => ({
      code: item.code,
      language: item.language,
      enabled: item.enabled !== false,
    }));
    renderLanguages(kind);
  }
  setDirty(false);
}

function renderModelOptions(current, currentFallback) {
  const select = form.elements.gst_model;
  const value = current || select.value || DEFAULT_MODEL;
  const models = [...(store.models || [])];
  if (!models.some((model) => model.id === value)) models.unshift({ id: value, name: value });
  select.replaceChildren(...models.map((model) => h("option", { value: model.id, text: model.name })));
  select.value = value;

  // An empty fallback disables it; keep a saved fallback even if it is no longer listed.
  const fallback = form.elements.gst_fallback_model;
  const fallbackValue = currentFallback ?? fallback.value;
  const fallbackModels = [...(store.models || [])];
  if (fallbackValue && !fallbackModels.some((model) => model.id === fallbackValue)) {
    fallbackModels.unshift({ id: fallbackValue, name: fallbackValue });
  }
  fallback.replaceChildren(
    h("option", { value: "", text: "None" }),
    ...fallbackModels.map((model) => h("option", { value: model.id, text: model.name })),
  );
  fallback.value = fallbackValue || "";
}

function renderLanguages(kind) {
  const chips = form.querySelector(`[data-language-list="${kind}"]`);
  const items = selected[kind];
  chips.replaceChildren(...(items.length
    ? items.map((item) => h("span", { class: "chip chip-removable" },
      languageLabel(item),
      h("button", {
        type: "button",
        class: "chip-remove",
        "aria-label": `Remove ${item.code}`,
        onClick: () => {
          selected[kind] = selected[kind].filter((candidate) => candidate.code !== item.code);
          renderLanguages(kind);
          setDirty(true);
        },
      }, icon("x"))))
    : [h("span", { class: "muted", text: "No languages selected" })]));

  const select = form.querySelector(`[data-language-kind="${kind}"]`);
  const taken = new Set(items.map((item) => item.code));
  const options = (store.languages || []).filter((language) => !taken.has(language.code));
  const option = (language) => h("option", { value: language.code, text: `${language.code} · ${language.name}${language.enabled_in_bazarr ? " •" : ""}` });
  const inBazarr = options.filter((language) => language.enabled_in_bazarr);
  const others = options.filter((language) => !language.enabled_in_bazarr);
  select.replaceChildren(...(inBazarr.length && others.length
    ? [h("optgroup", { label: "Enabled in Bazarr" }, inBazarr.map(option)), h("optgroup", { label: "All languages" }, others.map(option))]
    : options.map(option)));
  select.disabled = !options.length;
  if (!options.length) select.replaceChildren(h("option", { text: store.languages ? "No languages available" : "Loading languages…" }));
  form.querySelector(`[data-add-language="${kind}"]`).disabled = !options.length;
}

function addLanguage(kind) {
  const code = form.querySelector(`[data-language-kind="${kind}"]`).value;
  const language = (store.languages || []).find((item) => item.code === code);
  if (!language || selected[kind].some((item) => item.code === code)) return;
  selected[kind] = [...selected[kind], { code: language.code, language: language.language, enabled: true }];
  renderLanguages(kind);
  setDirty(true);
}

async function loadSettings() {
  fillForm(await get("/api/settings"));
  loaded = true;
}

async function saveSettings() {
  const saved = await post("/api/settings", readForm());
  fillForm(saved);
  toast("Settings saved", "success");
  refreshStatus().catch(() => {});
}

async function testConnection(button) {
  const kind = button.dataset.testKind;
  const input = button.closest(".field").querySelector("input");
  const note = form.querySelector(`[data-secret-status="${input.name}"]`);
  note.className = "hint";
  note.textContent = "Testing…";
  try {
    const data = await post("/api/test-connection", { ...readForm(), kind });
    note.className = `hint ${data.ok ? "hint-ok" : "hint-warn"}`;
    note.textContent = data.message || (data.ok ? "Connection OK" : "Connection failed");
  } catch (error) {
    note.className = "hint hint-warn";
    note.textContent = error.message;
  }
}

export default {
  title: "Settings",
  subtitle: "Bazarr connection, translator options, languages and library scan.",

  init() {
    form = document.getElementById("settings-form");
    // Only named inputs are saved; picking a language to add is not an edit yet.
    const markDirty = (event) => { if (event.target.name) setDirty(true); };
    form.addEventListener("input", markDirty);
    form.addEventListener("change", markDirty);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runAction(document.getElementById("settings-save"), saveSettings);
    });
    form.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        form.requestSubmit();
      }
    });
    document.getElementById("settings-discard").addEventListener("click", (event) => {
      runAction(event.currentTarget, loadSettings);
    });
    form.querySelectorAll("[data-test-kind]").forEach((button) => {
      button.addEventListener("click", () => runAction(button, testConnection));
    });
    form.querySelectorAll("[data-add-language]").forEach((button) => {
      button.addEventListener("click", () => addLanguage(button.dataset.addLanguage));
    });
    // A masked key is replaced, not edited: select it so typing overwrites it.
    form.querySelectorAll("[data-secret]").forEach((input) => {
      input.addEventListener("focus", () => { if (input.value === SECRET_MASK) input.select(); });
    });
    window.addEventListener("beforeunload", (event) => {
      if (dirty) event.preventDefault();
    });
  },

  async enter() {
    const extras = [
      loadLanguages().then(() => { renderLanguages("source"); renderLanguages("target"); }),
      loadModels().then(() => renderModelOptions()),
    ].map((promise) => promise.catch((error) => toast(`Could not load choices: ${error.message}`, "error")));
    // Keep unsaved edits when coming back to this view.
    if (!loaded || !dirty) await loadSettings();
    await Promise.all(extras);
  },
};
