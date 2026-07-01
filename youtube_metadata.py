"""Tạo title.txt và description.txt cho YouTube (Stage 4 + timeline từ transcript)."""

import os
import re
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from dotenv import load_dotenv

from config import PROMPTS_DIR, TRANSCRIPT_DIR, YOUTUBE_DIR

load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"
TIMESTAMP_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$")


def find_first_transcript():
    txt_files = sorted(
        f
        for f in os.listdir(TRANSCRIPT_DIR)
        if os.path.isfile(os.path.join(TRANSCRIPT_DIR, f)) and f.lower().endswith(".txt")
    )
    if not txt_files:
        return None
    return os.path.join(TRANSCRIPT_DIR, txt_files[0])


def parse_transcript_lines(text):
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = TIMESTAMP_RE.match(line)
        if match:
            lines.append(line)
        elif lines:
            lines[-1] = f"{lines[-1]} {line}"
    return lines


def timestamp_to_seconds(ts):
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def format_youtube_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def shorten(text, max_len=55):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 3].rsplit(" ", 1)[0]
    return cut + "..."


def build_timeline(transcript_lines, min_gap_sec=40):
    chapters = []
    last_sec = -min_gap_sec
    for line in transcript_lines:
        match = TIMESTAMP_RE.match(line)
        if not match:
            continue
        sec = timestamp_to_seconds(match.group(1))
        label = shorten(match.group(2))
        if not chapters or sec - last_sec >= min_gap_sec:
            chapters.append((format_youtube_time(sec), label))
            last_sec = sec
    if chapters and chapters[0][0] != "0:00":
        first = TIMESTAMP_RE.match(transcript_lines[0])
        if first:
            chapters[0] = (format_youtube_time(0), shorten(first.group(2)))
    return chapters


def load_stage4_prompt():
    path = os.path.join(PROMPTS_DIR, "stage4_system.txt")
    with open(path, encoding="utf-8") as f:
        return f.read()


def parse_metadata_response(text):
    def section(name):
        pattern = rf"(?:^|\n){name}:\s*\n(.*?)(?=\n[A-Z][A-Z ]*:\s*\n|\Z)"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    title = section("TITLE")
    description = section("DESCRIPTION")
    tags = section("TAGS").replace("\n", " ").strip()
    hashtags = section("HASHTAGS").replace("\n", " ").strip()
    if not title:
        title = text.strip().splitlines()[0] if text.strip() else "Untitled"
    return title, description, tags, hashtags


def call_gemini_metadata(transcript_text):
    from google import genai
    from google.genai import types

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    client = genai.Client(api_key=api_key)
    user_msg = (
        "Generate YouTube metadata for this timestamped video transcript.\n\n"
        f"{transcript_text[:12000]}"
    )
    response = client.models.generate_content(
        model=model,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=load_stage4_prompt(),
            max_output_tokens=4096,
            temperature=0.7,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    return parse_metadata_response(response.text or "")


def fallback_metadata(mp3_name, transcript_lines):
    title = mp3_name.replace("_", " ").title()
    if transcript_lines:
        first = TIMESTAMP_RE.match(transcript_lines[0])
        hook = shorten(first.group(2), 120) if first else ""
        description = (
            f"{hook}\n\n"
            "Discover the hidden world of wildlife in this educational explainer.\n"
            "Like, comment, and subscribe for more animal stories."
        )
    else:
        description = "Educational wildlife explainer. Like, comment, and subscribe!"
    tags = (
        "animal behavior, wildlife documentary, animal facts, nature documentary, "
        "wild animals, animal survival, wildlife education"
    )
    hashtags = "#Wildlife #AnimalFacts #NatureDocumentary #WildAnimals #Education"
    return title, description, tags, hashtags


def compose_description(body, timeline, tags, hashtags):
    parts = [body.rstrip(), "", "⏱ TIMELINE"]
    parts.extend(f"{ts} {label}" for ts, label in timeline)
    if tags:
        parts.extend(["", "🏷 TAGS", tags])
    if hashtags:
        parts.extend(["", hashtags.rstrip()])
    return "\n".join(parts).strip() + "\n"


def generate_youtube_metadata(mp3_basename):
    os.makedirs(YOUTUBE_DIR, exist_ok=True)
    transcript_path = find_first_transcript()
    transcript_lines = []
    transcript_text = ""

    if transcript_path:
        with open(transcript_path, encoding="utf-8") as f:
            transcript_text = f.read()
        transcript_lines = parse_transcript_lines(transcript_text)

    timeline = build_timeline(transcript_lines) if transcript_lines else []

    try:
        meta = call_gemini_metadata(transcript_text) if transcript_text else None
    except Exception as exc:
        print(f"  Cảnh báo metadata API: {exc}")
        meta = None

    if meta:
        title, body, tags, hashtags = meta
    else:
        print("  Dùng metadata mặc định (không có API hoặc lỗi)")
        title, body, tags, hashtags = fallback_metadata(mp3_basename, transcript_lines)

    description = compose_description(body, timeline, tags, hashtags)

    title_path = os.path.join(YOUTUBE_DIR, "title.txt")
    desc_path = os.path.join(YOUTUBE_DIR, "description.txt")
    with open(title_path, "w", encoding="utf-8") as f:
        f.write(title.strip() + "\n")
    with open(desc_path, "w", encoding="utf-8") as f:
        f.write(description)

    print(f"  Title → {title_path}")
    print(f"  Description → {desc_path}")
    return title_path, desc_path
