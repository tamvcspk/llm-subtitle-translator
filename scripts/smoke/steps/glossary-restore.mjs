// W1 (R12-6): Restore and Copy in the Glossary tab say what they flag stale, from a dry run, before the button works.
// Read-only on purpose: it opens the sheets and checks the sentence, then cancels; nothing is restored or copied.
export default async function ({ page, base, shot, expect }) {
  await page.goto(`${base}/#/series/1/glossary`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "History" }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("button", { name: /^Restore v\d+/ }).first().click();
  const sentence = sheet.getByRole("status");
  await sentence.waitFor();
  await page.waitForFunction(() => !/Checking/.test(document.querySelector("[role=dialog] [role=status]")?.textContent ?? ""));
  const said = (await sentence.innerText()).trim();
  expect(/^Restoring flags \d+ episodes? stale\.$|^No translation is affected\.$/.test(said), `a live count under Restore, got "${said}"`);
  await shot("glossary-restore-sheet");
  console.log(`ok  restore says: ${said}`);
  await sheet.getByRole("button", { name: "Cancel" }).click();
  await sheet.getByRole("button", { name: "Close" }).click();
}
