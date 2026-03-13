"""
One-time migration: file-based output → database.
Reads every raw_extraction.json in output/ and inserts into comic_niche.
Also detects any previously generated audio files.
"""

import json
from pathlib import Path

from db import init_db, get_db
from tagger import tag_line

OUTPUT_DIR = Path(__file__).parent / "output"


def migrate():
    init_db()
    print("Database table ready.\n")

    if not OUTPUT_DIR.is_dir():
        print("No output/ directory found. Nothing to migrate.")
        return

    for project_dir in sorted(OUTPUT_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        raw_path = project_dir / "raw_extraction.json"
        if not raw_path.exists():
            continue

        project_name = project_dir.name

        # Skip if already migrated
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM comic_niche WHERE project_name = %s",
                    (project_name,),
                )
                if cur.fetchone()[0] > 0:
                    print(f"  {project_name}: already in DB, skipping.")
                    continue

        print(f"  Migrating: {project_name}")

        with open(raw_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        characters = data.get("characters", {})
        char_line_counters: dict[str, int] = {}
        global_order = 0
        rows = []

        for page in data["pages"]:
            if not page["has_dialogue"]:
                continue

            page_num = page["page_number"]
            line_order = 0

            for line_data in page["lines"]:
                char = line_data["character"]
                tagged = tag_line(line_data)
                desc = characters.get(char, "")

                if char not in char_line_counters:
                    char_line_counters[char] = 0
                char_idx = char_line_counters[char]
                char_line_counters[char] += 1

                # Check for existing audio
                audio_path = (
                    project_dir
                    / "audio"
                    / char.lower()
                    / f"line_{char_idx + 1:03d}.mp3"
                )
                has_audio = audio_path.exists()
                audio_url = (
                    f"/api/audio/{project_name}/{char.lower()}/line_{char_idx + 1:03d}.mp3"
                    if has_audio
                    else None
                )

                rows.append(
                    (
                        project_name,
                        page_num,
                        None,  # page_image_path — not available for legacy data
                        line_order,
                        global_order,
                        char,
                        desc,
                        line_data["dialogue"],
                        tagged,
                        line_data.get("emotion", ""),
                        line_data.get("emotion_shift"),
                        line_data.get("shift_at"),
                        line_data.get("notes", ""),
                        audio_url,
                        has_audio,
                    )
                )

                line_order += 1
                global_order += 1

        # Bulk insert
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO comic_niche
                        (project_name, page_number, page_image_path,
                         line_order, global_order,
                         character_name, character_description,
                         dialogue, tagged_dialogue,
                         emotion, emotion_shift, shift_at, notes,
                         audio_url, audio_generated)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    rows,
                )
            conn.commit()

        generated = sum(1 for r in rows if r[14])
        print(f"    {len(rows)} lines inserted ({generated} with audio).")

    print("\nMigration complete.")


if __name__ == "__main__":
    migrate()
