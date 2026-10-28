"""Offline OpenCV-based travel highlight ranking. No cloud API."""
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}


def natural(name):
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r'(\d+)', name)]


@dataclass
class Candidate:
    filename: str
    start: float
    end: float
    score: float
    file_index: int
    reason: str = ''
    fingerprint: int = -1


def analyse_video(path, index, cv2, np, sample_interval=1.0):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        print(f'警告：無法開啟 {path.name}，跳過')
        return []
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if fps > 0 else 0
    if duration < 1.5:
        cap.release()
        print(f'警告：{path.name} 太短或無法讀取時長，跳過')
        return []
    metrics = []
    fingerprints = []
    previous = None
    for i in range(math.ceil(duration / sample_interval)):
        t = min(duration - 0.05, i * sample_interval + 0.25)
        if t < 0: continue
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok: continue
        h, w = frame.shape[:2]
        scale = min(1.0, 320 / max(w, h))
        if scale < 1:
            frame = cv2.resize(frame, (max(1, int(w*scale)), max(1, int(h*scale))))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # 64-bit perceptual difference hash, robust to modest compression/resize.
        small = cv2.resize(gray, (9, 8))
        bits = (small[:, 1:] > small[:, :-1]).reshape(-1)
        phash = sum(int(bit) << j for j, bit in enumerate(bits))
        fingerprints.append((t, phash))
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        exposure = max(0., 1. - abs(brightness-125.)/125.)
        sharpness = min(1., math.log1p(sharp)/math.log1p(450.))
        motion = 0.
        if previous is not None:
            resized = cv2.resize(gray, (160, 90))
            motion = float(cv2.absdiff(previous, resized).mean()) / 50.
            motion = min(1., motion)
        else:
            resized = cv2.resize(gray, (160, 90))
        previous = resized
        # Moderate movement preferred over camera shake; sharpness matters more.
        motion_good = max(0., 1. - abs(motion - .35)/.75)
        # Penalize flat/washed-out imagery and violent frame-to-frame motion.
        contrast = min(1., float(gray.std()) / 52.)
        extreme_motion = max(0., (motion - .72) / .28)
        score = .38 * sharpness + .25 * exposure + .18 * motion_good + .19 * contrast - .12 * extreme_motion
        metrics.append((t, score, motion))
    cap.release()
    if not metrics: return []
    results = []
    # 4-8s windows, stride 4s; prevent selecting exact same material repeatedly.
    for start in range(0, max(1, math.ceil(duration - 3)), 4):
        end = min(duration, start + 6.)
        if end-start < 3.9: continue
        window = [x for x in metrics if start <= x[0] < end]
        if not window: continue
        scores = [x[1] for x in window]
        # Penalize unstable scores; modest bonus for scene variation.
        score = sum(scores)/len(scores) - .12 * float(np.std(scores))
        variation = min(.06, float(np.std([x[2] for x in window])) * .10)
        score += variation
        hashes = [h for t, h in fingerprints if start <= t < end]
        representative = hashes[len(hashes)//2] if hashes else -1
        results.append(Candidate(path.name, float(start), round(end, 3), score, index,
                                 f'畫質/曝光/動態/對比評分 {score:.3f}', representative))
    print(f'分析 {path.name}: {duration:.1f}s，候選 {len(results)} 段')
    return results


def select_highlights(input_dir, csv_path, target=180):
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise RuntimeError('缺少 OpenCV 或 NumPy。請先執行 install.bat') from e
    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT],
                   key=lambda p: natural(p.name))
    if not files: raise RuntimeError('input 資料夾內沒有支援的影片')
    all_candidates = []
    for i, p in enumerate(files):
        all_candidates.extend(analyse_video(p, i, cv2, np))
    if not all_candidates: raise RuntimeError('沒有可用的 4–8 秒候選片段')
    # Pass 1: take the best shot per source for variety; pass 2: fill to target.
    by_file = {}
    for c in all_candidates:
        if c.filename not in by_file or c.score > by_file[c.filename].score:
            by_file[c.filename] = c
    selected = []
    used = set()
    total = 0.
    def add(c):
        nonlocal total
        if any(x.filename == c.filename and not (c.end <= x.start or c.start >= x.end) for x in selected):
            return False
        remaining = target-total
        if remaining < 4.: return False
        length = min(c.end-c.start, remaining)
        if length < 4.: return False
        selected.append(Candidate(c.filename, c.start, round(c.start+length,3), c.score, c.file_index,c.reason))
        total += length
        return True
    for c in sorted(by_file.values(), key=lambda x: x.score, reverse=True):
        if total >= target-0.01: break
        add(c)
    for c in sorted(all_candidates, key=lambda x: x.score, reverse=True):
        if total >= target-0.01: break
        add(c)
    selected.sort(key=lambda x: (x.file_index,x.start))
    with csv_path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['filename','start','end','score','reason'])
        for c in selected:
            writer.writerow([c.filename, f'{c.start:.3f}', f'{c.end:.3f}',f'{c.score:.3f}',c.reason])
    print(f'已產生 {csv_path}：{len(selected)} 段，合計 {total:.1f} 秒（目標 {target}s）')
    if total < target-1:
        print('提醒：素材不足或無足夠不重疊片段，成品可能短於 180 秒。')
    return selected
