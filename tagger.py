"""
Expression tagger for ElevenLabs V3 Audio Tags.
Takes extracted comic data and produces tagged scripts.
"""


def tag_line(line: dict) -> str:
    """
    Convert a single extracted dialogue line into an ElevenLabs V3 tagged string.

    Handles:
    - Primary emotion tag at the start
    - Mid-line emotion shifts
    - Bold word emphasis (kept as-is for reference)
    """
    emotion = line["emotion"]
    dialogue = line["dialogue"]
    shift = line.get("emotion_shift")
    shift_at = line.get("shift_at")

    if shift and shift_at and shift_at in dialogue:
        # Split the dialogue at the shift point and tag each part
        idx = dialogue.index(shift_at)
        before = dialogue[:idx].strip()
        after = dialogue[idx:].strip()

        if before:
            tagged = f"[{emotion}] {before} [{shift}] {after}"
        else:
            tagged = f"[{shift}] {after}"
    else:
        tagged = f"[{emotion}] {dialogue}"

    return tagged


def build_chronological_script(data: dict) -> str:
    """
    Build the full chronological voiceover script with expression tags.

    Returns a formatted string with page markers, character labels,
    tagged dialogue, and visual notes for silent panels.
    """
    lines = []
    characters = data.get("characters", {})

    # Character roster at the top
    lines.append("=" * 60)
    lines.append("CHARACTER ROSTER")
    lines.append("=" * 60)
    for label, desc in characters.items():
        lines.append(f"  {label}: {desc}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("SCRIPT (Chronological Order)")
    lines.append("=" * 60)
    lines.append("")

    for page in data["pages"]:
        page_num = page["page_number"]
        lines.append(f"--- PAGE {page_num} ---")

        if not page["has_dialogue"]:
            note = page.get("visual_note", "No dialogue")
            lines.append(f"  [VISUAL] {note}")
        else:
            for line_data in page["lines"]:
                char = line_data["character"]
                tagged = tag_line(line_data)
                lines.append(f"  {char}: {tagged}")

        lines.append("")

    return "\n".join(lines)


def build_per_character_scripts(data: dict) -> dict[str, str]:
    """
    Build separate scripts for each character, containing only their lines
    in chronological order. Ready to paste into ElevenLabs per voice.

    Returns a dict of {character_label: script_string}.
    """
    char_lines: dict[str, list[str]] = {}

    for page in data["pages"]:
        if not page["has_dialogue"]:
            continue
        for line_data in page["lines"]:
            char = line_data["character"]
            tagged = tag_line(line_data)
            page_num = page["page_number"]
            if char not in char_lines:
                char_lines[char] = []
            char_lines[char].append(f"  [Page {page_num}] {tagged}")

    result = {}
    for char, lines in char_lines.items():
        desc = data.get("characters", {}).get(char, "")
        header = f"{'=' * 60}\n{char}"
        if desc:
            header += f" — {desc}"
        header += f"\n{'=' * 60}\n"
        result[char] = header + "\n".join(lines) + "\n"

    return result


def build_elevenlabs_ready(data: dict) -> dict[str, str]:
    """
    Build clean per-character scripts with ONLY the tagged dialogue,
    no page markers or metadata. Pure copy-paste into ElevenLabs.

    Returns a dict of {character_label: clean_tagged_text}.
    """
    char_lines: dict[str, list[str]] = {}

    for page in data["pages"]:
        if not page["has_dialogue"]:
            continue
        for line_data in page["lines"]:
            char = line_data["character"]
            tagged = tag_line(line_data)
            if char not in char_lines:
                char_lines[char] = []
            char_lines[char].append(tagged)

    return {char: "\n".join(lines) for char, lines in char_lines.items()}
