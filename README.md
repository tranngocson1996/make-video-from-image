# make-video-from-image

Toolchain tự động hóa video YouTube kênh doodle động vật: tạo image prompts từ transcript, ghép ảnh + audio thành video, xuất metadata YouTube.

## Cấu trúc thư mục

```
make-video-from-image/
├── transcript/          # Transcript có timestamp (file .txt đầu tiên được dùng)
├── image_prompts/       # File prompt tạo ảnh (output của generate_image_prompts.py)
├── images/              # Ảnh đã generate (từ Google Flow / extension)
├── mp3/                 # File voiceover (.mp3)
├── youtube/             # Video .mp4 + title.txt + description.txt
├── prompts/             # System prompt Stage 3, Stage 4, prompt gốc kênh
├── generate_image_prompts.py
├── make_video_from_images.py
├── youtube_metadata.py
├── config.py
├── requirements.txt
└── .env                 # API keys (tạo từ .env.example)
```

## Cài đặt

```bash
pip install -r requirements.txt
cp .env.example .env
```

Chỉnh `.env`:

```env
GOOGLE_API_KEY=...          # https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash
```

## Workflow đầy đủ

```
Claude (Stage 1–2)     → kịch bản + voiceover ElevenLabs
       ↓
transcript/            → file .txt có timestamp
       ↓
generate_image_prompts → image_prompts/*.txt
       ↓
Google Flow + extension → images/*.jpeg
       ↓
mp3/                   → file audio
       ↓
make_video_from_images → youtube/*.mp4 + title + description
```

---

## Bước 1 — Kịch bản & voiceover (Claude + ElevenLabs)

Làm trên Claude chat theo prompt kênh (`prompts/channel_prompt_full.txt`):

1. Chọn 1 trong 5 chủ đề (Stage 1)
2. Lấy kịch bản narration (Stage 2)
3. Paste vào ElevenLabs → xuất file `.mp3` vào folder `mp3/`
4. Tạo transcript có timestamp (Descript, Otter, Premiere, YouTube captions…) → lưu vào `transcript/`

**Format transcript** (hỗ trợ `[0:00]` và `[00:00]`):

```
[0:00] You weigh less than a house cat.
[0:02] The rainforest around you is completely black.
[1:13] Your eyes are large, round, and packed with light-sensitive cells.
```

Chỉ cần bỏ **file `.txt` đầu tiên** (theo tên) vào `transcript/`.

---

## Bước 2 — Tạo image prompts (tự động)

```bash
python3 generate_image_prompts.py
```

Script sẽ:

- Đọc file `.txt` đầu tiên trong `transcript/`
- Gọi **Gemini API** (free tier text), chia batch 20 timestamp/lần
- Tự retry nếu thiếu/sai timestamp
- Lưu checkpoint nếu bị gián đoạn
- Xuất `image_prompts/image_prompts_<tên-file>.txt`

**Tuỳ chọn:**

```bash
python3 generate_image_prompts.py --fresh          # chạy lại từ đầu, bỏ checkpoint
python3 generate_image_prompts.py --batch-size 10  # batch nhỏ hơn nếu bị rate limit
python3 generate_image_prompts.py --transcript transcript/my.txt
```

**Format file output** (mỗi prompt một dòng, cách nhau một dòng trống):

```
[0:00] Hand-drawn 2D doodle cartoon animation, flat solid colors, ...

[0:02] Hand-drawn 2D doodle cartoon animation, flat solid colors, ...
```

---

## Bước 3 — Tạo ảnh (Google Flow + extension)

Google Flow **không có public API**. Dùng extension **h2dev_flow** trên Chrome:

1. Mở project tại [Google Flow](https://labs.google/fx/tools/flow)
2. Load file `image_prompts/*.txt` vào extension (mỗi dòng = 1 prompt)
3. Chạy batch → ảnh tải về, copy vào folder `images/`

**Quy ước tên ảnh** (bắt buộc để ghép video đúng thời điểm):

```
[000]-Hand-drawn-2D-doodle-cartoon-animation,-flat.jpeg
[027]-Hand-drawn-2D-doodle-cartoon-animation,-flat.jpeg
[107]-Hand-drawn-2D-doodle-cartoon-animation,-flat.jpeg
```

- Số trong `[]` = timestamp dạng **MMSS**: `0:27` → `[027]`, `1:07` → `[107]`, `5:27` → `[527]`
- Luôn **3 chữ số** (zero-pad) nếu < 1000
- Extension thường tự đặt tên đúng format này

> **Lưu ý:** API Ideogram/Gemini Image có thể dùng thay Flow nhưng thường **cần trả phí** hoặc quota rất ít — không phù hợp ~100+ ảnh/video free.

---

## Bước 4 — Ghép video + metadata YouTube

Bỏ file `.mp3` vào `mp3/`, đảm bảo ảnh đã có trong `images/`, rồi chạy:

```bash
python3 make_video_from_images.py
```

Script sẽ:

- Lấy **file mp3 đầu tiên** trong `mp3/`
- Lấy ảnh từ `images/`, sync theo số trong `[xxx]` trên tên file
- Tăng âm lượng audio **x2**
- Xuất video vào `youtube/<tên-mp3>.mp4`
- Tạo `youtube/title.txt` và `youtube/description.txt` (Gemini + timeline từ transcript)

**Output trong `youtube/`:**

| File | Nội dung |
|------|----------|
| `*.mp4` | Video hoàn chỉnh |
| `title.txt` | Tiêu đề YouTube |
| `description.txt` | Mô tả + timeline chapters + tags + hashtags |

**Tuỳ chọn:**

```bash
python3 make_video_from_images.py --audio mp3/voice.mp3
python3 make_video_from_images.py --images images/
python3 make_video_from_images.py --output youtube/custom.mp4
```

---

## Quy tắc đặt tên ảnh & thời gian

| Timestamp transcript | Số trong tên file | Thời điểm video |
|---------------------|-------------------|-----------------|
| `[0:06]` | `[006]` hoặc `[6]` | 6 giây |
| `[1:03]` | `[103]` | 1 phút 3 giây |
| `[5:27]` | `[527]` | 5 phút 27 giây |

Script tự xóa ký tự thừa **trước** dấu `[` trong tên ảnh (nếu có).

---

## Xử lý lỗi thường gặp

### Gemini API — `limit: 0` hoặc `gemini-2.0-flash`

Model `gemini-2.0-flash` đã ngừng. Trong `.env` dùng:

```env
GEMINI_MODEL=gemini-2.5-flash
```

### Gemini — `429 RESOURCE_EXHAUSTED`

Free tier bị rate limit. Chờ 1–2 phút rồi chạy lại (script có checkpoint). Hoặc giảm `--batch-size 10`.

### Transcript không parse được

Đảm bảo mỗi dòng bắt đầu bằng `[phút:giây]` hoặc `[giờ:phút:giây]`.

### Video không có ảnh / sai thời điểm

Kiểm tra tên file ảnh có `[số]` khớp timestamp trong transcript.

### Metadata YouTube lỗi API

Nếu không có `GOOGLE_API_KEY`, script vẫn tạo video; metadata dùng bản mặc định + timeline từ transcript.

---

## Tóm tắt lệnh

```bash
# 1. Prompts ảnh
python3 generate_image_prompts.py

# 2. Ảnh — thủ công qua Google Flow extension → folder images/

# 3. Video + YouTube metadata
python3 make_video_from_images.py
```
