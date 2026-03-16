"""
Database module — single table 'comic_niche' for everything.
"""

from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = "postgresql://app_user:p71d4ecaf55149042985cf1a738bb3524167069ff81f0faa3f4517ee8d35c5ef6@91.98.188.35:5432/myappdb"


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS comic_niche (
                    id SERIAL PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    page_image_path TEXT,
                    line_order INTEGER NOT NULL DEFAULT 0,
                    global_order INTEGER NOT NULL DEFAULT 0,
                    character_name TEXT NOT NULL,
                    character_description TEXT DEFAULT '',
                    voice_id TEXT DEFAULT '',
                    dialogue TEXT NOT NULL,
                    tagged_dialogue TEXT NOT NULL,
                    emotion TEXT DEFAULT '',
                    emotion_shift TEXT,
                    shift_at TEXT,
                    notes TEXT DEFAULT '',
                    audio_url TEXT,
                    audio_generated BOOLEAN DEFAULT FALSE,
                    instagram_url TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cn_project
                    ON comic_niche(project_name);
                CREATE INDEX IF NOT EXISTS idx_cn_project_page
                    ON comic_niche(project_name, page_number);
            """)
            # Add instagram_url column if missing (existing tables)
            cur.execute("""
                ALTER TABLE comic_niche ADD COLUMN IF NOT EXISTS instagram_url TEXT DEFAULT '';
            """)
        conn.commit()
