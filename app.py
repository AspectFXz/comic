"""
Comic Voiceover Studio — Flask backend, DB-backed, R2 storage.
"""

import io
import os
import mimetypes
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

import boto3
import requests as http_requests
from flask import Flask, Response, jsonify, request, send_from_directory
from psycopg2.extras import RealDictCursor

from db import get_db, init_db
from extractor import extract_from_images
from tagger import (
    tag_line,
    build_chronological_script,
    build_per_character_scripts,
    build_elevenlabs_ready,
)
from formatter import save_scripts, save_raw_extraction

app = Flask(__name__, static_folder="static")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
MEDIA_DIR = os.path.join(os.path.dirname(__file__), "media")
WAVESPEED_API_KEY = "9c563897882372a8f252224a2ea44a9abb6331e2cf818424923e6492c2ee6e90"

# ── Cloudflare R2 ────────────────────────────────────
R2_BUCKET = "algrow-voiceovers"
R2_BASE_URL = "https://audio.algrow.online"
R2_ACCESS_KEY = "306204626cd716b7a0c1655c280356e2"
R2_SECRET_KEY = "1d0f0c4817a5119290bf8ad826f7eb99449f028f91d35aec3131671e38b54f37"
R2_ENDPOINT = "https://a65a27504118e6a07a782b3ea1ad592a.r2.cloudflarestorage.com"

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
)


def upload_to_r2(local_path, r2_key):
    """Upload a file to R2 and return its public URL."""
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    s3.upload_file(
        str(local_path),
        R2_BUCKET,
        r2_key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{R2_BASE_URL}/{r2_key}"


def upload_bytes_to_r2(data, r2_key, content_type="audio/mpeg"):
    """Upload raw bytes to R2 and return its public URL."""
    s3.put_object(
        Bucket=R2_BUCKET,
        Key=r2_key,
        Body=data,
        ContentType=content_type,
    )
    return f"{R2_BASE_URL}/{r2_key}"

app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

try:
    init_db()
except Exception as e:
    print(f"[WARN] init_db failed: {e}")


# ── Pages ─────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/health")
def health():
    import sys
    checks = {"python": sys.version, "db_url_set": bool(os.getenv("DATABASE_URL"))}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = str(e)
    return jsonify(checks)


@app.route("/api/config")
def get_config():
    return jsonify({"api_key": WAVESPEED_API_KEY})


# ── Projects ──────────────────────────────────────────

@app.route("/api/projects")
def list_projects():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT project_name,
                       COUNT(*) as line_count,
                       COUNT(*) FILTER (WHERE audio_generated) as generated_count,
                       COUNT(DISTINCT character_name) as character_count,
                       COUNT(DISTINCT page_number) as page_count
                FROM comic_niche
                GROUP BY project_name
                ORDER BY project_name
            """)
            return jsonify(cur.fetchall())


# ── Single project ────────────────────────────────────

@app.route("/api/project/<name>")
def get_project(name):
    name = unquote(name)
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Characters (only those with lines)
            cur.execute("""
                SELECT character_name,
                       MAX(character_description) as description,
                       MAX(voice_id) as voice_id,
                       COUNT(*) as line_count
                FROM comic_niche
                WHERE project_name = %s
                GROUP BY character_name
                ORDER BY MIN(global_order)
            """, (name,))
            characters = cur.fetchall()

            # All lines with computed char_line_index
            cur.execute("""
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY character_name
                           ORDER BY global_order
                       ) - 1 AS char_line_index
                FROM comic_niche
                WHERE project_name = %s
                ORDER BY global_order
            """, (name,))
            lines = cur.fetchall()

            # Group by page
            pages = {}
            for line in lines:
                pn = line["page_number"]
                if pn not in pages:
                    pages[pn] = {
                        "page_number": pn,
                        "image_path": line["page_image_path"],
                        "lines": [],
                    }
                pages[pn]["lines"].append(line)

            return jsonify({
                "name": name,
                "characters": characters,
                "pages": sorted(pages.values(), key=lambda p: p["page_number"]),
            })


# ── Create project (upload images → extract → DB) ────

@app.route("/api/project/create", methods=["POST"])
def create_project():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Project name is required"}), 400

    files = request.files.getlist("images")
    if not files:
        return jsonify({"error": "At least one image is required"}), 400

    # Save images to temp dir
    temp_dir = tempfile.mkdtemp()
    image_paths = []
    for i, f in enumerate(files):
        ext = Path(f.filename).suffix or ".png"
        path = os.path.join(temp_dir, f"page_{i + 1:03d}{ext}")
        f.save(path)
        image_paths.append(path)

    # Run the vision extraction pipeline
    try:
        data = extract_from_images(image_paths)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({"error": f"Extraction failed: {e}"}), 500

    # Upload images to R2
    page_image_map = {}
    for i, img_path in enumerate(image_paths):
        ext = Path(img_path).suffix
        r2_key = f"comic/{name}/page_{i + 1}{ext}"
        r2_url = upload_to_r2(img_path, r2_key)
        page_image_map[i + 1] = r2_url

    shutil.rmtree(temp_dir, ignore_errors=True)

    # Save file-based outputs (skip on read-only filesystems like Vercel)
    try:
        chrono = build_chronological_script(data)
        per_char = build_per_character_scripts(data)
        el_ready = build_elevenlabs_ready(data)
        save_scripts(name, chrono, per_char, el_ready)
        save_raw_extraction(name, data)
    except OSError:
        pass

    # Insert into DB
    characters = data.get("characters", {})
    global_order = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM comic_niche WHERE project_name = %s", (name,))

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
                    cur.execute("""
                        INSERT INTO comic_niche
                            (project_name, page_number, page_image_path,
                             line_order, global_order,
                             character_name, character_description,
                             dialogue, tagged_dialogue,
                             emotion, emotion_shift, shift_at, notes)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        name, page_num, img_path, line_order, global_order,
                        char, desc, line_data["dialogue"], tagged,
                        line_data.get("emotion", ""),
                        line_data.get("emotion_shift"),
                        line_data.get("shift_at"),
                        line_data.get("notes", ""),
                    ))
                    line_order += 1
                    global_order += 1
        conn.commit()

    return jsonify({"ok": True, "project_name": name, "lines": global_order})


