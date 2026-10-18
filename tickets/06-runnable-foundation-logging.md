# 06 — 可啟動系統骨架與正式日誌

- Spec：`../spec.md`
- Blocked by：無
- 覆蓋：R11、架構與技術限制；AC26
- 狀態：使用者已核准本次拆分及後續修正；尚未實作。

## 交付成果

交付按 Spec 選型可啟動的前後端及 PostgreSQL 環境，頁面可透過 API 驗證資料庫連線，並具備正式日誌。

## 驗收條件

- 建立 React／FastAPI／SQLAlchemy／Alembic／pgvector 與 Compose 的可重現啟動及遷移流程；提供設定範例，遵守 Code／Data／Runtime 分離、秘密不入庫及測試隔離。
- 以共用 Python logging 輸出各程序分檔 JSONL，具時間、等級、事件、來源、訊息及適用追蹤欄位；驗證秘密、健康資料、照片及未清理模型內容不入 Log。
- 驗證每日或 10 MiB 輪替、14 日及 200 MiB 政策；不刪執行中或調查保留檔，無可清理空間有提示。提供必要測試與協作／CI 檢查入口，不把空骨架當成功產品。

