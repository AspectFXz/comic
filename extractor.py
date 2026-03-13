"""
Vision-based comic panel extractor.
Sends comic images to OpenAI vision API and extracts structured dialogue
with character mappings and reading order.
"""

import base64
import json
from pathlib import Path

from openai import OpenAI

import config

client = OpenAI(api_key=config.OPENAI_API_KEY)

SYSTEM_PROMPT = """You are an expert comic book analyst. You will receive comic panel images IN ORDER (page 1, page 2, etc.) from a comic strip.

Your job is to extract ALL information needed to create a voiceover script.

For EACH page, you must:

1. **Identify characters** — Give each character a consistent label based on their appearance. Use a short name or descriptor (e.g., "COACH", "GIRL", "BERN", "JESS"). If a character's name is mentioned in dialogue, use that name. Track characters across ALL pages — the same character must always get the same label.

2. **Extract dialogue in chronological reading order** — Read speech bubbles left-to-right, top-to-bottom following standard comic reading order. Pay attention to speech bubble tails (pointers) to determine which character is speaking. Include ALL text exactly as written, preserving bold markers with asterisks (e.g., **bold**).

3. **Detect emotional context** — For each line of dialogue, determine the emotional tone based on:
   - The character's facial expression in that panel
   - The context of what's happening in the story
   - The content and punctuation of the dialogue itself
   - Bold/italic text indicating emphasis
   - Ellipses indicating hesitation or trailing off
   - The overall mood/atmosphere of the scene

4. **Note silent panels** — If a page has NO dialogue, describe what's happening visually in a brief note. These are important for the story's pacing.

Return your analysis as a JSON object with this EXACT structure:

```json
{
  "characters": {
    "CHARACTER_LABEL": "Brief visual description for consistent identification"
  },
  "pages": [
    {
      "page_number": 1,
      "has_dialogue": true,
      "visual_note": null,
      "lines": [
        {
          "character": "CHARACTER_LABEL",
          "dialogue": "The exact dialogue text",
          "emotion": "primary emotional tag",
          "emotion_shift": "secondary tag if emotion changes mid-line, otherwise null",
          "shift_at": "the word/phrase where the emotion shifts, otherwise null",
          "bold_words": ["any", "bold", "words"],
          "notes": "any relevant context about delivery"
        }
      ]
    },
    {
      "page_number": 2,
      "has_dialogue": false,
      "visual_note": "Description of what happens in this silent panel",
      "lines": []
    }
  ]
}
```

IMPORTANT RULES:
- Emotion tags should be lowercase and natural (e.g., "excited", "nervous", "deadpan", "whispering", "in awe", "sarcastically", "trembling")
- Be creative with emotion tags — go beyond basic emotions. Use tags like "mockingly", "through gritted teeth", "barely containing laughter", "voice breaking", etc.
- If a character's emotion shifts MID-LINE, capture both the primary emotion and the shift point
- Silent/no-dialogue panels MUST still be included with has_dialogue: false and a visual_note
- Reading order is CRITICAL — get it right by following bubble positions and tail pointers
- Keep character labels CONSISTENT across all pages
- Return ONLY valid JSON, no other text"""


def encode_image(image_path: str) -> str:
    """Encode an image file to base64."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def get_image_media_type(image_path: str) -> str:
    """Determine the media type from file extension."""
    ext = Path(image_path).suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return media_types.get(ext, "image/png")


def extract_from_images(image_paths: list[str]) -> dict:
    """
    Send comic images to vision model and extract structured dialogue data.

    Args:
        image_paths: List of image file paths in chronological order.

    Returns:
        Parsed JSON dict with characters and pages.
    """
    # Build the message content with all images
    content = [
        {
            "type": "text",
            "text": f"Here are {len(image_paths)} comic pages in order. Analyze them and extract all dialogue, characters, and emotions.",
        }
    ]

    for i, path in enumerate(image_paths):
        b64 = encode_image(path)
        media_type = get_image_media_type(path)
        content.append(
            {
                "type": "text",
                "text": f"--- PAGE {i + 1} ---",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{b64}",
                    "detail": "high",
                },
            }
        )

    response = client.chat.completions.create(
        model=config.VISION_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        max_completion_tokens=40096,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]  # remove first line
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

    return json.loads(raw)
