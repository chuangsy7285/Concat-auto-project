Concat Windows V3.1 — 旅遊影片依音樂節奏自動剪輯（60秒）

一、安裝
1. Windows 安裝 Python 3.10+，並將 Python 加入 PATH。
2. 執行 install.bat 安裝 numpy、scipy、opencv-python。
3. 安裝 FFmpeg 並加入 PATH，命令提示字元執行 ffmpeg -version 應成功。
4. 在 Concat → Settings → Automation 啟用 API，確認 127.0.0.1:7420。
5. 將 API Token 貼入 token.txt（不要分享 Token）。

二、檔案
input/    放旅遊 MP4/MOV 影片
music/    放至少 60 秒的 MP3/WAV/FLAC/M4A 背景音樂
beats.csv 音樂節奏候選點（秒、強度）
cuts.csv  選取片段（來源開始/結束、時間軸開始/結束、分數）

三、使用
執行 run.bat，選模式 1，選音樂，分析後可先檢查兩份 CSV。
確認 YES，再選 1 建立新專案（建議）。
模式 2 使用已編輯 cuts.csv；片段總長必須約 60 秒。
模式 0 僅重新匯出既有專案，不修改影片或音樂。

四、規格
音樂 onset/節奏峰值分析 + OpenCV 影像品質/動態評分；每段 4–8 秒。
專案設 3840x2160、60000/1001 FPS，預設 H.264 CRF 12。
原片音量 18%、音樂 90%，音樂在 T3 音軌，開頭/結尾淡入淡出。
原生 Concat dissolve/fade-black/fade-white 轉場可選。
匯出後 OpenCV 抽樣檢查是否黑畫面；音訊仍需人工播放驗證。

五、限制與注意
本機啟發式節奏/畫面分析，不是語意辨識 AI；自由節奏音樂可能不準。
素材不足會明確停止，不會複製畫面或虛構精彩片段。
匯出/混音依 Concat 安裝版本支援；未在實際 Windows Concat 完整測試。
選既有專案追加可能重複；音樂同步模式要求空白新專案。
不要在 Concat GUI 與 API 同時開啟相同專案。
