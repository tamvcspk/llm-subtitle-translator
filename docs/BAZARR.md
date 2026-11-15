# Bazarr Integration

## Goal

Bazarr keeps doing what it is good at: tracking wanted subtitles, extracting embedded source
subtitles and indexing local subtitles. The worker adds the translation step. Since P10 the
worker **pulls**: it reads the library from Bazarr's API and keeps it as a cache, and Bazarr
needs no configuration on its side (no post-processing command, no shared script folder).

## What the worker needs

- Bazarr's address and API key (Settings, or `BAZARR_URL` / `BAZARR_API_KEY` for the first run).
- The same media paths as Bazarr (`/media/...` in both containers), because the worker writes
  the translated `.srt` beside the media and Bazarr must find it.
- Enable Bazarr's Embedded Subtitles provider if you want it to extract `.srt` files from video
  containers for the source languages configured in the worker console.

## How the worker sees the library

Every `catalog_sync_interval` seconds (default 300) and when you press **Sync now** in Library,
the worker reads series (paged), each series' episodes, and movies (paged), with the subtitles
each has and the languages Bazarr still lists as missing. That is the **catalog**.

- A read never creates a job.
- A read that fails, times out, answers empty, or lists more than `catalog_shrink_guard`
  percent fewer items than the last accepted read is set aside. The catalog stays as it was,
  Library says when it was last read, and shows the warning. Nothing becomes "not missing"
  because Bazarr was down.
- An item is identified by Bazarr's own id, not its path. If Sonarr renames the files, a job
  that was already waiting follows them (the next read brings the new paths).

## Translating

Nothing is translated until you ask, or until the policy asks:

- **Library → Request translation** for an item, a selection, a season or a series. A request
  for more than one job shows an estimate first (jobs, cues, calls, what is left of the day's
  `max_calls_per_day`, the time at the recent pace) and asks you to confirm.
- **Auto-request** (Settings → Library and auto-request, `auto_request`; per series on its
  Library page: follow the global setting, on, off). When on, after each read every item
  Bazarr lists as missing a target language, and that has no job, is not Ignored and was not
  removed by you, gets a job with origin `policy`. `auto_request_daily_cap` limits how many
  per day (UTC); what is over stays Missing. It is **off by default**: turn it on for one
  series first. `GST_ONLY_PATHS`, if set, still confines it to those folders.
- **Ignore** an item and a language to say "do not translate this": neither the policy nor a
  read creates a job for it. **Stop ignoring** takes it back.

A job only translates a language that is still missing: if the target subtitle appeared in the
meantime (Bazarr downloaded one), the job ends without a model call, and an existing subtitle is
never overwritten by an automatic job.

## Upgrading from the post-processing script (before P10)

1. Update and start the worker. At its first start it settles the old queue once: files left in
   `pending/`, `deferred/` become jobs, `done/` and `failed/` become history, **held** jobs are
   dropped (the item is simply Missing again), **rejected** jobs become Ignored. The old folders
   move to `queue.migrated/`; nothing is deleted. Running it again changes nothing.
2. In Bazarr: Settings → Subtitles → **Use Custom Post-Processing**: switch it off and clear the
   command (`/config/postprocess/gst_enqueue.sh ...`).
3. In Bazarr's compose, drop the `.../postprocess:/config/postprocess` mount. The worker no
   longer mounts `/bazarr-postprocess`; its `/queue` mount is only for step 1 and can go once
   the folders have moved to `queue.migrated/`.
4. Open Library, press **Sync now**, and look at what Bazarr lists as missing. If you relied on
   the script translating everything automatically, turn **Auto-request** on, for one series
   first (a whole library at once spends a lot of quota). The old `admission_mode` setting and
   `GST_MAX_PENDING` no longer exist; the old Queue → Held tab is replaced by Missing in Library.

If Bazarr's post-processing command is left in place it only errors (the script file is gone) and
changes nothing in the worker.

## Refresh behavior

After translation, the worker asks Bazarr to rescan the item:

```text
PATCH /api/series?seriesid=<series_id>&action=scan-disk
PATCH /api/movies?radarrid=<radarr_id>&action=scan-disk
```

If an item id is missing, it falls back to Bazarr's full subtitle scan task for series or
movies. A slow or failing Bazarr never changes the result of a finished job.

## Daily quota and cancellation

Queued and waiting jobs have a Cancel button in the queue view. Cancellation removes the request
without deleting subtitle files. When the provider reports that the daily quota is spent, jobs
wait (they are not failed) until the reset and then run; requests made meanwhile are accepted
and wait too.
