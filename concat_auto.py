import csv, json, socket, itertools, re, sys, traceback, logging, time, random
from datetime import datetime
from pathlib import Path
from highlight_selector import select_highlights
from music_sync import auto_generate
from music_segment import choose_music_start, extract_music, music_duration
from photo_support import PHOTO_EXT, VIDEO_EXT

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / 'input'
PROJECTS = Path.home() / 'ConcatAutoProjects'
OUTPUT = Path.home() / 'ConcatAutoExports'
TOKEN_FILE = ROOT / 'token.txt'
MUSIC = ROOT / 'music'
TARGET = 60

class Concat:
    def __init__(self, token, host='127.0.0.1', port=7420):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(None)
        self.reader = self.sock.makefile('r', encoding='utf-8')
        self.ids = itertools.count(1)
        self.events = []
        self.last_method = "auth"
        self._send({'jsonrpc':'2.0','id':0,'method':'auth','params':{'token':token}})
        self._wait(0)
    def _send(self, value):
        self.sock.sendall((json.dumps(value, ensure_ascii=False)+'\n').encode('utf-8'))
    def _read(self):
        line = self.reader.readline()
        if not line: raise ConnectionError('Concat 已關閉連線')
        return json.loads(line)
    def _wait(self, rid):
        while True:
            msg = self._read()
            if 'id' not in msg:
                self.events.append(msg)
                continue
            if msg['id'] != rid: continue
            if 'error' in msg:
                err = msg['error']
                raise RuntimeError(f"Concat API {self.last_method} 失敗: {json.dumps(err, ensure_ascii=False, default=str)}")
            if 'result' not in msg:
                raise RuntimeError(f"Concat API {self.last_method} 回應沒有 result: {msg!r}")
            return msg['result']
    def call(self, method, **params):
        self.last_method = method
        print(f'[API] {method}', flush=True)
        rid = next(self.ids)
        self._send({'jsonrpc':'2.0','id':rid,'method':method,'params':params})
        return self._wait(rid)
    def wait_job(self, job):
        while True:
            msg = self.events.pop(0) if self.events else self._read()
            if 'id' in msg: continue
            params = msg.get('params', {})
            if params.get('job') != job: continue
            if msg.get('method') == 'export.progress':
                print(f"\r輸出中: {params.get('frame',0)}/{params.get('total','?')} ({params.get('stage','')})", end='', flush=True)
            elif msg.get('method') in ('export.done', 'export.failed'):
                print()
                return msg
    def close(self):
        self.reader.close(); self.sock.close()


def verify_export(path):
    """Independent verification: actual MP4 frames, not only successful API status."""
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError(f'輸出檔不存在或太小：{path}')
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError('輸出檔存在但 OpenCV 無法解碼。可能是 CRF 0 / codec 相容性問題；請先用 VLC 或 ffprobe 檢查。')
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if count <= 0:
        cap.release()
        raise RuntimeError('輸出影片沒有可讀取的影格')
    indices = sorted(set([min(count-1, max(0,int(count*f))) for f in (0.05,0.2,0.5,0.8,0.95)]))
    brightness=[]
    for index in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES,index)
        ok,frame=cap.read()
        if ok and frame is not None:
            brightness.append(float(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY).mean()))
    cap.release()
    print(f'[驗證] {width}x{height} FPS={fps:.3f} 影格={count} 大小={path.stat().st_size/1024/1024:.1f}MB')
    print('[驗證] 抽樣平均亮度:', [round(x,1) for x in brightness])
    if not brightness:
        raise RuntimeError('MP4 無法讀取任何抽樣影格，不能宣告輸出成功')
    if max(brightness) < 4:
        raise RuntimeError('輸出 MP4 抽樣影格全黑。已確認問題在 Concat 匯出/轉場/編碼路徑，請回傳 error_log.txt 與輸出設定。')
    print('[驗證] 輸出影片包含非黑色影格；請用播放器確認畫面與音訊。')

def existing_media_id(project_view, filename):
    """Find an already imported media item by its full normalized source path."""
    project = project_view.get('project', project_view)
    items = project.get('media', [])
    if isinstance(items, dict):
        items = list(items.values())
    wanted = str(Path(filename).resolve()).casefold().replace('\\', '/')
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in ('path', 'file', 'source', 'uri'):
            source = item.get(field)
            if isinstance(source, dict):
                source = source.get('path') or source.get('file')
            if not isinstance(source, str):
                continue
            normalized = source.casefold().replace('\\', '/')
            if normalized == wanted:
                return item.get('id')
    return None

