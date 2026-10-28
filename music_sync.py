"""Offline music beat analysis and 4–8s video edit decisions. FFmpeg is required."""
import csv, subprocess, shutil, math
from pathlib import Path


def analyse_music(path, beats_csv, target=60):
    import numpy as np
    from scipy.signal import find_peaks
    if not shutil.which('ffmpeg'):
        raise RuntimeError('找不到 ffmpeg.exe。請安裝 FFmpeg 並加入 PATH（音樂 MP3/WAV 解碼需要）。')
    sr = 11025
    cmd = ['ffmpeg','-nostdin','-v','error','-i',str(path),'-t',str(target+1),'-ac','1','-ar',str(sr),'-f','f32le','pipe:1']
    p = subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode: raise RuntimeError('無法解碼音樂：'+p.stderr.decode(errors='replace')[-1200:])
    audio=np.frombuffer(p.stdout,dtype='<f4')
    if len(audio)<sr*2:
        raise RuntimeError('音樂不足2秒，請換一首音樂')
    hop=256; nfft=1024
    frames=[]
    for i in range(0,len(audio)-nfft,hop):
        window=audio[i:i+nfft]*np.hanning(nfft)
        frames.append(np.abs(np.fft.rfft(window)))
    spec=np.asarray(frames,dtype=np.float32)
    flux=np.maximum(0,np.diff(spec,axis=0)).sum(axis=1)
    flux=np.r_[0,flux]
    # Smooth and normalize to tolerate quiet intros and varying mastering.
    smooth=np.convolve(flux,np.ones(5)/5,'same')
    threshold=float(np.median(smooth)+0.6*np.std(smooth))
    peaks,_=find_peaks(smooth,distance=int(.28*sr/hop),prominence=max(threshold*.3,1e-8))
    events=[(round(float(k*hop/sr),3),round(float(smooth[k]),3)) for k in peaks if .1<k*hop/sr<target-.1]
    if events:
        values=np.asarray([x[1] for x in events]); scale=float(max(np.percentile(values,90),1e-9))
        events=[(t,round(min(1.,v/scale),3)) for t,v in events]
    with open(beats_csv,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['time_sec','strength','type'])
        for t,v in events:w.writerow([f'{t:.3f}',f'{v:.3f}','onset'])
    print(f'[音樂] {path.name}：找到 {len(events)} 個節奏候選點；{beats_csv}')
    return events


