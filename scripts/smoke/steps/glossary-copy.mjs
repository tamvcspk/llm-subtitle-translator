// W1 (R12-6): "Copy from another series" counts what the copy flags stale (a dry run) before Copy can be pressed.
// Read-only: it opens the sheet, picks the other series, reads the sentence and cancels. Needs the seeded "Second Demo".
export default async function ({ page, base, shot, expect }) {
  await page.goto(`${base}/#/series/1/glossary`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Copy from another series" }).click();
  const sheet = page.getByRole("dialog");
  const copy = sheet.getByRole("button", { name: "Copy" });
  expect(await copy.isDisabled(), "Copy to be disabled until a series is chosen");
  await sheet.getByRole("combobox", { name: "From" }).click();
  await page.getByRole("option", { name: "Second Demo" }).click();
  const sentence = sheet.getByRole("status");
  await sentence.waitFor();
  await page.waitForFunction(() => !/Checking/.test(document.querySelector("[role=dialog] [role=status]")?.textContent ?? ""));
  const said = (await sentence.innerText()).trim();
  expect(/^Copying flags \d+ episodes? stale\.$/.test(said), `a live count under Copy, got "${said}"`);
  expect(await copy.isEnabled(), "Copy to be enabled once the count is known");
  await shot("glossary-copy-sheet");
  console.log(`ok  copy says: ${said}`);
  await sheet.getByRole("button", { name: "Cancel" }).click();
}