def natural(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)',s)]

def main():
    INPUT.mkdir(exist_ok=True)
    PROJECTS.mkdir(exist_ok=True)
    OUTPUT.mkdir(exist_ok=True)
    if not TOKEN_FILE.exists() or not TOKEN_FILE.read_text(encoding='utf-8-sig').strip():
        raise RuntimeError('請將 Concat Settings > Automation 複製的 Token 存入 token.txt')
    token = TOKEN_FILE.read_text(encoding='utf-8-sig').strip()
    MUSIC.mkdir(exist_ok=True)
    input_videos = sorted(x for x in INPUT.iterdir() if x.is_file() and x.suffix.lower() in VIDEO_EXT)
    input_photos = sorted(x for x in INPUT.iterdir() if x.is_file() and x.suffix.lower() in PHOTO_EXT)
    print(f'[素材偵測] 影片 {len(input_videos)} 支；照片 {len(input_photos)} 張')
    for x in input_photos: print(f'  [照片] {x.name}')
    if not input_photos:
        print('[提醒] 目前 input 根目錄未找到 JPG/JPEG/PNG/WEBP/BMP/HEIC/HEIF 照片；不搜尋子資料夾。')
    print('=== Concat V3.5.4 可選擇 MP4 匯出或僅保留專案／分離主標題／副標題／音樂軌道／副標題縮小／片尾縮短／音樂立即播放／隨機照片效果／節奏剪輯 / 4K ===')
    print('0. 僅重新匯出既有專案（不改時間軸）')
    print('1. 音樂節奏 + OpenCV 自動產生 beats.csv / cuts.csv（預設）')
    print('2. 使用既有 cuts.csv（可保留音樂混音）')
    mode=input('選擇 [0/1/2]：').strip() or '1'
    if mode not in ('0','1','2'):raise ValueError('請輸入 0、1、2')
    print('\n=== 最終輸出方式 ===')
    print('1. 儲存 Concat 專案並匯出 MP4（預設）')
    print('2. 僅儲存可編輯 Concat 專案，不匯出 MP4')
    output_choice = input('選擇 [1/2]：').strip() or '1'
    if output_choice not in ('1', '2'):
        raise ValueError('輸出方式請輸入 1 或 2')
    export_mp4 = output_choice == '1'

    music_path=None
    if mode != '0':
        music_files=sorted([x for x in MUSIC.iterdir() if x.suffix.lower() in ('.mp3','.wav','.flac','.m4a','.aac')])
        if not music_files:raise RuntimeError('請先將 MP3/WAV/FLAC/M4A 放進 music 資料夾')
        for i,x in enumerate(music_files,1):print(f'{i}. {x.name}')
        idx=int(input('選擇音樂編號 [1]：').strip() or '1')
        if idx<1 or idx>len(music_files):raise ValueError('無效的音樂編號')
        music_path=music_files[idx-1]
    title_text = ''
    subtitle_text = ''
    if mode != '0':
        print('\n=== 開場標題（前 3 秒疊加在第一段畫面，不增加片長） ===')
        title_text = input('主標題（Enter 表示不要標題）：').strip()
        if title_text:
            subtitle_text = input('副標題／旅行日期（可留空）：').strip()
    photo_effect = 'random'
    if mode == '1' and input_photos:
        print('\n=== 照片效果設定（套用至所有照片） ===')
        print('0. 無效果（靜態照片）')
        print('1. 緩慢放大')
        print('2. 緩慢縮小')
        print('3. 向右平移')
        print('4. 向左平移')
        print('5. 向上平移')
        print('6. 隨機使用上述 5 種效果（預設）')
        effect_choice = input('選擇照片效果 [6]：').strip() or '6'
        effect_map = {'0':'none','1':'zoom_in','2':'zoom_out',
                      '3':'pan_right','4':'pan_left','5':'pan_up','6':'random'}
        if effect_choice not in effect_map:
            raise ValueError('照片效果請選 0～6')
        photo_effect = effect_map[effect_choice]
        print(f'[照片效果設定] {photo_effect}')
    csvfile=ROOT/'cuts.csv'
    target_duration = TARGET
    if mode == '1':
        print('\n=== 最終影片總長度 ===')
        print('1. 自訂總長度（預設 60 秒）')
        print('2. 依素材自動調整總長度（不強制補滿）')
        duration_choice = input('選擇 [1/2]：').strip() or '1'
        if duration_choice not in ('1','2'):
            raise ValueError('請輸入 1 或 2')
        if duration_choice == '1':
            target_duration = float(input('最終影片目標總長度（秒）[60]：').strip() or '60')
            if not 1.0 <= target_duration <= 600.0:
                raise ValueError('總長度須介於 1～600 秒')
        else:
            target_duration = None
            print('[總長度] 自動依可用素材決定，不限制 60 秒，也不套用 600 秒固定上限')
        auto_generate(INPUT,music_path,ROOT/'beats.csv',csvfile,target=target_duration,
                      video_mode='auto',video_seconds=5.0,photo_effect=photo_effect)
        if input('請先檢查 beats.csv / cuts.csv。輸入 YES 才開始編輯：').strip()!='YES':
            print('已產生 CSV，但未更動 Concat 專案。');return
    if mode == '1':
        with csvfile.open(encoding='utf-8-sig',newline='') as f:
            initial_rows=[r for r in csv.DictReader(f) if r.get('filename','').strip()]
        preliminary=sum(float(r['end'])-float(r['start']) for r in initial_rows)
        if preliminary <= 0:raise RuntimeError('初次剪輯沒有可用片段')
        music_start,music_full=choose_music_start(music_path,preliminary)
        print(f'[音樂選段] 歌曲總長 {music_full:.2f}s；預選 {music_start:.2f}s 起，片長 {preliminary:.2f}s')
        if music_full < preliminary-0.1:
            raise RuntimeError(f'歌曲長 {music_full:.1f}s，不足以覆蓋影片 {preliminary:.1f}s，請選更長音樂')
        selected_music=extract_music(music_path,music_start,preliminary,ROOT/'_music_generated')
        # Analyze beats in the extracted section, not the original song's intro.
        auto_generate(INPUT,selected_music,ROOT/'beats.csv',csvfile,target=target_duration,
                      video_mode='auto',video_seconds=5.0,photo_effect=photo_effect)
        print('[音樂選段] 已依所選音樂片段重新計算節拍與 cuts.csv')
        music_path=selected_music
    elif mode == '2':
        with csvfile.open(encoding='utf-8-sig',newline='') as f:
            existing_rows=[r for r in csv.DictReader(f) if r.get('filename','').strip()]
        needed=sum(float(r['end'])-float(r['start']) for r in existing_rows)
        if needed<=0:raise RuntimeError('cuts.csv 沒有有效片段')
        start,full=choose_music_start(music_path,needed)
        if full < needed-0.1:raise RuntimeError('背景音樂比影片短，請換較長歌曲')
        print(f'[音樂選段] 模式2：{start:.2f}s 起，擷取 {needed:.2f}s；原 cuts.csv 保持不變')
        music_path=extract_music(music_path,start,needed,ROOT/'_music_generated')
    rows=[]
    if mode!='0':
        if not csvfile.is_file():raise RuntimeError('找不到 cuts.csv；請選擇模式1自動產生')
        with csvfile.open(encoding='utf-8-sig',newline='') as f:
            rows=[r for r in csv.DictReader(f) if r.get('filename','').strip()]
        if not rows:raise RuntimeError('cuts.csv 沒有有效片段')
        photo_rows = [r for r in rows if r['filename'].replace('\\','/').startswith('_photo_generated/')]
        print(f'[選片確認] cuts.csv 共 {len(rows)} 段，其中照片動態片段 {len(photo_rows)} 段')
        if input_photos and not photo_rows:
            print('[提醒] cuts.csv 沒有照片片段。模式2只讀取舊CSV；請選模式1重新分析。')
        total=sum(float(r['end'])-float(r['start']) for r in rows)
        if total <= 0 or (mode == '1' and target_duration is not None and total > target_duration+.08):raise RuntimeError(f'cuts.csv 片段總長 {total:.2f}s，超出目標總長度 {target_duration}s')
        limit_label = f'{target_duration:g}s' if target_duration is not None else '依素材自動決定（無60秒上限）'
        print(f'[剪輯] 本次預計輸出 {total:.2f}s（模式1目標 {limit_label}；素材不足不補足）')
    c=Concat(token)
    project_path = None
    try:
        print('Concat API:',c.call('version'))
        print('\n=== Concat 專案管理 V3.1 ===')
        print('1. 建立新專案（自動產生不重複名稱）')
        print('2. 開啟既有專案（將剪輯追加到原有時間軸）')
        choice = '2' if mode == '0' else input('請選擇 [1/2]：').strip()
        if choice == '1':
            base = input('新專案名稱前綴 [AutoEdit]：').strip() or 'AutoEdit'
            if not re.fullmatch(r'[\w-]+', base, re.UNICODE):
                raise ValueError('專案名稱只允許文字、數字、底線和連字號')
            name = base + '_' + datetime.now().strftime('%Y%m%d_%H%M%S')
            index = 2
            while (PROJECTS/name).exists():
                name = base + '_' + datetime.now().strftime('%Y%m%d_%H%M%S') + f'_{index}'
                index += 1
            project_path = str(PROJECTS/name)
            c.call('project.create', location=str(PROJECTS), name=name)
            position = 0.0
            print('新建專案:', project_path)
        elif choice == '2':
            projects = sorted(
                [p for p in PROJECTS.iterdir() if p.is_dir() and (p/'concat.json').is_file()],
                key=lambda p:p.name.lower()
            )
            if not projects:
                raise RuntimeError(f'找不到既有 Concat 專案（預設位置：{PROJECTS}）。請選擇 1 新建專案。')
            print('\n可開啟的專案：')
            for i, p in enumerate(projects, 1):
                print(f'{i}. {p.name}')
            selection = input('請輸入專案編號：').strip()
            if not selection.isdecimal() or not 1 <= int(selection) <= len(projects):
                raise ValueError('無效的專案編號')
            project_path = str(projects[int(selection)-1])
            print('選擇專案:', project_path)
            print('注意：此模式會將 cuts.csv 中的片段追加到既有時間軸，不會清空原有片段。')
            if mode != '0' and input('輸入 APPEND 確認修改既有專案：').strip() != 'APPEND':
                print('已取消，既有專案沒有修改。')
                return
            opened = c.call('project.open', path=project_path)
            # Read the opened project to determine the current timeline end.
            view = c.call('project.get', path=project_path)
            project = view.get('project', view)
            position = max(
                (float(clip.get('start', 0)) + float(clip.get('duration', 0))
                 for timeline in project.get('timelines', [])
                 for clip in timeline.get('clips', [])),
                default=0.0
            )
            print(f'從既有時間軸 {position:.2f} 秒處開始追加。')
        else:
            raise ValueError('請輸入 1 或 2')
        crf = 12
        if export_mp4:
            print('\n=== 輸出畫質 ===')
            print('預設：H.264 CRF 12（4K高畫質；CRF 0 可自行輸入）')
            quality = input('CRF [12]：').strip()
            crf = int(quality) if quality else 12
            if not 0 <= crf <= 51:
                raise ValueError('CRF 必須介於 0 到 51')
        if mode != '0':
            print('\n=== 影片轉場 ===')
            print('1. 柔和溶解（預設）  2. 淡黑  3. 淡白  0. 無轉場')
            transition_choice = input('轉場 [1]：').strip() or '1'
            transition_ids = {'0':None,'1':'concat.dissolve','2':'concat.fade-black','3':'concat.fade-white'}
            if transition_choice not in transition_ids:
                raise ValueError('無效轉場選項')
            transition_id = transition_ids[transition_choice]
            duration = 0.5
            if transition_id:
                duration = float(input('轉場秒數 [0.5]：').strip() or '0.5')
                if not 0.1 <= duration <= 10:
                    raise ValueError('轉場秒數需介於 0.1 到 10')
        if mode != '0':
            if position > .01:
                raise RuntimeError('音樂同步剪輯必須從0秒開始，請建立新專案。既有專案請用模式0重新匯出。')
            c.call('project.setVideo',path=project_path,video={'width':3840,'height':2160,'rateNum':60000,'rateDen':1001})
        has_previous = position > 0.001
        media_cache = {}  # Each file imported once; many clips can reuse one media ID.
        for row in (rows if mode != '0' else []):
            f=(INPUT/row['filename']).resolve()
            if not f.is_file() or INPUT.resolve() not in f.parents:
                raise FileNotFoundError(f'找不到 input 素材：{row["filename"]}')
            print(f'[處理影片] {f.name}', flush=True)
            media_key = str(f).casefold()
            mid = media_cache.get(media_key)
            if mid:
                print(f'[媒體重用] {f.name} -> {mid}', flush=True)
            else:
                current = c.call('project.get', path=project_path)
                mid = existing_media_id(current, f)
                if mid:
                    print(f'[媒體已存在] {f.name} -> {mid}', flush=True)
                else:
                    imported = c.call('media.import', path=project_path, file=str(f))
                    mid = imported.get('createdId')
                    if not mid:
                        # Concat can return no createdId if the media already exists.
                        current = c.call('project.get', path=project_path)
                        mid = existing_media_id(current, f)
                if not mid:
                    raise RuntimeError(f'{f.name}: 無法取得媒體 ID。請檢查 project.get 的 media 路徑與 media.import 回應。')
                media_cache[media_key] = mid
            view=c.call('edit.apply',path=project_path,command={'op':'addClip','mediaId':mid,'trackId':'T1','start':position})
            cid=view['createdId']
            # Concat 的 clip 存在 timeline.clips，而不是 track.clips。
            # 使用 next(..., None) 避免空結果引發無說明的 StopIteration。
            clip = next((item
                         for timeline in view.get('project', {}).get('timelines', [])
                         for item in timeline.get('clips', [])
                         if item.get('id') == cid), None)
            if clip is None:
                # 若編輯 API 回傳的是簡化視圖，再向 Concat 讀取完整專案。
                refreshed = c.call('project.get', path=project_path)
                clip = next((item
                             for timeline in refreshed.get('project', {}).get('timelines', [])
                             for item in timeline.get('clips', [])
                             if item.get('id') == cid), None)
            if clip is None:
                raise RuntimeError(
                    f'{f.name}: Concat 已回傳片段 ID {cid!r}，但專案時間軸找不到該片段。'
                    '請回傳 error_log.txt，並確認專案未在 GUI 同時編輯。')
            full=float(clip['duration']); start=float(row.get('start') or 0)
            end=float(row['end']) if row.get('end','').strip() else full
            if start < 0 or end <= start:
                raise ValueError(f'{f.name}: 無效剪輯範圍 {start}-{end}s，需符合 0 <= 開始 < 結束')
            if start >= full:
                raise ValueError(f'{f.name}: 起始時間 {start:.2f}s 已超過影片長度 {full:.2f}s，無法產生有效片段。請修改 cuts.csv')
            if end > full:
                print(f'警告：{f.name} 結束時間 {end:.2f}s 超過影片長度 {full:.2f}s，自動調整為 {full:.2f}s。')
                end = full
            if end<full-0.00001:
                c.call('edit.apply',path=project_path,command={'op':'trimClip','clipId':cid,'edge':'end','delta':end-full})
            if start>0:
                c.call('edit.apply',path=project_path,command={'op':'trimClip','clipId':cid,'edge':'start','delta':start})
                c.call('edit.apply',path=project_path,command={'op':'moveClips','moves':[{'clipId':cid,'start':position,'trackId':'T1'}]})
            if transition_id and has_previous:
                transition_duration = min(duration, (end-start)/2)
                if transition_duration >= 0.1:
                    c.call('edit.apply', path=project_path, command={
                        'op':'updateClip', 'clipId':cid,
                        'patch':{'transitionIn':{
                            'id':transition_id,
                            'duration':transition_duration
                        }}
                    })
                    print(f'轉場：{transition_id} / {transition_duration:.2f}s')
            # Keep environmental audio quiet under the soundtrack.
            c.call('edit.apply',path=project_path,command={'op':'updateClip','clipId':cid,'patch':{'volume':0.18}})
            has_previous = True
            position+=end-start
            print(f'已加入 {f.name}: {start:.2f} - {end:.2f}, 時間軸長度 {position:.2f}s')
        if mode != '0' and has_previous:
            # The final video clip fades out for only 0.12s; don't append a
            # black clip or lengthen the timeline just for the end card.
            last_video_id = cid
            c.call('edit.apply',path=project_path,command={
                'op':'updateClip','clipId':last_video_id,
                'patch':{'fadeOut':min(0.12,position/8)}
            })
            print('[片尾] 最後畫面淡出縮短至最多 0.12 秒（不增加黑畫面）')
        # Each overlay/soundtrack gets its own dedicated Concat track.
        # Do not hardcode T2/T3: title animations can consume those tracks.
        def create_dedicated_track(label):
            result = c.call('edit.apply', path=project_path,
                            command={'op': 'addTrack', 'bottom': False})
            track_id = result.get('createdId')
            if not track_id:
                raise RuntimeError(f'{label} 專用軌道建立後未回傳 Track ID；請檢查 Concat API 版本')
            print(f'[獨立軌道] {label}: {track_id}')
            return track_id

        title_track_id = None
        subtitle_track_id = None
        if mode != '0' and title_text and position > 0:
            title_track_id = create_dedicated_track('主標題')
            if subtitle_text:
                subtitle_track_id = create_dedicated_track('副標題')
            title_duration = min(3.0, position)
            effects = ['fade_zoom', 'rise', 'typewriter', 'pop', 'pulse']
            effect_choice = random.choice(effects)
            print(f'[開場動態] 本次效果：{effect_choice}')
            def add_title_segment(content, size, y, begin, length, fade_in=0.0, fade_out=0.0, opacity=1.0, track_id=None):
                # Native Concat editable text clips; use stepped animation because
                # current API does not expose arbitrary text-transform keyframes.
                if length <= 0.015 or not content:
                    return
                style = {
                    'content':content, 'fontFamily':'Noto Sans TC',
                    'fontSize':size, 'fontWeight':700.0, 'italic':False,
                    'textCase':'original', 'underline':False, 'strikethrough':False,
                    'color':'#ffffff', 'align':'center', 'opacity':opacity,
                    'strokeWidth':0.002, 'strokeColor':'#000000', 'shadow':True,
                    'background':'', 'backgroundRadius':0.0135,
                    'backgroundPaddingX':0.0315, 'backgroundPaddingY':0.018,
                    'lineHeight':1.2, 'tracking':0.0, 'maxWidth':0.90,
                    'maxHeight':0.0, 'runs':[]
                }
                result = c.call('edit.apply', path=project_path, command={
                    'op':'addTextClip', 'trackId':track_id or title_track_id, 'above':True,
                    'start':begin, 'style':style, 'duration':length,
                    'offsetY':y
                })
                tid=result.get('createdId')
                if not tid:
                    raise RuntimeError('Concat 未回傳開場文字 Clip ID，請檢查 API 版本')
                if fade_in > 0 or fade_out > 0:
                    c.call('edit.apply',path=project_path,command={
                        'op':'updateClip','clipId':tid,'patch':{
                            'fadeIn':min(fade_in,length/2),
                            'fadeOut':min(fade_out,length/2)
                        }
                    })
            def animate_title(content, size, y, effect, duration):
                if duration <= 0.35:
                    add_title_segment(content,size,y,0,duration)
                    return
                # All five effects are native, editable Concat text segments.
                # Segment boundaries are contiguous; no duplicated title layer.
                if effect == 'fade_zoom':
                    stages=[(.00,.20,.80),(.20,.18,.93),(.38,duration-.38,1.0)]
                    for begin,length,mul in stages:
                        add_title_segment(content,size*mul,y,begin,length,
                                          fade_in=.10 if begin==0 else 0,
                                          fade_out=min(.45,length/3) if begin>.37 else 0)
                elif effect == 'rise':
                    stages=[(0,.15,y+.08),(.15,.15,y+.04),(.30,duration-.30,y)]
                    for begin,length,yy in stages:
                        add_title_segment(content,size,yy,begin,length,
                                          fade_in=.08 if begin==0 else 0,
                                          fade_out=min(.45,length/3) if begin>=.30 else 0)
                elif effect == 'typewriter':
                    # 5 stepped reveals; Unicode slicing works for common CJK titles.
                    n=min(5,max(1,len(content)))
                    intro=min(1.0,duration*.45)
                    for i in range(n):
                        begin=i*intro/n
                        length=intro/n
                        count=max(1,round((i+1)*len(content)/n))
                        add_title_segment(content[:count],size,y,begin,length)
                    add_title_segment(content,size,y,intro,duration-intro,
                                      fade_out=min(.45,(duration-intro)/3))
                elif effect == 'pop':
                    stages=[(0,.12,.65),(.12,.12,1.14),(.24,duration-.24,1.0)]
                    for begin,length,mul in stages:
                        add_title_segment(content,size*mul,y,begin,length,
                                          fade_out=min(.45,length/3) if begin>.23 else 0)
                else:  # pulse: a subtle, restrained emphasis at the beginning.
                    stages=[(0,.20,1.0),(.20,.20,1.09),(.40,.20,.98),(.60,duration-.60,1.0)]
                    for begin,length,mul in stages:
                        add_title_segment(content,size*mul,y,begin,length,
                                          fade_out=min(.45,length/3) if begin>.59 else 0)
            # Title sizing: 0.085 main, 0.055 subtitle (three 0.01-size steps smaller).
            # Concat fontSize is relative to frame height, not a Windows point size.
            MAIN_TITLE_SIZE = 0.085
            SUBTITLE_SIZE = MAIN_TITLE_SIZE - 0.030
            animate_title(title_text, MAIN_TITLE_SIZE, 0.12, effect_choice, title_duration)
            if subtitle_text:
                # Subtitle is kept clean to remain readable during the title effect.
                add_title_segment(subtitle_text,SUBTITLE_SIZE,0.24,0,title_duration,
                                  fade_in=min(.35,title_duration/4),
                                  fade_out=min(.45,title_duration/4),
                                  track_id=subtitle_track_id)
            print(f'[開場標題] {title_text} / {subtitle_text}，{title_duration:.1f}s，主標題字級0.085／副標題0.055')
        if mode != '0':
            # Background soundtrack on a separate lane, trim to actual edited footage duration.
            music_track_id = create_dedicated_track('背景音樂')
            print('[音樂] 匯入背景音樂:',music_path.name)
            imported=c.call('media.import',path=project_path,file=str(music_path.resolve()))
            mid=imported.get('createdId')
            if not mid:raise RuntimeError('音樂匯入後未取得媒體ID')
            added=c.call('edit.apply',path=project_path,command={'op':'addClip','mediaId':mid,'trackId':music_track_id,'start':0.0})
            cid=added.get('createdId')
            if not cid:raise RuntimeError('音樂加入時間軸後未取得片段ID')
            view=c.call('project.get',path=project_path)
            clip=next((x for tl in view.get('project',view).get('timelines',[]) for x in tl.get('clips',[]) if x.get('id')==cid),None)
            if not clip:raise RuntimeError('音樂片段無法在時間軸找到')
            full=float(clip['duration'])
            if full < position-.1:
                raise RuntimeError(f'音樂實際長度 {full:.2f}s，比影片 {position:.2f}s 短；請換較長的音樂')
            if full > position+.0001:
                c.call('edit.apply',path=project_path,command={'op':'trimClip','clipId':cid,'edge':'end','delta':position-full})
            c.call('edit.apply',path=project_path,command={'op':'updateClip','clipId':cid,'patch':{'volume':0.90,'fadeIn':0.0,'fadeOut':min(1.5,position/4)}})
            print(f'[音樂] 音樂軌 {music_track_id}，PCM WAV 已去除開頭靜音，從0秒播放、無淡入；音樂90%，現場音18%')
        c.call('project.save',path=project_path)
        if not export_mp4:
            print('\n[完成] 已儲存可編輯 Concat 專案：', project_path)
            print('[輸出方式] 僅儲存專案，未執行 MP4 匯出；日後可選模式 0 再匯出。')
            return
        export_stem = Path(project_path).name + '_' + datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = OUTPUT / (export_stem + '.mp4')
        suffix = 2
        while output_path.exists():
            output_path = OUTPUT / f'{export_stem}_{suffix}.mp4'
            suffix += 1
        output = str(output_path)
        print('匯出目標:', output, flush=True)
        started=c.call('export.run',path=project_path,output=output,codec='h264',hdr=False,crf=crf,preset='medium')
        final=c.wait_job(started['job'])
        if final['method']=='export.failed': raise RuntimeError(str(final['params'].get('error')))
        verify_export(output_path)
        print('[提醒] OpenCV僅檢查影像；請用播放器確認背景音樂及環境音混音。')
        print('完成:',output,'\n可編輯專案:',project_path)
    finally:
        if project_path:
            try:
                c.call('project.close', path=project_path)
                print('已釋放 Concat 專案鎖定')
            except Exception as close_error:
                print('提醒：API 無法關閉專案，請退出 Concat 後重新開啟：',repr(close_error))
        c.close()

if __name__=='__main__':
    try:main()
    except Exception as exc:
        detail = traceback.format_exc()
        (ROOT / 'error_log.txt').write_text(detail, encoding='utf-8')
        print('\n錯誤類型:', type(exc).__name__, file=sys.stderr)
        print('錯誤內容:', repr(exc), file=sys.stderr)
        print(detail, file=sys.stderr)
        print('完整錯誤已儲存：', ROOT / 'error_log.txt', file=sys.stderr)
        sys.exit(1)
