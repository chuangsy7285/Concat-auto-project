"""Pick a musically active section and export a temporary soundtrack."""
import json, math, shutil, subprocess
from pathlib import Path


def music_duration(path):
    if not shutil.which('ffprobe'):
        raise RuntimeError('需要 ffprobe.exe（與 FFmpeg 同一套件）來偵測音樂長度')
    p = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                        '-of','default=noprint_wrappers=1:nokey=1',str(path)],
                       capture_output=True,text=True)
    if p.returncode or not p.stdout.strip():
        raise RuntimeError('無法讀取音樂長度：'+p.stderr[-500:])
    return float(p.stdout.strip())


def choose_music_start(path, clip_seconds):
    """Score possible music windows by onset strength and rhythmic regularity."""
    import numpy as np
    from scipy.signal import find_peaks
    full=music_duration(path)
    if full <= clip_seconds+0.25:
        return 0.0,full
    sr=4000
    cmd=['ffmpeg','-nostdin','-v','error','-i',str(path),'-ac','1','-ar',str(sr),
         '-f','f32le','pipe:1']
    p=subprocess.run(cmd,capture_output=True)
    if p.returncode:raise RuntimeError('音樂分析失敗：'+p.stderr.decode(errors='replace')[-700:])
    samples=np.frombuffer(p.stdout,dtype='<f4')
    hop=400; size=800
    if len(samples)<size:return 0.0,full
    # 0.1s envelope, emphasize sudden attacks over sustained loud passages.
    n=(len(samples)-size)//hop+1
    rms=np.empty(n,dtype=np.float32)
    for i in range(n):
        chunk=samples[i*hop:i*hop+size]
        rms[i]=np.sqrt(np.mean(chunk*chunk)+1e-10)
    attacks=np.maximum(0,np.diff(rms,prepend=rms[0]))
    if len(attacks)>3:
        attacks=np.convolve(attacks,np.array([.25,.5,.25]),mode='same')
    peaks,_=find_peaks(attacks,distance=3,prominence=max(float(np.percentile(attacks,65))*.4,1e-5))
    window=int(clip_seconds*sr/hop)
    if window>=len(rms):return 0.0,full
    step=max(1,int(1.0*sr/hop))
    candidates=list(range(0,len(rms)-window,step))
    candidates.append(len(rms)-window)
    best=(-1e30,0)
    for i in candidates:
        j=i+window
        subset=peaks[(peaks>=i)&(peaks<j)]
        if len(subset)<2:regularity=0.0
        else:
            gaps=np.diff(subset)
            regularity=1/(1+float(np.std(gaps)/(np.mean(gaps)+1e-6)))
        strength=float(attacks[i:j].sum())/max(1,window)
        loudness=float(np.mean(rms[i:j]))
        # Reward active sections, strong onsets, steady beats; slight intro avoidance.
        score=strength*8+loudness*.2+regularity*.03
        if score>best[0]:best=(score,i)
    return round(best[1]*hop/sr,3),full


def extract_music(path,start,duration,out_dir):
    """Decode selected song to PCM WAV and remove any leading near-silence.

    AAC encoder priming can cause a perceived delayed start. PCM avoids that
    second encode. Trim silence *before* beat analysis so all timecodes match.
    """
    out_dir=Path(out_dir); out_dir.mkdir(parents=True,exist_ok=True)
    output=out_dir/'selected_music.wav'
    # 0.5s safety margin; silence is removed before final audio is padded.
    # A short 20ms ramp prevents clicks, without a perceptible fade-in.
    filters=(
        'silenceremove=start_periods=1:start_duration=0.04:'
        'start_threshold=-42dB:start_silence=0,'
        'asetpts=PTS-STARTPTS,'
        'apad,'
        f'atrim=duration={duration+0.5:.6f},'
        'asetpts=PTS-STARTPTS'
    )
    cmd=['ffmpeg','-y','-nostdin','-v','error','-ss',str(start),'-i',str(path),
         '-vn','-af',filters,'-ar','48000','-ac','2',
         '-c:a','pcm_s16le',str(output)]
    p=subprocess.run(cmd,capture_output=True)
    if p.returncode or not output.exists() or output.stat().st_size<1024:
        raise RuntimeError('擷取音樂失敗：'+p.stderr.decode(errors='replace')[-900:])
    return output
