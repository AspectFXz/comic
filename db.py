"""
Database module — single table 'comic_niche' for everything.
"""

import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)


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
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cn_project
                    ON comic_niche(project_name);
                CREATE INDEX IF NOT EXISTS idx_cn_project_page
                    ON comic_niche(project_name, page_number);
            """)
        conn.commit()
