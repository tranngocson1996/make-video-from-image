import os
import re
import argparse
from moviepy import ImageClip, AudioFileClip, CompositeVideoClip

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BASE_DIR, "images")
MP3_DIR = os.path.join(BASE_DIR, "mp3")
MP4_DIR = os.path.join(BASE_DIR, "mp4")

def rename_images(image_folder):
    """Xóa các ký tự trước dấu '[' trong tên file ảnh."""
    for fname in os.listdir(image_folder):
        fpath = os.path.join(image_folder, fname)
        if not os.path.isfile(fpath):
            continue
        bracket_pos = fname.find('[')
        if bracket_pos > 0:
            new_fname = fname[bracket_pos:]
            new_fpath = os.path.join(image_folder, new_fname)
            if not os.path.exists(new_fpath):
                os.rename(fpath, new_fpath)

def mmss_to_seconds(value):
    """Chuyển số dạng MMSS hoặc SS thành giây.
    Ví dụ: 6 -> 6s, 103 -> 63s (1p3s), 330 -> 210s (3p30s)
    """
    if value < 100:
        return value  # Chỉ là giây
    minutes = value // 100
    seconds = value % 100
    return minutes * 60 + seconds

def parse_image_infos(image_folder):
    pattern = re.compile(r'\[(\d+)\]')
    image_files = sorted([
        f for f in os.listdir(image_folder)
        if os.path.isfile(os.path.join(image_folder, f)) and pattern.search(f)
    ])
    images_with_time = []
    for f in image_files:
        match = pattern.search(f)
        if match:
            raw = int(match.group(1))
            start_sec = mmss_to_seconds(raw)
            images_with_time.append((start_sec, f))
    return images_with_time

def build_clips_with_slide(images_with_time, image_folder, video_duration, video_size):
    clips = []
    n = len(images_with_time)

    for i, (start_sec, fname) in enumerate(images_with_time):
        end_sec = (
            images_with_time[i+1][0]
            if i + 1 < n
            else video_duration
        )
        duration = end_sec - start_sec

        if duration <= 0:
            continue

        path = os.path.join(image_folder, fname)

        # Tạo clip ảnh tại đúng thời gian start_sec tuyệt đối, bỏ hoàn toàn slide
        clip = (ImageClip(path)
                .with_start(start_sec)
                .with_duration(duration)
                .resized(video_size))

        clips.append(clip)

    return clips

def ensure_dirs():
    for folder in (IMAGES_DIR, MP3_DIR, MP4_DIR):
        os.makedirs(folder, exist_ok=True)

def find_first_mp3(mp3_dir):
    mp3_files = sorted(
        f for f in os.listdir(mp3_dir)
        if os.path.isfile(os.path.join(mp3_dir, f)) and f.lower().endswith(".mp3")
    )
    if not mp3_files:
        return None
    return os.path.join(mp3_dir, mp3_files[0])

def resolve_paths(audio_path=None, image_folder=None, output_path=None):
    ensure_dirs()
    image_folder = image_folder or IMAGES_DIR
    if audio_path is None:
        audio_path = find_first_mp3(MP3_DIR)
        if not audio_path:
            raise FileNotFoundError("Không tìm thấy file .mp3 nào trong folder mp3/")
    if output_path is None:
        mp3_name = os.path.splitext(os.path.basename(audio_path))[0]
        output_path = os.path.join(MP4_DIR, f"{mp3_name}.mp4")
    return audio_path, image_folder, output_path

def main(audio_path, image_folder, output_path):
    print("Đang xử lý...")
    audio = AudioFileClip(audio_path).with_volume_scaled(2)
    video_duration = audio.duration

    # Rename ảnh: xóa ký tự trước dấu '[' trong tên file
    rename_images(image_folder)

    images_with_time = parse_image_infos(image_folder)
    if not images_with_time:
        print("Không tìm thấy file ảnh hợp lệ!")
        return

    # Lấy kích thước frame chuẩn (từ ảnh đầu tiên)
    first_img = os.path.join(image_folder, images_with_time[0][1])
    # w, h
    size = ImageClip(first_img).size

    clips = build_clips_with_slide(images_with_time, image_folder, video_duration, size)

    # Dùng CompositeVideoClip để đặt các clip ở đúng thời điểm tuyệt đối thay vì concatenate
    video = CompositeVideoClip(clips, size=size)
    video = video.with_audio(audio)
    video = video.with_duration(video_duration)

    video.write_videofile(output_path, fps=24, audio_codec="aac")
    print(f"Đã xuất video ra: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ghép ảnh và mp3 thành video")
    parser.add_argument("--audio", help="Path đến file mp3 (mặc định: file đầu tiên trong mp3/)")
    parser.add_argument("--images", help="Path đến folder chứa ảnh (mặc định: images/)")
    parser.add_argument("--output", help="Path file video đầu ra (mặc định: mp4/<tên-mp3>.mp4)")
    args = parser.parse_args()
    try:
        audio_path, image_folder, output_path = resolve_paths(
            args.audio, args.images, args.output
        )
    except FileNotFoundError as e:
        print(e)
        raise SystemExit(1)
    print(f"Ảnh: {image_folder}")
    print(f"MP3: {audio_path}")
    print(f"Xuất: {output_path}")
    main(audio_path, image_folder, output_path)
