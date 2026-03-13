"""
Comic Niche Scripter — Main entry point.

Usage:
    python main.py                          # Process all images in input/
    python main.py --name "boxing_comic"    # Name the comic
    python main.py --images img1.png img2.png img3.png  # Specific images
    python main.py --model gpt-5.1          # Use a specific model
"""

import argparse
import mimetypes
import shutil
import sys
import os
from pathlib import Path

import boto3
import config
from extractor import extract_from_images
from tagger import (
    build_chronological_script,
    build_per_character_scripts,
    build_elevenlabs_ready,
    tag_line,
)
from formatter import save_scripts, save_raw_extraction
from db import init_db, get_db

MEDIA_DIR = Path(__file__).parent / "media"

# ── R2 ───────────────────────────────────────────────
R2_BUCKET = "algrow-voiceovers"
R2_BASE_URL = "https://audio.algrow.online"
s3 = boto3.client(
    "s3",
    endpoint_url="https://a65a27504118e6a07a782b3ea1ad592a.r2.cloudflarestorage.com",
    aws_access_key_id="306204626cd716b7a0c1655c280356e2",
    aws_secret_access_key="1d0f0c4817a5119290bf8ad826f7eb99449f028f91d35aec3131671e38b54f37",
    region_name="auto",
)

def upload_to_r2(local_path, r2_key):
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    s3.upload_file(str(local_path), R2_BUCKET, r2_key, ExtraArgs={"ContentType": content_type})
    return f"{R2_BASE_URL}/{r2_key}"


def collect_images_from_dir(directory: str) -> list[str]:
    """Collect and sort image files from a directory."""
    valid_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    images = []

    for f in sorted(Path(directory).iterdir()):
        if f.suffix.lower() in valid_exts:
            images.append(str(f))

    return images


def main():
    parser = argparse.ArgumentParser(
        description="Extract comic dialogue and generate ElevenLabs V3 tagged scripts."
    )
    parser.add_argument(
        "--name",
        type=str,
        default="comic",
        help="Name for this comic (used for output folder name)",
    )
    parser.add_argument(
        "--images",
        nargs="+",
        type=str,
        help="Specific image paths in order. If not provided, reads from input/ folder.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override the vision model (e.g., gpt-5.1, gpt-5-mini)",
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=None,
        help="Custom input directory (default: ./input/)",
    )

    args = parser.parse_args()

    # Override model if specified
    if args.model:
        config.VISION_MODEL = args.model

    # Collect images
    if args.images:
        image_paths = args.images
        # Verify all files exist
        for p in image_paths:
            if not os.path.isfile(p):
                print(f"Error: Image not found: {p}")
                sys.exit(1)
    else:
        input_dir = args.input_dir or config.INPUT_DIR
        if not os.path.isdir(input_dir):
            print(f"Error: Input directory not found: {input_dir}")
            sys.exit(1)
        image_paths = collect_images_from_dir(input_dir)

    if not image_paths:
        print("Error: No images found. Place images in input/ or use --images.")
        sys.exit(1)

    print(f"Processing {len(image_paths)} comic page(s)...")
    for i, p in enumerate(image_paths):
        print(f"  Page {i + 1}: {Path(p).name}")

    # Step 1: Extract dialogue and emotions via vision model
    print(f"\nSending to {config.VISION_MODEL} for analysis...")
    try:
        data = extract_from_images(image_paths)
    except Exception as e:
        print(f"Error during extraction: {e}")
        sys.exit(1)

    # Step 2: Build scripts
    print("Building tagged scripts...")

    chronological = build_chronological_script(data)
    per_character = build_per_character_scripts(data)
    elevenlabs_ready = build_elevenlabs_ready(data)

    # Step 3: Save file outputs
    output_path = save_scripts(args.name, chronological, per_character, elevenlabs_ready)
    raw_path = save_raw_extraction(args.name, data)

    # Step 4: Copy images to media/ and save to database
    print("Saving to database...")
    init_db()

    # Upload images to R2
    page_image_map = {}
    for i, img_path in enumerate(image_paths):
        ext = Path(img_path).suffix
        r2_key = f"comic/{args.name}/page_{i + 1}{ext}"
        r2_url = upload_to_r2(img_path, r2_key)
        page_image_map[i + 1] = r2_url
        print(f"    Uploaded page {i + 1} → {r2_url}")

    # Insert into DB
    characters = data.get("characters", {})
    global_order = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            # Clear existing rows for this project (re-run safe)
            cur.execute(
                "DELETE FROM comic_niche WHERE project_name = %s", (args.name,)
            )

            for page in data["pages"]:
                if not page["has_dialogue"]:
                    continue

                page_num = page["page_number"]
                img_path = page_image_map.get(page_num)
                line_order = 0

                for line_data in page["lines"]:
                    char = line_data["character"]
                    tagged = tag_line(line_data)
                    desc = characters.get(char, "")

                    cur.execute(
                        """
                        INSERT INTO comic_niche
                            (project_name, page_number, page_image_path,
                             line_order, global_order,
                             character_name, character_description,
                             dialogue, tagged_dialogue,
                             emotion, emotion_shift, shift_at, notes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            args.name, page_num, img_path,
                            line_order, global_order,
                            char, desc,
                            line_data["dialogue"], tagged,
                            line_data.get("emotion", ""),
                            line_data.get("emotion_shift"),
                            line_data.get("shift_at"),
                            line_data.get("notes", ""),
                        ),
                    )
                    line_order += 1
                    global_order += 1
        conn.commit()

    print(f"  {global_order} lines saved to database.")

    # Step 5: Print summary
    chars = data.get("characters", {})
    pages = data.get("pages", [])
    dialogue_pages = sum(1 for p in pages if p["has_dialogue"])
    silent_pages = len(pages) - dialogue_pages
    total_lines = sum(len(p["lines"]) for p in pages)

    print(f"\nDone!")
    print(f"  Pages processed:  {len(pages)}")
    print(f"  Dialogue pages:   {dialogue_pages}")
    print(f"  Silent panels:    {silent_pages}")
    print(f"  Total lines:      {total_lines}")
    print(f"  Characters found: {len(chars)}")
    for label, desc in chars.items():
        line_count = sum(
            1 for p in pages for l in p["lines"] if l["character"] == label
        )
        print(f"    {label}: {line_count} line(s) — {desc}")

    print(f"\nOutput saved to: {output_path}")
    print(f"  full_script.txt       — Full chronological script")
    print(f"  characters/           — Per-character scripts with page markers")
    print(f"  elevenlabs/           — Clean tagged text, paste into ElevenLabs")
    print(f"  raw_extraction.json   — Raw vision model output")

    # Also print the full script to console
    print(f"\n{'=' * 60}")
    print("FULL SCRIPT PREVIEW")
    print(f"{'=' * 60}\n")
    print(chronological)


if __name__ == "__main__":
    main()
