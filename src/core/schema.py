"""Database schema migrations for all hunt modules.

Each migration is a SQL string applied in order.
"""

MIGRATIONS: list[str] = [
    # ── Streaming Farm ──────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS stream_accounts (
        id              TEXT PRIMARY KEY,
        platform        TEXT NOT NULL DEFAULT 'spotify',
        username        TEXT NOT NULL,
        password_enc    TEXT NOT NULL,
        proxy_id        TEXT,
        device_id       TEXT,
        status          TEXT NOT NULL DEFAULT 'active',
        streams_today   INTEGER NOT NULL DEFAULT 0,
        streams_total   INTEGER NOT NULL DEFAULT 0,
        last_stream_at  TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_stream_accounts_status ON stream_accounts(status);

    CREATE TABLE IF NOT EXISTS stream_playlists (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        track_count     INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS stream_tracks (
        id              TEXT PRIMARY KEY,
        playlist_id     TEXT NOT NULL REFERENCES stream_playlists(id),
        title           TEXT NOT NULL,
        artist          TEXT NOT NULL,
        uri             TEXT NOT NULL,
        duration_ms     INTEGER NOT NULL,
        play_order      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_stream_tracks_playlist ON stream_tracks(playlist_id);

    CREATE TABLE IF NOT EXISTS stream_logs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id      TEXT NOT NULL REFERENCES stream_accounts(id),
        track_id        TEXT NOT NULL REFERENCES stream_tracks(id),
        played_at       TEXT NOT NULL DEFAULT (datetime('now')),
        duration_sec    INTEGER NOT NULL,
        success         INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_stream_logs_account ON stream_logs(account_id);
    """,

    # ── KDP Factory ─────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS kdp_books (
        id              TEXT PRIMARY KEY,
        topic           TEXT NOT NULL,
        title           TEXT NOT NULL,
        subtitle        TEXT,
        author_name     TEXT NOT NULL,
        chapter_count   INTEGER NOT NULL DEFAULT 0,
        word_count      INTEGER NOT NULL DEFAULT 0,
        language        TEXT NOT NULL DEFAULT 'en',
        status          TEXT NOT NULL DEFAULT 'draft',
        book_dir        TEXT,
        pdf_path        TEXT,
        cover_path      TEXT,
        asin            TEXT,
        published_at    TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS kdp_chapters (
        id              TEXT PRIMARY KEY,
        book_id         TEXT NOT NULL REFERENCES kdp_books(id),
        chapter_num     INTEGER NOT NULL,
        title           TEXT NOT NULL,
        content_md      TEXT NOT NULL,
        word_count      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_kdp_chapters_book ON kdp_chapters(book_id);

    CREATE TABLE IF NOT EXISTS kdp_sales (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id         TEXT NOT NULL REFERENCES kdp_books(id),
        sale_date       TEXT NOT NULL,
        units           INTEGER NOT NULL DEFAULT 0,
        revenue_cents   INTEGER NOT NULL DEFAULT 0,
        kenp_reads      INTEGER NOT NULL DEFAULT 0,
        marketplace     TEXT NOT NULL DEFAULT 'US'
    );
    CREATE INDEX IF NOT EXISTS idx_kdp_sales_book ON kdp_sales(book_id);
    """,

    # ── Deepfake / AI Media ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS media_voices (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        source_path     TEXT NOT NULL,
        model_path      TEXT,
        language        TEXT NOT NULL DEFAULT 'en',
        sample_rate     INTEGER NOT NULL DEFAULT 44100,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS media_clones (
        id              TEXT PRIMARY KEY,
        voice_id        TEXT NOT NULL REFERENCES media_voices(id),
        input_text      TEXT NOT NULL,
        output_path     TEXT NOT NULL,
        duration_sec    REAL,
        quality_score   REAL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_media_clones_voice ON media_clones(voice_id);

    CREATE TABLE IF NOT EXISTS media_influencers (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        style           TEXT NOT NULL,
        platform        TEXT NOT NULL DEFAULT 'instagram',
        handle          TEXT,
        bio             TEXT,
        post_count      INTEGER NOT NULL DEFAULT 0,
        follower_count  INTEGER NOT NULL DEFAULT 0,
        status          TEXT NOT NULL DEFAULT 'active',
        sd_config       TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS media_posts (
        id              TEXT PRIMARY KEY,
        influencer_id   TEXT NOT NULL REFERENCES media_influencers(id),
        platform        TEXT NOT NULL,
        post_type       TEXT NOT NULL DEFAULT 'image',
        caption         TEXT,
        image_path      TEXT,
        video_path      TEXT,
        likes           INTEGER NOT NULL DEFAULT 0,
        comments        INTEGER NOT NULL DEFAULT 0,
        posted_at       TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_media_posts_influencer ON media_posts(influencer_id);
    """,
]
