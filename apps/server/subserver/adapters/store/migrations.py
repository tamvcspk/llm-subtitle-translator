"""The schema of the application's own SQLite, one script per version (``PRAGMA user_version``).

Append a script, never edit one that shipped. The comments say which phase and requirement each came from.
"""

from __future__ import annotations

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE translations (
        id INTEGER PRIMARY KEY,
        output_path TEXT NOT NULL,
        source_path TEXT NOT NULL DEFAULT '',
        source_sha256 TEXT NOT NULL DEFAULT '',
        target_lang TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL,
        stored_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        context_hash TEXT NOT NULL DEFAULT '',
        trigger TEXT NOT NULL,            -- auto | retranslate | revert | external
        parent_version INTEGER,
        status TEXT NOT NULL,             -- current | superseded
        model TEXT NOT NULL DEFAULT '',
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE (output_path, version)
    );
    CREATE INDEX translations_cache ON translations (source_sha256, target_lang, context_hash);
    CREATE TABLE llm_calls (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        task TEXT NOT NULL,
        tokens_in INTEGER NOT NULL,
        tokens_out INTEGER NOT NULL,
        latency_ms INTEGER NOT NULL,
        outcome TEXT NOT NULL,
        at REAL NOT NULL
    );
    CREATE INDEX llm_calls_at ON llm_calls (at);
    """,
    # P3: library (series, units), corrections, and the link from a version to its unit.
    """
    CREATE TABLE series (
        id INTEGER PRIMARY KEY,
        key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL DEFAULT '',
        bazarr_id TEXT NOT NULL DEFAULT '',
        stale_policy TEXT NOT NULL DEFAULT 'flag',   -- flag | ignore
        created_at REAL NOT NULL
    );
    CREATE TABLE units (
        id INTEGER PRIMARY KEY,
        series_id INTEGER REFERENCES series(id),
        movie_key TEXT,
        season INTEGER,
        episode INTEGER,
        title TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT '',
        bazarr_id TEXT NOT NULL DEFAULT '',
        video_path TEXT NOT NULL DEFAULT '',
        source_path TEXT NOT NULL DEFAULT '',
        source_code TEXT NOT NULL DEFAULT '',
        source_language TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX units_episode ON units (series_id, season, episode) WHERE series_id IS NOT NULL;
    CREATE UNIQUE INDEX units_movie ON units (movie_key) WHERE movie_key IS NOT NULL;
    CREATE TABLE unit_outputs (
        unit_id INTEGER NOT NULL REFERENCES units(id),
        target_lang TEXT NOT NULL,
        target_language TEXT NOT NULL DEFAULT '',
        output_path TEXT NOT NULL,
        PRIMARY KEY (unit_id, target_lang)
    );
    CREATE TABLE corrections (
        id INTEGER PRIMARY KEY,
        unit_id INTEGER NOT NULL REFERENCES units(id),
        target_lang TEXT NOT NULL,
        kind TEXT NOT NULL,                -- term | address | note | cue_edit (scope: episode only)
        payload_json TEXT NOT NULL,
        created_at REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX corrections_unit ON corrections (unit_id, target_lang);
    ALTER TABLE translations ADD COLUMN unit_id INTEGER;
    ALTER TABLE translations ADD COLUMN glossary_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE translations ADD COLUMN context_complete INTEGER NOT NULL DEFAULT 1;
    CREATE INDEX translations_unit ON translations (unit_id);
    """,
    # P4: story memory. Facts are immutable per (unit, source sha256, extraction prompt version).
    # series.memory is '' (follow the global setting), 'on' or 'off'; specials_after anchors season 0.
    # translations.memory_hash is the fingerprint of the facts a version was made with ('' = none used);
    # missing_units_json lists the earlier units that had no usable facts at the time (context_complete = 0).
    """
    ALTER TABLE series ADD COLUMN memory TEXT NOT NULL DEFAULT '';
    ALTER TABLE series ADD COLUMN specials_after TEXT NOT NULL DEFAULT '';
    CREATE TABLE facts (
        id INTEGER PRIMARY KEY,
        unit_id INTEGER NOT NULL REFERENCES units(id),
        source_sha256 TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        json TEXT NOT NULL,
        facts_hash TEXT NOT NULL,
        tokens_in INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE (unit_id, source_sha256, prompt_version)
    );
    CREATE TABLE summaries_cache (
        id INTEGER PRIMARY KEY,
        series_id INTEGER NOT NULL REFERENCES series(id),
        up_to_unit INTEGER NOT NULL,
        input_hash TEXT NOT NULL UNIQUE,
        text TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE context_snapshots (
        memory_hash TEXT PRIMARY KEY,
        sections_json TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    ALTER TABLE translations ADD COLUMN memory_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE translations ADD COLUMN missing_units_json TEXT NOT NULL DEFAULT '[]';
    """,
    # Schema 4: the single console account (P0c); password_hash is "scrypt$N$r$p$salt$hash".
    """
    CREATE TABLE auth (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        user TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
    # Schema 5: the key console sessions are signed with; rotated whenever the account is written.
    """
    ALTER TABLE auth ADD COLUMN session_secret TEXT NOT NULL DEFAULT '';
    """,
    # Schema 6 (P5, ADR-15): the event log, the quality signals, the index of kept prompt bodies, and the
    # fuller record of a model call. bundle_hash and style_hash stay '' until P11 and P12 compute them.
    """
    ALTER TABLE llm_calls ADD COLUMN kind TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN prompt_version TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN bundle_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN style_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN glossary_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN batch INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE llm_calls ADD COLUMN http_status INTEGER;
    ALTER TABLE llm_calls ADD COLUMN finish_reason TEXT NOT NULL DEFAULT '';
    ALTER TABLE llm_calls ADD COLUMN retries INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE llm_calls ADD COLUMN repairs INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE llm_calls ADD COLUMN cost_est REAL NOT NULL DEFAULT 0;
    CREATE INDEX llm_calls_job ON llm_calls (job_id);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        level TEXT NOT NULL,
        kind TEXT NOT NULL,
        job_id TEXT NOT NULL DEFAULT '',
        unit_id INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_job ON events (job_id, id);
    CREATE INDEX events_unit ON events (unit_id, id);
    CREATE INDEX events_kind ON events (kind, id);
    CREATE INDEX events_at ON events (at);
    CREATE TABLE llm_bodies (
        call_id INTEGER PRIMARY KEY REFERENCES llm_calls(id),
        file TEXT NOT NULL,
        byte_offset INTEGER NOT NULL,
        byte_length INTEGER NOT NULL,
        rotated INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX llm_bodies_file ON llm_bodies (file);
    CREATE TABLE quality_signals (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        unit_id INTEGER,
        version INTEGER,
        cue INTEGER,
        before_text TEXT NOT NULL DEFAULT '',
        after_text TEXT NOT NULL DEFAULT '',
        meta_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX quality_signals_kind ON quality_signals (kind, id);
    CREATE INDEX quality_signals_unit ON quality_signals (unit_id, id);
    """,
    # Schema 7 (P6, ADR-13): console settings and API keys. Keys of `settings` that start with "_" belong to the
    # worker (the one-time config.json import and the env key seeding leave their marker there).
    # api_keys.value is stored as typed: the worker has to read it back to call the provider (FR-20.3).
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE api_keys (
        id INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        value TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ok',     -- ok | invalid | exhausted
        last_used_at REAL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX api_keys_provider ON api_keys (provider, id);
    """,
    # Schema 8 (P8, ADR-11): jobs and provider health. A job is a row, not a file. ``job_id`` is the public id
    # (the old sha1 for the first job of a file, so imported and script jobs keep theirs); ``key`` is that sha1,
    # the thing at most one open job may hold per ``kind`` (FR-15.3). ``payload_json`` is the job as the worker
    # passes it around (paths, languages, retranslate range, ...); the columns are what the scheduler queries.
    # ``prio`` is the class (interactive 200, retranslate 100, auto 0); ``tier`` is '' (any model) or 'strong'.
    # ``lease_until`` is on the monotonic clock of the running process and means nothing after a restart, which
    # recovers every running row at startup; ``not_before`` is wall-clock time.
    """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        job_id TEXT NOT NULL UNIQUE,
        key TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'publish',
        mode TEXT NOT NULL DEFAULT 'fill',
        origin TEXT NOT NULL DEFAULT 'manual',
        prio INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL,
        wait_reason TEXT NOT NULL DEFAULT '',
        series_key TEXT NOT NULL DEFAULT '',
        unit_id INTEGER,
        target_lang TEXT NOT NULL DEFAULT '',
        tier TEXT NOT NULL DEFAULT '',
        not_before REAL NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until REAL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        base_version INTEGER,
        source_sha256 TEXT NOT NULL DEFAULT '',
        snapshot_json TEXT NOT NULL DEFAULT '{}',
        payload_json TEXT NOT NULL DEFAULT '{}',
        result TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL
    );
    CREATE UNIQUE INDEX jobs_open ON jobs (key, kind) WHERE state IN ('queued', 'running', 'waiting', 'held');
    CREATE INDEX jobs_state ON jobs (state, not_before);
    CREATE INDEX jobs_series ON jobs (series_key, started_at);
    CREATE INDEX jobs_key ON jobs (key);
    CREATE TABLE provider_health (
        provider TEXT NOT NULL,
        model TEXT NOT NULL,              -- '*' is the provider as a whole (a refused key)
        state TEXT NOT NULL DEFAULT 'closed',   -- closed | open | half_open | exhausted | invalid
        failures INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 0,
        until REAL NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        PRIMARY KEY (provider, model)
    );
    """,
    # Schema 9 (P8, FR-16, ADR-11): versions carry an origin (machine | edit | revert | external | import), may be
    # candidate / removed / pruned, and a publish is announced (publishing = 1) before the file is renamed into
    # place, so a crash in between is settled at start-up. edited_at is the last cue edit folded into a version
    # (FR-16.7). At most one current version per file.
    """
    ALTER TABLE translations ADD COLUMN origin TEXT NOT NULL DEFAULT '';
    ALTER TABLE translations ADD COLUMN publishing INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE translations ADD COLUMN edited_at REAL NOT NULL DEFAULT 0;
    ALTER TABLE translations ADD COLUMN job_id TEXT NOT NULL DEFAULT '';
    UPDATE translations SET origin = CASE trigger
        WHEN 'cue_edit' THEN 'edit' WHEN 'revert' THEN 'revert' WHEN 'external' THEN 'external' ELSE 'machine' END;
    CREATE UNIQUE INDEX translations_one_current ON translations (output_path) WHERE status = 'current';
    """,
    # Schema 10 (P10, FR-14, FR-15.4, FR-15.6, ADR-10): the library as Bazarr lists it (``catalog_items``, a cache
    # with an age), what the last attempts to read it did (``catalog_state``, one row), the admin's "do not
    # translate this" (``unit_decisions``) and the per-series override of ``auto_request``. An item is identified by
    # (kind, bazarr_id), never by a path; ``gone_at`` is set when a full, plausible read no longer lists it. The
    # ``held`` state is gone from the queue: the index of open jobs forgets it.
    """
    CREATE TABLE catalog_items (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,                  -- series | movie | episode
        bazarr_id TEXT NOT NULL,
        parent_id INTEGER,                   -- the series row an episode belongs to
        series_key TEXT NOT NULL DEFAULT '',
        season INTEGER,
        episode INTEGER,
        title TEXT NOT NULL DEFAULT '',
        video_path TEXT NOT NULL DEFAULT '',
        tvdb_id TEXT NOT NULL DEFAULT '',
        subtitles_json TEXT NOT NULL DEFAULT '[]',
        missing_json TEXT NOT NULL DEFAULT '[]',
        synced_at REAL NOT NULL,
        gone_at REAL,
        UNIQUE (kind, bazarr_id)
    );
    CREATE INDEX catalog_parent ON catalog_items (parent_id);
    CREATE TABLE catalog_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        synced_at REAL NOT NULL DEFAULT 0,
        attempted_at REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT '',
        warning TEXT NOT NULL DEFAULT '',
        items INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE unit_decisions (
        unit_id INTEGER NOT NULL REFERENCES units(id),
        target_lang TEXT NOT NULL,
        decision TEXT NOT NULL,              -- ignored
        at REAL NOT NULL,
        PRIMARY KEY (unit_id, target_lang)
    );
    ALTER TABLE series ADD COLUMN auto_request TEXT NOT NULL DEFAULT '';
    DROP INDEX jobs_open;
    CREATE UNIQUE INDEX jobs_open ON jobs (key, kind) WHERE state IN ('queued', 'running', 'waiting');
    CREATE INDEX jobs_unit ON jobs (unit_id, target_lang);
    """,
    # Migration 11 (P11, FR-21.2): the bundle of a job's latest attempt. A table of its own, because a bundle is
    # hundreds of KB and many queue queries read whole `jobs` rows. The job's deletes sweep it (see JobStore).
    """
    CREATE TABLE job_bundles (
        job_id TEXT PRIMARY KEY,
        bundle_hash TEXT NOT NULL,
        bundle_json TEXT NOT NULL
    );
    """,
    # Migration 12 (P12, FR-18, ADR-14): glossaries, style profiles and character cards in the database. An owner is
    # a series or a movie, named by (owner_kind, owner_key) like the folders the YAML used to live in (``series/<key>``,
    # ``movies/<key>``), so a movie has a glossary too and no row needs a series id. ``glossary_history`` keeps what
    # each save replaced (the old ``backups/glossary/``). ``style_profiles.scope`` is preset | series | unit.
    # ``translations.params_json`` says how a version was made when its other fields do not (a range, a variant).
    """
    ALTER TABLE translations ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}';
    CREATE TABLE glossaries (
        id INTEGER PRIMARY KEY,
        owner_kind TEXT NOT NULL,
        owner_key TEXT NOT NULL,
        lang TEXT NOT NULL,
        doc_json TEXT NOT NULL,
        rules_hash TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL,
        UNIQUE (owner_kind, owner_key, lang)
    );
    CREATE TABLE glossary_history (
        id INTEGER PRIMARY KEY,
        glossary_id INTEGER NOT NULL REFERENCES glossaries(id),
        version INTEGER NOT NULL,
        doc_json TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        at REAL NOT NULL,
        UNIQUE (glossary_id, version)
    );
    CREATE TABLE style_profiles (
        id INTEGER PRIMARY KEY,
        scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        doc_json TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL,
        UNIQUE (scope, scope_id)
    );
    CREATE TABLE character_cards (
        id INTEGER PRIMARY KEY,
        owner_kind TEXT NOT NULL,
        owner_key TEXT NOT NULL,
        name TEXT NOT NULL COLLATE NOCASE,
        doc_json TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at REAL NOT NULL,
        UNIQUE (owner_kind, owner_key, name)
    );
    """,
    # Migration 13 (P13, FR-4.4, FR-11.1.5): what the generators propose, one table for every source. The
    # fingerprint is the hash of the normalised content, so the same content is never stored twice for a series
    # and language: a rejected suggestion stays as a row and is why it is not proposed again. ``applied_json``
    # is what accepting changed, kept so it can be undone.
    """
    CREATE TABLE suggestions (
        id INTEGER PRIMARY KEY,
        series_id INTEGER NOT NULL REFERENCES series(id),
        target_lang TEXT NOT NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        sources_json TEXT NOT NULL DEFAULT '[]',
        auto_accepted INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        applied_json TEXT NOT NULL DEFAULT '{}',
        decided_at REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        UNIQUE (series_id, target_lang, fingerprint)
    );
    CREATE INDEX suggestions_status ON suggestions (status, series_id);
    ALTER TABLE series ADD COLUMN bootstrap_released INTEGER NOT NULL DEFAULT 0;
    """,
    # Migration 14 (review wave W3, R13-6, R13-5): ``identity`` is what a suggestion is about (its kind, name or pair,
    # and where it starts), blind to the wording a model gave it; the start-up fills it for every older row, decided
    # ones included, so a rejection still holds. The fingerprint stays. ``suggestion_skips`` remembers a candidate the
    # model could not answer, for a while, so it does not head the list of every run.
    """
    ALTER TABLE suggestions ADD COLUMN identity TEXT NOT NULL DEFAULT '';
    CREATE INDEX suggestions_identity ON suggestions (series_id, target_lang, identity);
    CREATE TABLE suggestion_skips (
        series_id INTEGER NOT NULL REFERENCES series(id),
        target_lang TEXT NOT NULL,
        identity TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        at REAL NOT NULL,
        PRIMARY KEY (series_id, target_lang, identity)
    );
    """,
    # Migration 15 (P15, FR-19.5, ADR-15): ``metrics_daily`` keeps the figures of Insights per UTC day, split by labels
    # (model, prompt version...), so the long-term numbers outlive the events that are cleaned up. Rolled up from the
    # raw tables by ``Metrics.rollup``; the rows of day '0000-00-00' are its markers, not figures.
    """
    CREATE TABLE metrics_daily (
        day TEXT NOT NULL,
        name TEXT NOT NULL,
        labels_json TEXT NOT NULL DEFAULT '{}',
        value REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (day, name, labels_json)
    );
    CREATE INDEX quality_signals_at ON quality_signals (at);
    """,
    # Migration 16 (P16, FR-7.6): the series a call was made for, so the research budget (``research_max_calls_per_series``)
    # is counted from the calls themselves and not from ``jobs``, whose finished rows are cleaned up. NULL for the calls
    # made before this column and for those of a movie or of no series.
    """
    ALTER TABLE llm_calls ADD COLUMN series_id INTEGER;
    CREATE INDEX llm_calls_series ON llm_calls (series_id, task);
    """,
)
