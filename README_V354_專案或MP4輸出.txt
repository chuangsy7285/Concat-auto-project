Concat V3.5.4｜輸出方式選擇

新增選單：
1. 儲存 Concat 專案並匯出 MP4（預設）
2. 僅儲存可編輯 Concat 專案，不匯出 MP4

使用方法：
- 執行 run.bat，先選擇模式 1（重新分析素材）、2（使用 cuts.csv）或 0（舊專案）。
- 接著選擇輸出方式 1 或 2。
- 選 2 時仍會建立/修改、儲存 Concat 專案，包含剪輯、照片、標題、音樂等時間軸內容；不呼叫 export.run。
- 日後需要 MP4，重新執行 run.bat，選模式 0、輸出方式 1，選擇該專案。
- 專案存於 projects 資料夾；專案素材依賴原始 input、music 及產生的暫存媒體，請勿隨意刪除或搬移。
- 保留原有 token.txt、input、music；安裝時避免覆蓋自己的 Token。

檢查：Python 語法及 ZIP 完整性通過；未在 Windows Concat API 實機驗證。
