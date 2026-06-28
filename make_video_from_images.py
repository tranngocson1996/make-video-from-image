import os
import re
import argparse
from moviepy import ImageClip, AudioFileClip, concatenate_videoclips, CompositeVideoClip
import moviepy.video.fx as vfx

SLIDE_DURATION = 0.5  # Thời gian hiệu ứng chuyển slide, theo giây

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

def main(audio_path, image_folder, output_path):
    print("Đang xử lý...")
    audio = AudioFileClip(audio_path)
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
    parser = argparse.ArgumentParser(description="Ghép ảnh và mp3 thành video có hiệu ứng slide")
    parser.add_argument('--audio', required=True, help="Path đến file mp3")
    parser.add_argument('--images', required=True, help="Path đến folder chứa ảnh")
    parser.add_argument('--output', required=True, help="Path file video đầu ra (.mp4)")
    args = parser.parse_args()
    main(args.audio, args.images, args.output)
