"""
Output formatter — writes scripts to files.
"""

import os
import re
from pathlib import Path
import config


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    return re.sub(r'[^\w\-]', '_', name).strip('_').lower()


def save_scripts(
    comic_name: str,
    chronological: str,
    per_character: dict[str, str],
    elevenlabs_ready: dict[str, str],
) -> str:
    """
    Save all script outputs to the output directory.

    Creates a folder per comic:
      output/
        comic_name/
          full_script.txt          — chronological with all characters
          characters/
            CHARACTER.txt           — per-character with page markers
          elevenlabs/
            CHARACTER.txt           — clean tagged text, paste into ElevenLabs

    Returns the output folder path.
    """
    safe_name = sanitize_filename(comic_name)
    base_dir = Path(config.OUTPUT_DIR) / safe_name
    char_dir = base_dir / "characters"
    el_dir = base_dir / "elevenlabs"

    os.makedirs(char_dir, exist_ok=True)
    os.makedirs(el_dir, exist_ok=True)

    # Full chronological script
    full_path = base_dir / "full_script.txt"
    full_path.write_text(chronological, encoding="utf-8")

    # Per-character scripts (with page markers)
    for char, script in per_character.items():
        fname = sanitize_filename(char) + ".txt"
        (char_dir / fname).write_text(script, encoding="utf-8")

    # ElevenLabs-ready scripts (clean, no markers)
    for char, script in elevenlabs_ready.items():
        fname = sanitize_filename(char) + ".txt"
        (el_dir / fname).write_text(script, encoding="utf-8")

    return str(base_dir)


def save_raw_extraction(comic_name: str, data: dict) -> str:
    """Save the raw JSON extraction for debugging/review."""
    import json

    safe_name = sanitize_filename(comic_name)
    base_dir = Path(config.OUTPUT_DIR) / safe_name
    os.makedirs(base_dir, exist_ok=True)

    raw_path = base_dir / "raw_extraction.json"
    raw_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return str(raw_path)