# ── Upload image for an existing page ────────────────

@app.route("/api/project/<name>/page/<int:page_num>/image", methods=["POST"])
def upload_page_image(name, page_num):
    name = unquote(name)
    f = request.files.get("image")
    if not f:
        return jsonify({"error": "No image file"}), 400

    ext = Path(f.filename).suffix or ".png"

    # Save to temp, upload to R2
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    f.save(tmp.name)
    r2_key = f"comic/{name}/page_{page_num}{ext}"
    img_url = upload_to_r2(tmp.name, r2_key)
    os.unlink(tmp.name)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE comic_niche SET page_image_path = %s
                WHERE project_name = %s AND page_number = %s
            """, (img_url, name, page_num))
        conn.commit()

    return jsonify({"ok": True, "image_path": img_url})


# ── Voice ID ──────────────────────────────────────────

@app.route("/api/project/<name>/voice", methods=["PUT"])
def update_voice(name):
    name = unquote(name)
    body = request.json
    char = body.get("character")
    voice_id = body.get("voice_id", "")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE comic_niche
                SET voice_id = %s
                WHERE project_name = %s AND character_name = %s
            """, (voice_id, name, char))
        conn.commit()

    return jsonify({"ok": True})


# ── Generate Audio ────────────────────────────────────

@app.route("/api/generate", methods=["POST"])
def generate_audio():
    body = request.json
    api_key = body.get("api_key") or WAVESPEED_API_KEY
    text = body.get("text")
    voice_id = body.get("voice_id", "Alice")
    stability = body.get("stability", 0.5)
    similarity = body.get("similarity", 1)
    line_id = body.get("line_id")
    comic_name = body.get("comic_name")
    character = body.get("character")
    char_line_index = body.get("char_line_index", 0)

    if not api_key:
        return jsonify({"error": "Set WAVESPEED_API_KEY in .env"}), 400
    if not text:
        return jsonify({"error": "Text is required"}), 400

    try:
        from wavespeed import Client

        client = Client(api_key=api_key)
        output = client.run("elevenlabs/eleven-v3", {
            "similarity": similarity,
            "stability": stability,
            "text": text,
            "use_speaker_boost": True,
            "voice_id": voice_id,
        })

        audio_url = output["outputs"][0]

        # Download from WaveSpeed and upload to R2
        final_url = audio_url
        if comic_name and character:
            resp = http_requests.get(audio_url, timeout=60)
            if resp.status_code == 200:
                r2_key = f"comic/{comic_name}/audio/{character.lower()}/line_{char_line_index + 1:03d}.mp3"
                final_url = upload_bytes_to_r2(resp.content, r2_key)

        # Update database
        if line_id:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE comic_niche
                        SET audio_url = %s, audio_generated = TRUE
                        WHERE id = %s
                    """, (final_url, line_id))
                conn.commit()

        return jsonify({
            "status": "completed",
            "audio_url": final_url,
        })

    except ImportError:
        return jsonify({"error": "wavespeed not installed. Run: pip install wavespeed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Migrate local files to R2 ────────────────────────

@app.route("/api/migrate-r2", methods=["POST"])
def migrate_to_r2():
    """Upload all local media + audio to R2 and update DB paths."""
    migrated = 0

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Migrate page images
            cur.execute("""
                SELECT DISTINCT project_name, page_number, page_image_path
                FROM comic_niche
                WHERE page_image_path IS NOT NULL
                  AND page_image_path LIKE '/api/media/%'
            """)
            for row in cur.fetchall():
                rel = row["page_image_path"].replace("/api/media/", "", 1)
                local = Path(MEDIA_DIR) / rel
                if local.exists():
                    r2_key = f"comic/{rel}"
                    r2_url = upload_to_r2(str(local), r2_key)
                    cur.execute("""
                        UPDATE comic_niche SET page_image_path = %s
                        WHERE project_name = %s AND page_number = %s
                    """, (r2_url, row["project_name"], row["page_number"]))
                    migrated += 1

            # Migrate audio files
            cur.execute("""
                SELECT id, audio_url FROM comic_niche
                WHERE audio_url IS NOT NULL
                  AND audio_url LIKE '/api/audio/%'
            """)
            for row in cur.fetchall():
                rel = row["audio_url"].replace("/api/audio/", "", 1)
                local = Path(OUTPUT_DIR) / rel.split("/")[0] / "audio" / "/".join(rel.split("/")[1:])
                if local.exists():
                    r2_key = f"comic/{rel.split('/')[0]}/audio/{'/'.join(rel.split('/')[1:])}"
                    r2_url = upload_to_r2(str(local), r2_key)
                    cur.execute("UPDATE comic_niche SET audio_url = %s WHERE id = %s", (r2_url, row["id"]))
                    migrated += 1

        conn.commit()

    return jsonify({"ok": True, "migrated": migrated})


# ── Download all audio as ZIP ─────────────────────────

@app.route("/api/project/<name>/download")
def download_project_audio(name):
    """Stream a ZIP of all generated audio files for a project."""
    name = unquote(name)
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT character_name, audio_url, char_line_index
                FROM (
                    SELECT character_name, audio_url,
                           ROW_NUMBER() OVER (
                               PARTITION BY character_name ORDER BY global_order
                           ) - 1 AS char_line_index
                    FROM comic_niche
                    WHERE project_name = %s AND audio_generated = TRUE
                      AND audio_url IS NOT NULL
                    ORDER BY global_order
                ) sub
            """, (name,))
            rows = cur.fetchall()

    if not rows:
        return jsonify({"error": "No generated audio to download"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            char = row["character_name"]
            idx = row["char_line_index"]
            url = row["audio_url"]
            filename = f"{char}/line_{idx + 1:03d}.mp3"
            try:
                resp = http_requests.get(url, timeout=30)
                if resp.status_code == 200:
                    zf.writestr(filename, resp.content)
            except Exception:
                continue

    buf.seek(0)
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}_voiceovers.zip"'},
    )


# ── Serve files (fallback for old local paths) ───────

@app.route("/api/audio/<comic>/<character>/<filename>")
def serve_audio(comic, character, filename):
    audio_dir = Path(OUTPUT_DIR) / comic / "audio" / character
    return send_from_directory(str(audio_dir), filename)


@app.route("/api/media/<path:filepath>")
def serve_media(filepath):
    return send_from_directory(MEDIA_DIR, filepath)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
