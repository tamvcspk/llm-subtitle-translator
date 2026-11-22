# Measuring quality: measure before you change a prompt or a style

Translation quality has one honest measure here: how many cues **you** had to correct. Insights shows it; the golden set
lets you test a change against it before you roll the change out.

## What to watch (Insights)

- **Cues you corrected** (`GET /api/metrics`, `quality.cue_edits`): distinct cues corrected in the window over cues translated in
  it. By model and by prompt version in the same page. This is the number a change must lower.
- **Repairs**, **Retranslated**, **Read too fast**, **Lines too long**: cheaper signs that the machine struggled (a repair
  call is paid for twice; a cue over 20 characters a second or a line over 42 characters is hard to read).
- **Style trials**: the verdicts you gave on variants cue by cue, by style and prompt version.

A rate is a dash while there is nothing to divide by. A corrected cue is blamed on the model and prompt version of the job
that wrote it, so a change shows only on episodes translated after it; compare windows, not the whole history.

## Before you change a prompt, a style or a model

1. **Take the golden set** of the series: Insights → *Your corrections* → **Download golden set** (or
   `GET /api/series/{id}/golden?lang=vi&download=1`). It holds the cues you corrected, with their source line. It is only as
   good as your corrections: a series with a dozen of them tells you little.
2. **Export the bundle of an episode that has corrected cues**: Activity → a finished job → **Export job spec** (or
   `subcore bundle`). That is `before.json`. `after.json` is a copy with the change made in it: another style, another
   glossary, another model. (A new prompt version is compared by running the same bundle with the old and the new release
   of `subcore`.)
3. **Score both on exactly those cues**, with a real key (this is the only step that spends quota; it is two translations):

   ```bash
   uv run subcore compare Show.S01E02.en.srt --bundle before.json --bundle after.json --golden show.vi.golden.json
   ```

   The table has `golden: exact cues` (how many corrected cues the output now writes exactly as you did) and
   `golden: similarity` (0 to 1). Higher is better. `--provider fake` checks the plumbing without a call. The golden set is
   read for the bundle's own season and episode, so run it once per episode that has corrected cues.
4. **Change it for real only if `after` wins**, and look at Insights again after a week of new translations.

A golden entry counts only while its source line is the one you corrected: if the source subtitle changed under the cue
numbers, the entry is left out, and when none is left `compare` stops before any call.

## A second opinion on a variant: the LLM judge

When you try a style on a whole episode (Style → *Compare on a whole episode*) the variant comes back as a candidate with
hundreds of differing cues. The judge is a cheap first look that tells you where to look first. It is **off** until you turn on
*LLM judge* in Settings → Translation, and every use costs model calls.

- On the Compare page of a candidate, **Ask the judge (N cues)** shows its price first (a call per 30 cues, at most 300 cues a
  job) and queues a job. The verdicts come back batch by batch; the page updates by itself.
- Under each cue you read `Judge prefers the variant: tighter wording`, `…the current text…` or `…sees no real difference`,
  and above them a tally. It ticks nothing for you and counts nowhere: the A/B scores in Insights are only the cues **you**
  pick. Use it to skip the cues it calls equal and to read first the ones where it prefers the current text.
- It judges the meaning first, then how natural the sentence is, then how short it is; it is told that length, formality and
  coming first are no reason. Which of the two texts it sees as *A* is fixed per cue and mixed, so it does not favour either
  side by position. It sees the previous source line for context and never a timecode.
- Set **Judge model** to a model stronger than the one that translated; the same model judging its own work is the weakest
  setting. Asking again only pays for the cues without a verdict; **Ask the judge again** re-asks them all (after you changed
  the judge model, say). A verdict disappears when the cue's text, the variant or the rubric changes.

Do not take it for more than it is: nobody has yet measured how often it agrees with you. Compare its tally with what you
pick on a few candidates before you let it decide where you look.

## What the loop learns by itself

After each cue edit the worker looks for a word you keep swapping for another: the same change of one to three words in
three or more different cues that share a word of the source (`Mom` → `Má` every time). It proposes a term in Review with the
cues as evidence; you accept, edit or reject it, and a rejection is remembered. It asks no model. **Look at my edits** on
Insights does it on demand; `suggest_from_edits` in Settings turns the automatic look off.

It cannot suggest a term when the cues share no source word, and it does not see corrections made before P15 (they did not
keep what the machine wrote); the golden set does include those.