def make_cuts(candidates, beats, csv_path, target=60, video_mode='auto', video_seconds=5.0):
    """Prefer one clip per original source, photo 1-4s, video flexible; never reuse intervals."""
    if not candidates:
        raise RuntimeError('沒有可用的影片或照片候選片段')
    import random
    rng = random.Random(345)
    beats = sorted((float(t), float(strength)) for t, strength in beats)
    groups = {}
    for c in candidates:
        groups.setdefault(c.filename, []).append(c)
    # Start with one representative of each source; prevents the best-scoring video
    # from consuming the whole timeline before the rest get a chance.
    source_names = list(groups)
    rng.shuffle(source_names)
    source_names.sort(key=lambda name: max(c.score for c in groups[name]), reverse=True)
    rows, used, used_photos, recent_hashes = [], [], set(), []
    pos, last_file = 0., None
    covered = set()
    while pos < target - 0.05:
        remaining = target - pos
        options = []
        uncovered = [name for name in source_names if name not in covered]
        for c in candidates:
            filename = c.filename.replace('\\', '/')
            is_photo = filename.startswith('_photo_generated/')
            if is_photo and filename in used_photos:
                continue
            lo, hi = (1.0, 4.0) if is_photo else (1.0, 30.0)
            if not is_photo:
                if video_mode == 'fixed':
                    lo = max(1.0, video_seconds - 0.5)
                    hi = video_seconds
                else:
                    # Reserve budget for as many remaining sources as possible.
                    remaining_sources = max(1, len(uncovered))
                    share = remaining / remaining_sources
                    hi = min(12.0, max(1.0, share))
                    lo = 1.0
            gaps = [(float(c.start), float(c.end))]
            for f, u0, u1 in used:
                if f != c.filename: continue
                new_gaps = []
                for g0, g1 in gaps:
                    if u1 <= g0+1e-6 or u0 >= g1-1e-6:
                        new_gaps.append((g0,g1))
                    else:
                        if u0-g0 >= lo-0.02: new_gaps.append((g0,u0))
                        if g1-u1 >= lo-0.02: new_gaps.append((u1,g1))
                gaps = new_gaps
            for g0, g1 in gaps:
                available = min(g1-g0, hi, remaining)
                if available < lo-0.02: continue
                beat_options = [(t,strength) for t,strength in beats
                                if pos+lo-0.002 <= t <= pos+available+0.002]
                preferred = min(2.5, available) if is_photo else min(video_seconds if video_mode=='fixed' else 5.0,available)
                if beat_options:
                    t,strength = max(beat_options,key=lambda x: .7*x[1]-.10*abs(x[0]-pos-preferred))
                    length = min(available,max(lo,t-pos)); aligned=True
                else:
                    length = min(available,max(lo,preferred));strength=0.;aligned=False
                similar = (c.fingerprint >= 0 and any((c.fingerprint^h).bit_count()<=8 for h in recent_hashes[-4:]))
                # Unseen source gets priority, regardless of quality differences.
                priority = 2.0 if c.filename not in covered else 0.0
                value = priority + c.score + (0.12 if is_photo else 0.) + .16*strength + (.1 if aligned else 0.) + rng.random()*.005
                value -= .3 if c.filename==last_file else 0.
                value -= .12 if similar else 0.
                options.append((value,c,g0,length,similar,aligned,is_photo))
        if not options:break
        _,c,start,length,similar,aligned,is_photo=max(options,key=lambda x:x[0])
        end=start+length
        rows.append({'filename':c.filename,'start':round(start,3),'end':round(end,3),
                     'timeline_start':round(pos,3),'timeline_end':round(pos+length,3),
                     'score':round(c.score,4),
                     'visual_hash':format(c.fingerprint,'016x') if c.fingerprint>=0 else '',
                     'similar_to_recent':int(similar),
                     'reason':('照片／' if is_photo else '影片／')+('強節拍切點' if aligned else '自然切點')})
        used.append((c.filename,start,end))
        covered.add(c.filename)
        if is_photo:used_photos.add(c.filename)
        if c.fingerprint>=0:recent_hashes.append(c.fingerprint)
        last_file=c.filename;pos+=length
    if not rows:raise RuntimeError('沒有可用片段（至少1秒）')
    with open(csv_path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    photo_count=sum(r['filename'].replace('\\','/').startswith('_photo_generated/') for r in rows)
    print(f'[節奏剪輯] {len(rows)} 段；照片 {photo_count}；涵蓋 {len(covered)}/{len(groups)} 個來源；總長 {pos:.3f}s / {target}s')
    if len(covered)<len(groups):print(f'[提醒] 受 {target}s 上限限制，有 {len(groups)-len(covered)} 個來源未選入。')
    if pos<target-.05:print(f'[提醒] 實際長度 {pos:.2f}s，不補足。')
    return rows

def unique_material_duration(candidates):
    """Auto timeline budget: each photo at most 4s; video intervals counted once."""
    grouped = {}
    for c in candidates:
        grouped.setdefault(c.filename, []).append((float(c.start), float(c.end)))
    total = 0.0
    for filename, intervals in grouped.items():
        if filename.replace('\\', '/').startswith('_photo_generated/'):
            total += min(4.0, max((b-a for a,b in intervals), default=0.0))
            continue
        merged = []
        for a,b in sorted(intervals):
            if b <= a: continue
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a,b))
        total += sum(b-a for a,b in merged)
    return round(total, 3)


def auto_generate(input_dir,music_path,beats_csv,cuts_csv,target=60,video_mode="auto",video_seconds=5.0,photo_effect="random"):
    from highlight_selector import analyse_video,VIDEO_EXT,natural
    from photo_support import prepare_photos
    import cv2,numpy as np
    files=sorted((p for p in Path(input_dir).iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT),key=lambda p:natural(p.name))
    photos=prepare_photos(input_dir, effect_mode=photo_effect)
    print(f'[素材處理] 原始影片 {len(files)} 支；已轉換照片 {len(photos)} 張')
    files += photos
    if not files:raise RuntimeError('input 資料夾沒有影片或照片')
    candidates=[]
    for i,p in enumerate(files):
        shots=analyse_video(p,i,cv2,np)
        if p.parent.name=='_photo_generated':
            for shot in shots:
                shot.filename='_photo_generated/'+p.name
                shot.reason='照片 Ken Burns 動態效果 / '+shot.reason
        candidates.extend(shots)
    photo_candidates = sum(1 for c in candidates if c.filename.startswith('_photo_generated/'))
    print(f'[候選分析] 總候選 {len(candidates)} 段；照片候選 {photo_candidates} 段')
    if photos and not photo_candidates:
        print('[警告] 照片已轉換，但 OpenCV 未產生可用照片候選，請檢查照片轉換檔。')
    if target is None:
        target = unique_material_duration(candidates)
        print(f'[總長度] 自動模式：可用不重複素材約 {target:.2f} 秒（不受 60 秒限制）')
        if target < 1.0:
            raise RuntimeError('可用素材不足 1 秒')
    beats=analyse_music(music_path,beats_csv,target)
    return make_cuts(candidates,beats,cuts_csv,target,video_mode=video_mode,video_seconds=video_seconds)
