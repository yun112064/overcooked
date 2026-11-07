# 共用管理骨架

這個模組提供 `management-v1.0.0-rc1` 的共用授權、來源證據、候選修訂、驗證、雙範圍審核及不可變發布。現有食譜、營養、價格與已採用餐單保留原有儲存方式。

## 執行邊界

- `MANAGEMENT_ENABLED` 預設 false。API 使用既有 session 與 Origin 檢查；家庭 membership 不授予管理權限。
- 正式啟用前配置非 owner PostgreSQL 應用角色，僅授予所需表權限。管理不可變表不授予 UPDATE／DELETE／TRUNCATE；應用角色不得具 schema CREATE、CREATEROLE、CREATEDB 或 superuser。API 檢查不安全角色並拒絕服務。
- 首位 access_admin 只以離線 `python -m app.management.bootstrap --user-id <existing-user-uuid> --reason <reason>` 授予既有帳號；沒有預設帳密或自動帳號建立。
- Validation API 建立可追蹤任務；目前可明確用 `python -m app.management.validate_task --task-id <id>` 執行一次純驗證。沒有啟動常駐 worker／scheduler。執行前重新核對發起者權限及 revision。
- 新發布只寫 management 表。沒有 consumer adapter 時，activation plan 顯示阻擋，activate／rollback 拒絕。沒有正式生成 activation channel，沒有 T04 核准繼承或外部執行。
- 價格候選與新 Food ID 可先保存、檢查格式；實際發布仍需專屬 domain 規則與下游相容驗收。來源政策只允許已註冊 fixture targets。

## API 進度

支援 `/admin/v1` 的 capabilities、grant／revoke、artifact 上傳、proposal 建立／詳情／修訂／驗證／送審／審核／撤回、publication build／manifest、驗證任務狀態及 activation plan。`GET /tasks/{task_id}/result` 讀取完成的 ValidationResult；capabilities 可用 `domain` 查詢受 scope 限制的動作。這兩個是讀取面的相容補充。

Dashboard、完整列表／diff／監控端點、真實採集與模型 provider 不在此骨架。App 內有完整輸入 schema；部署不依賴開發機工作紀錄或外部文件路徑。

## 資料庫與驗證

`m1107management1` 由 `d1105merge1` 新增管理表、初始空 binding 與不可變保護；不種入人員角色或核准。有管理資料時拒絕破壞式 downgrade。舊 Admin `d1105admin2` 不是可直接升級的已知 Alembic revision；沒有原遷移時，測試中的 synthetic reconciliation 不構成正式升級授權或工具。

SQLite／ASGI 測試：`PYTHONPATH=backend:tests python -m pytest tests/test_management_foundation.py tests/test_management_api.py`（Windows 使用分號分隔 PYTHONPATH）。

PostgreSQL 測試是明確 opt-in：使用新建的 `meal_planner_1102_v3_management_test`、`meal_planner_1102_v3_management_legacy_test`、`meal_planner_1102_v3_management_reference_test`，每個 database comment 設為 `overcooked:disposable:management`。`MANAGEMENT_TEST_DATABASE_URL` 指向第一個庫；測試先檢查實際庫名及 marker，然後只重建這三個庫的 public schema。禁止給正式或匯入資料庫加這個標記。`python -m pytest tests/test_management_postgres.py`。
