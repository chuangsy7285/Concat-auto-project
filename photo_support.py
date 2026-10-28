"""Create motion-video intermediates from still images for Concat's media API."""
import hashlib
import random
import shutil
import subprocess
from pathlib import Path

PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.heic', '.heif'}
VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}


def prepare_photos(input_dir, seconds=4.5, width=1920, height=1080, fps=30, effect_mode="random"):
    """Render still photos with a selected effect, or deterministic random effects."""
    allowed = {"random", "none", "zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up"}
    if effect_mode not in allowed:
        raise ValueError(f"不支援的照片效果：{effect_mode}")
    root = Path(input_dir)
    photos = sorted((p for p in root.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXT), key=lambda p: p.name.casefold())
    if not photos:
        return []
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('照片轉動態影片需要 ffmpeg.exe，請先設定 FFmpeg PATH')
    out_dir = root / '_photo_generated'
    out_dir.mkdir(exist_ok=True)
    result = []
    frames = int(seconds * fps)
    heic_photos = [p for p in photos if p.suffix.lower() in ('.heic', '.heif')]
    if heic_photos:
        try:
            import pillow_heif
            from PIL import Image, ImageOps
            pillow_heif.register_heif_opener()
        except ImportError as exc:
            raise RuntimeError('找到 HEIC/HEIF 照片，但缺少解碼套件。請執行 install.bat 安裝 pillow-heif 和 Pillow。') from exc
        converted_dir = out_dir / '_heic_converted'
        converted_dir.mkdir(exist_ok=True)
    for idx, photo in enumerate(photos):
        key = hashlib.sha1((str(photo.resolve()) + str(photo.stat().st_mtime_ns) + str(photo.stat().st_size) + str(width) + str(height) + str(fps) + str(seconds)).encode('utf-8')).hexdigest()[:14]
        effects = ['zoom_in', 'zoom_out', 'pan_right', 'pan_left', 'pan_up']
        chosen = effects[random.Random(int(hashlib.sha1((photo.name+str(photo.stat().st_size)).encode('utf-8')).hexdigest()[:12], 16)).randrange(5)] if effect_mode == 'random' else effect_mode
        effect_index = effects.index(chosen) if chosen != 'none' else None
        dest = out_dir / f'photo_{idx+1:03d}_{chosen}_{key}.mp4' 
        if not dest.exists() or dest.stat().st_size < 2048:
            image_input = photo
            if photo.suffix.lower() in ('.heic', '.heif'):
                converted = converted_dir / f'{photo.stem}_{key}.png'
                if not converted.exists() or converted.stat().st_size < 2048:
                    print(f'[HEIC] 解碼 {photo.name} -> PNG', flush=True)
                    try:
                        with Image.open(photo) as im:
                            im = ImageOps.exif_transpose(im)
                            # Preserve transparency for conversion; FFmpeg handles PNG input.
                            if im.mode not in ('RGB', 'RGBA'):
                                im = im.convert('RGB')
                            im.save(converted, format='PNG')
                    except Exception as exc:
                        converted.unlink(missing_ok=True)
                        raise RuntimeError(f'HEIC 照片 {photo.name} 解碼失敗：{exc}') from exc
                image_input = converted
            print(f'[照片] 轉換 {photo.name} -> 動態片段 ({seconds}s)', flush=True)
            # Five motion variants: zoom-in, zoom-out, pan-left, pan-right, pan-up.
            # Expressions depend on output frame counter 'on', not source timestamp.
            variants = [
                ("min(1.0+on*0.0008,1.13)", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"),
                ("max(1.13-on*0.0008,1.0)", "iw/2-iw/zoom/2", "ih/2-ih/zoom/2"),
                ("1.14", "(iw-iw/zoom)*on/({n}-1)".format(n=max(2,frames)), "ih/2-ih/zoom/2"),
                ("1.14", "(iw-iw/zoom)*(1-on/({n}-1))".format(n=max(2,frames)), "ih/2-ih/zoom/2"),
                ("1.14", "iw/2-iw/zoom/2", "(ih-ih/zoom)*on/({n}-1)".format(n=max(2,frames))),
            ]
            if effect_index is None:
                # Static photo: no pan or zoom, but still normalize to project frame size.
                filt = (f'scale={width}:{height}:force_original_aspect_ratio=increase,'
                        f'crop={width}:{height},fps={fps},format=yuv420p')
            else:
                z, x, y = variants[effect_index]
                filt = (f'scale={width*2}:{height*2}:force_original_aspect_ratio=increase,'
                        f'crop={width*2}:{height*2},'
                        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
                        'format=yuv420p')
            print(f'[照片效果] {photo.name}: {chosen}')
            cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-loop', '1', '-i', str(image_input),
                   '-vf', filt, '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-preset', 'veryfast',
                   '-crf', '19', '-movflags', '+faststart', str(dest)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode or not dest.exists() or dest.stat().st_size < 2048:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f'照片 {photo.name} 轉換失敗：{proc.stderr[-1500:]}')
        result.append(dest)
    print(f'[照片] 已準備 {len(result)} 張照片的動態片段')
    return result
