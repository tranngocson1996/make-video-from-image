#!/usr/bin/env python3
"""Tự động tạo image prompts từ transcript có timestamp (Stage 3) — Gemini API."""

import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

from config import IMAGE_PROMPTS_DIR, PROMPTS_DIR, TRANSCRIPT_DIR

load_dotenv()

BATCH_SIZE = 20
DEFAULT_MODEL = "gemini-2.5-flash"
FALLBACK_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
)
DEPRECATED_MODELS = {
    "gemini-2.0-flash": "gemini-2.5-flash",
    "gemini-2.0-flash-001": "gemini-2.5-flash",
    "gemini-2.0-flash-lite": "gemini-2.5-flash-lite",
}
BATCH_DELAY_SECONDS = 6  # free tier ~10-15 RPM tùy model
TIMESTAMP_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$", re.MULTILINE)
PROMPT_LINE_RE = re.compile(
    r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s+Hand-drawn 2D doodle",
    re.IGNORECASE,
)


def ensure_dirs():
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    os.makedirs(IMAGE_PROMPTS_DIR, exist_ok=True)


def find_first_transcript(transcript_dir):
    txt_files = sorted(
        f
        for f in os.listdir(transcript_dir)
        if os.path.isfile(os.path.join(transcript_dir, f)) and f.lower().endswith(".txt")
    )
    if not txt_files:
        return None
    return os.path.join(transcript_dir, txt_files[0])


def load_system_prompt():
    path = os.path.join(PROMPTS_DIR, "stage3_system.txt")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Không tìm thấy system prompt: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def normalize_timestamp(ts):
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return f"{minutes:02d}:{seconds:02d}"
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return ts


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


