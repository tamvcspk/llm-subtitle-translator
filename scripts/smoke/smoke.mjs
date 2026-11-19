// Walk the console in a real browser and fail on anything broken: a page that does not render, a console error, a
// failed /api call. It is the "can I click it" check after a change; it does not replace the unit tests.
//
//   node scripts/smoke/smoke.mjs [--url http://127.0.0.1:6790] [--out DIR] [--routes /library,/series/1/glossary]
//                                [--steps steps.mjs] [--headed]
// In Git Bash put MSYS_NO_PATHCONV=1 before the command, or a leading "/" in --routes is turned into a Windows path.
//
// `--steps` names a module whose default export is `async ({ page, base, shot, expect }) => {}`: the clicks that belong
// to one change (open the History sheet, press Restore, ...). playwright-core is not a dependency of the project: the
// first run installs it into local-state/tools (git-ignored) and drives the Microsoft Edge that Windows already has.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const tools = join(root, "local-state", "tools");
const PLAYWRIGHT = "playwright-core@1.54.2";

function arg(name, fallback) {
  const at = process.argv.indexOf(`--${name}`);
  return at === -1 ? fallback : process.argv[at + 1];
}
const flag = (name) => process.argv.includes(`--${name}`);

function load() {
  const need = join(tools, "node_modules", "playwright-core");
  if (!existsSync(need)) {
    mkdirSync(tools, { recursive: true });
    console.log(`installing ${PLAYWRIGHT} into ${tools} ...`);
    execFileSync("npm", ["install", "--prefix", tools, "--save-exact", "--ignore-scripts", "--no-audit", "--no-fund", PLAYWRIGHT], {
      stdio: "inherit",
      shell: true,
    });
  }
  return createRequire(join(tools, "package.json"))("playwright-core");
}

const base = arg("url", "http://127.0.0.1:6790").replace(/\/$/, "");
const out = resolve(arg("out", join(root, "local-state", "smoke")));
mkdirSync(out, { recursive: true });
// What a first look covers when no routes are named: every screen that opens without an id from a list.
const routes = (arg("routes", "/library,/series/1/episodes,/series/1/style,/series/1/glossary,/series/1/characters,/series/1/context,/review,/activity,/settings,/backups,/logs")).split(",");

const { chromium } = load();
const browser = await chromium.launch({ channel: "msedge", headless: !flag("headed") });
const page = await (await browser.newContext({ viewport: { width: 820, height: 1100 } })).newPage(); // an iPad held upright
const problems = [];
let current = "";
page.on("console", (m) => m.type() === "error" && problems.push(`${current}: console error: ${m.text()}`));
page.on("pageerror", (e) => problems.push(`${current}: uncaught: ${e.message}`));
page.on("response", (r) => r.url().includes("/api/") && r.status() >= 400 && problems.push(`${current}: ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`));

const shot = async (name) => {
  const file = join(out, `${name.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "root"}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
};
const expect = (ok, what) => ok || problems.push(`${current}: expected ${what}`);

for (const route of routes) {
  current = route;
  await page.goto(`${base}/#${route}`, { waitUntil: "networkidle" });
  const text = (await page.locator("body").innerText()).trim();
  expect(text.length > 40, "the page to have content");
  expect(!/something went wrong|unexpected application error/i.test(text), "no error screen");
  await shot(route);
  console.log(`ok  ${route}`);
}

const steps = arg("steps");
if (steps) {
  current = `steps ${steps}`;
  const module = await import(pathToFileURL(resolve(steps)).href);
  await module.default({ page, base, shot, expect });
}

await browser.close();
if (problems.length) {
  console.error(`\n${problems.length} problem(s):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`\nno problem found; screenshots in ${out}`);