def slug_from_filename(path):
    name = os.path.splitext(os.path.basename(path))[0]
    slug = re.sub(r"^script_", "", name, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")
    return slug or "video"


def normalize_topic_slug(topic):
    slug = re.sub(r"^image_prompts_", "", topic.strip(), flags=re.IGNORECASE)
    slug = re.sub(r"^script_", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[^a-z0-9]+", "_", slug.lower()).strip("_")
    return slug or "video"


def resolve_topic_slug(transcript_path, topic=None):
    if topic:
        return normalize_topic_slug(topic)
    env_topic = os.getenv("VIDEO_TOPIC")
    if env_topic:
        return normalize_topic_slug(env_topic)
    return slug_from_filename(transcript_path)


def fix_prompt_timestamp(prompt, transcript_line):
    """Giữ đúng format timestamp như transcript, ví dụ [3:11] không [03:11]."""
    orig_ts = TIMESTAMP_RE.match(transcript_line).group(1)
    body = re.sub(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", "", prompt)
    return f"[{orig_ts}] {body}"


def finalize_prompts(transcript_lines, prompts):
    if len(transcript_lines) != len(prompts):
        raise ValueError(
            f"Số prompt ({len(prompts)}) không khớp số timestamp ({len(transcript_lines)})"
        )
    return [fix_prompt_timestamp(prompt, line) for prompt, line in zip(prompts, transcript_lines)]


def parse_prompts_from_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    prompts = []
    for line in text.splitlines():
        line = line.strip()
        if PROMPT_LINE_RE.match(line):
            prompts.append(line)
    return prompts


def prompts_by_timestamp(prompts):
    by_ts = {}
    for prompt in prompts:
        match = PROMPT_LINE_RE.match(prompt)
        if match:
            by_ts[normalize_timestamp(match.group(1))] = prompt
    return by_ts


def align_prompts_to_batch(expected_lines, prompts):
    by_ts = prompts_by_timestamp(prompts)
    aligned = []
    missing = []
    for line in expected_lines:
        ts = normalize_timestamp(TIMESTAMP_RE.match(line).group(1))
        if ts in by_ts:
            aligned.append(by_ts[ts])
        else:
            missing.append(line)
    expected_ts = {normalize_timestamp(TIMESTAMP_RE.match(line).group(1)) for line in expected_lines}
    extras = sorted(set(by_ts) - expected_ts)
    return aligned, missing, extras


def required_timestamps_text(batch_lines):
    labels = [f"[{TIMESTAMP_RE.match(line).group(1)}]" for line in batch_lines]
    return ", ".join(labels)


def build_batch_user_message(batch_lines, batch_num, total_batches, prior_prompts, strict=False):
    parts = [
        "image prompts",
        "",
        f"Batch {batch_num} of {total_batches}. Generate one image prompt for each timestamp below.",
        "",
        f"REQUIRED TIMESTAMPS — exactly {len(batch_lines)} prompts, use ONLY these timestamps:",
        required_timestamps_text(batch_lines),
        "",
        "Do NOT invent any other timestamps. Time gaps in the transcript are normal "
        "(e.g. jump from [5:27] to [5:42] still means exactly two prompts, not extra ones in between).",
    ]
    if strict:
        parts.extend([
            "",
            "STRICT MODE: Your previous response had wrong or extra timestamps. "
            f"Return exactly {len(batch_lines)} prompts for the timestamps listed above, nothing else.",
        ])
    if prior_prompts:
        parts.extend([
            "",
            "Character consistency context — last prompts from the previous batch:",
            "",
            "\n\n".join(prior_prompts[-3:]),
        ])
    parts.extend(["", "Timestamped transcript lines for this batch:", "", *batch_lines])
    parts.extend([
        "",
        "Output ONLY the image prompts. One prompt per line. "
        "Separate prompts with exactly one blank line. No other text.",
    ])
    return "\n".join(parts)


def checkpoint_path(slug):
    return os.path.join(IMAGE_PROMPTS_DIR, f".checkpoint_{slug}.json")


def load_checkpoint(slug):
    path = checkpoint_path(slug)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_checkpoint(slug, prompts, completed_batches):
    path = checkpoint_path(slug)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"prompts": prompts, "completed_batches": completed_batches}, f, ensure_ascii=False)


def clear_checkpoint(slug):
    path = checkpoint_path(slug)
    if os.path.isfile(path):
        os.remove(path)


def is_rate_limit_error(exc):
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg


def is_quota_zero_error(exc):
    return "limit: 0" in str(exc) or "limit': 0" in str(exc)


def resolve_model_chain(requested):
    if requested in DEPRECATED_MODELS:
        replacement = DEPRECATED_MODELS[requested]
        print(
            f"⚠ Model '{requested}' đã ngừng (6/2026). "
            f"Tự chuyển sang '{replacement}'."
        )
        requested = replacement

    models = [requested]
    for model in FALLBACK_MODELS:
        if model not in models:
            models.append(model)
    return models


def quota_help_message():
    return (
        "\nGợi ý:\n"
        "  1. Đổi model trong .env: GEMINI_MODEL=gemini-2.5-flash\n"
        "  2. Nếu vẫn 'limit: 0' → vào https://aistudio.google.com → Settings → "
        "bật Billing (vẫn free trong quota, Google yêu cầu link thẻ)\n"
        "  3. Hoặc tạo API key mới tại project khác"
    )


def call_gemini(client, model, system_prompt, user_message, max_retries=3, temperature=0.7):
    from google.genai import types

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=8192,
                    temperature=temperature,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = response.text
            if not text or not text.strip():
                raise RuntimeError("Gemini trả về response rỗng")
            return text
        except Exception as exc:
            last_error = exc
            if is_quota_zero_error(exc):
                raise RuntimeError(f"Model '{model}' không có quota free (limit: 0).{quota_help_message()}") from exc
            if attempt < max_retries and is_rate_limit_error(exc):
                wait = 20
                print(f"  Rate limit (lần {attempt}). Chờ {wait}s...")
                time.sleep(wait)
            elif attempt < max_retries:
                wait = 2 ** attempt
                print(f"  API lỗi (lần {attempt}): {exc}. Thử lại sau {wait}s...")
                time.sleep(wait)
    raise RuntimeError(f"Gọi API thất bại sau {max_retries} lần: {last_error}")


def call_gemini_with_fallback(client, models, system_prompt, user_message, temperature=0.7):
    last_error = None
    for model in models:
        try:
            print(f"  Dùng model: {model}")
            return call_gemini(client, model, system_prompt, user_message, temperature=temperature), model
        except RuntimeError as exc:
            last_error = exc
            if is_quota_zero_error(exc) or "limit: 0" in str(exc):
                print(f"  ✗ {model}: không có quota → thử model khác...")
                continue
            raise
    raise RuntimeError(f"Không model nào dùng được.{quota_help_message()}") from last_error


def validate_batch(expected_lines, prompts):
    _, missing, extras = align_prompts_to_batch(expected_lines, prompts)
    if missing or extras:
        missing_ts = [normalize_timestamp(TIMESTAMP_RE.match(line).group(1)) for line in missing]
        raise ValueError(
            f"Số lượng hoặc thứ tự timestamp không khớp.\n"
            f"  Thiếu: {missing_ts or 'không'}\n"
            f"  Thừa (bị bỏ qua): {extras or 'không'}"
        )


def fetch_batch_prompts(client, models, system_prompt, batch_lines, batch_num, total_batches, prior_prompts):
    active_model = models[0]
    pending_lines = list(batch_lines)
    collected = {}

    for attempt in range(1, 5):
        strict = attempt > 1
        temp = 0.2 if strict else 0.7
        user_message = build_batch_user_message(
            pending_lines, batch_num, total_batches, prior_prompts, strict=strict,
        )
        response_text, active_model = call_gemini_with_fallback(
            client,
            [active_model] + [m for m in models if m != active_model],
            system_prompt,
            user_message,
            temperature=temp,
        )
        parsed = parse_prompts_from_response(response_text)
        _, missing, extras = align_prompts_to_batch(pending_lines, parsed)

        for line in pending_lines:
            ts = normalize_timestamp(TIMESTAMP_RE.match(line).group(1))
            if ts in prompts_by_timestamp(parsed):
                collected[ts] = prompts_by_timestamp(parsed)[ts]

        if not missing:
            if extras:
                print(f"  Bỏ qua timestamp thừa do model tự thêm: {extras}")
            break

        print(f"  Thiếu {len(missing)} prompt (lần {attempt}) — thử lại chỉ phần thiếu...")
        pending_lines = missing
        if attempt == 4:
            missing_ts = [normalize_timestamp(TIMESTAMP_RE.match(line).group(1)) for line in missing]
            raise ValueError(f"Không tạo được prompt cho: {missing_ts}")

    return [collected[normalize_timestamp(TIMESTAMP_RE.match(line).group(1))] for line in batch_lines], active_model


def format_output_file(prompts):
    """Mỗi prompt một dòng, cách nhau đúng một dòng trống — giống image_prompts_*.txt."""
    if not prompts:
        return ""
    return "\n\n".join(prompts) + "\n"


def generate_image_prompts(transcript_path, output_path=None, model=None, batch_size=BATCH_SIZE, fresh=False, topic=None):
    try:
        from google import genai
    except ImportError:
        raise SystemExit(
            "Chưa cài google-genai. Chạy: pip install -r requirements.txt"
        )

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit(
            "Thiếu GOOGLE_API_KEY. Tạo file .env từ .env.example và điền API key.\n"
            "Lấy key miễn phí tại: https://aistudio.google.com/apikey"
        )

    model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    models = resolve_model_chain(model)
    system_prompt = load_system_prompt()

    with open(transcript_path, encoding="utf-8") as f:
        transcript_text = f.read()

    transcript_lines = parse_transcript_lines(transcript_text)
    if not transcript_lines:
        raise ValueError("Transcript không có dòng timestamp hợp lệ (ví dụ: [00:00] ...)")

    slug = resolve_topic_slug(transcript_path, topic=topic)
    if output_path is None:
        output_path = os.path.join(IMAGE_PROMPTS_DIR, f"image_prompts_{slug}.txt")

    if fresh:
        clear_checkpoint(slug)

    batches = [
        transcript_lines[i : i + batch_size]
        for i in range(0, len(transcript_lines), batch_size)
    ]
    total_batches = len(batches)

    print(f"Transcript: {transcript_path}")
    print(f"Xuất file: {output_path}")
    print(f"Tổng {len(transcript_lines)} timestamp → {total_batches} batch (mỗi batch tối đa {batch_size})")
    print(f"Model ưu tiên: {models[0]} (fallback: {', '.join(models[1:])})")

    client = genai.Client(api_key=api_key)
    checkpoint = load_checkpoint(slug)
    if checkpoint and checkpoint.get("completed_batches", 0) > 0:
        all_prompts = checkpoint["prompts"]
        start_batch = checkpoint["completed_batches"]
        print(f"Tiếp tục từ checkpoint: đã có {len(all_prompts)} prompts, batch {start_batch + 1}/{total_batches}")
    else:
        all_prompts = []
        start_batch = 0

    active_model = models[0]

    for i, batch in enumerate(batches, start=1):
        if i <= start_batch:
            continue

        first_ts = TIMESTAMP_RE.match(batch[0]).group(1)
        last_ts = TIMESTAMP_RE.match(batch[-1]).group(1)
        print(f"\nBatch {i}/{total_batches} — [{first_ts}] → [{last_ts}]...")

        batch_prompts, active_model = fetch_batch_prompts(
            client, [active_model] + [m for m in models if m != active_model],
            system_prompt, batch, i, total_batches, all_prompts,
        )
        all_prompts.extend(batch_prompts)
        save_checkpoint(slug, all_prompts, i)
        print(f"  ✓ {len(batch_prompts)} prompts")

        if i < total_batches:
            print(f"  Chờ {BATCH_DELAY_SECONDS}s (tránh vượt quota free tier)...")
            time.sleep(BATCH_DELAY_SECONDS)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    final_prompts = finalize_prompts(transcript_lines, all_prompts)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(format_output_file(final_prompts))

    clear_checkpoint(slug)

    print(f"\nĐã xuất {len(all_prompts)} prompts → {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Tạo image prompts từ transcript có timestamp (Gemini API)"
    )
    parser.add_argument(
        "--transcript",
        help="File transcript .txt (mặc định: file đầu tiên trong transcript/)",
    )
    parser.add_argument(
        "--output",
        help="File đầu ra (mặc định: image_prompts/image_prompts_<topic>.txt)",
    )
    parser.add_argument(
        "--topic",
        help="Slug tên video cho file đầu ra (vd: how_jaguars_hunt_in_total_darkness → image_prompts_how_jaguars_hunt_in_total_darkness.txt)",
    )
    parser.add_argument("--model", help=f"Gemini model (mặc định: {DEFAULT_MODEL})")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Số timestamp mỗi batch API (mặc định: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Bỏ checkpoint, chạy lại từ đầu",
    )
    args = parser.parse_args()

    ensure_dirs()
    transcript_path = args.transcript or find_first_transcript(TRANSCRIPT_DIR)
    if not transcript_path:
        print("Không tìm thấy file .txt nào trong folder transcript/")
        sys.exit(1)

    try:
        generate_image_prompts(
            transcript_path,
            output_path=args.output,
            model=args.model,
            batch_size=args.batch_size,
            fresh=args.fresh,
            topic=args.topic,
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Lỗi: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
