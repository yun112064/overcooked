# 共用管理骨架

這個模組提供 `management-v1.0.0-rc1` 的共用授權、來源證據、候選修訂、驗證、雙範圍審核及不可變發布。現有食譜、營養、價格與已採用餐單保留原有儲存方式。

## 執行邊界

- `MANAGEMENT_ENABLED` 預設 false。API 使用既有 session 與 Origin 檢查；家庭 membership 不授予管理權限。
- 正式啟用前配置非 owner PostgreSQL 應用角色，僅授予所需表權限。管理不可變表不授予 UPDATE／DELETE／TRUNCATE；應用角色不得具 schema CREATE、CREATEROLE、CREATEDB 或 superuser。API 檢查不安全角色並拒絕服務。
- 首位 access_admin 只以離線 `python -m app.management.bootstrap --user-id <existing-user-uuid> --reason <reason>` 授予既有帳號；沒有預設帳密或自動帳號建立。
- Validation API 建立可追蹤任務；目前可明確用 `python -m app.management.validate_task --task-id <id>` 執行一次純驗證。沒有啟動常駐 worker／scheduler。執行前重新核對發起者權限及 revision。
- 新發布只寫 management 表。價格通道的 T13 adapter 由 MANAGEMENT_PRICE_POLICY_ENABLED 控制，預設 false，啟用需要 PostgreSQL 與 price.activate 權限。其他通道的 activation plan 仍回傳 adapter 阻擋。正式生成的食譜與營養就緒狀態維持原契約。
- 價格候選依指定食材發布、Food ID、來源證據、觀察時間及效期驗證。新 Food ID 仍須另外完成下游相容驗收。來源政策只允許已註冊 fixture targets。

## API 進度

支援 `/admin/v1` 的 capabilities、grant／revoke、artifact 上傳、proposal 建立／詳情／修訂／驗證／送審／審核／撤回、publication build／manifest、驗證任務狀態及 activation plan。`GET /tasks/{task_id}/result` 讀取完成的 ValidationResult；capabilities 可用 `domain` 查詢受 scope 限制的動作。這兩個是讀取面的相容補充。

Dashboard 提供列表、diff、版本選單、原始觀察商品全名與分權審核。連線狀態保留未設定語意，真實採集與模型執行保持停用。App 內有完整輸入 schema，部署使用封裝的契約。

## 價格版的發布與使用

- 價格發布與啟用分開。具有權限的操作人員先建立五分鐘有效的啟用計畫，確認後才以 binding revision 比對更新。伺服器再次核對 manifest、依賴與目前食譜發布。計畫綁定建立者與指定目標，過期、被替換的 binding 或跨使用者計畫會拒絕。
- 後續版的 base 必須符合目前啟用版本。回復只接受曾在同一通道啟用過的版本，仍需新計畫及遞增的 binding revision。歷史 publication、來源、審核與已採用餐單皆保留。
- 計畫與執行時均按食材檢查目前有效的區間，支援同範圍的不重疊未來區間。沒有目前可用成員的整版會拒絕啟用。部分食材到期、尚未生效或條件無法解析時，計畫明確顯示警告，T13 對該食材保留未知成本與原因。
- 新增草稿可明確選擇價格基礎發布版本，或從發布內容選擇「新增至此價格版本」。資料版本與字典須符合該基礎，新增成員與既有成員一起形成後續完整政策。
- 已發布人工價的修訂，在 PriceContent 明確指定可選欄位 replaces_version_id，並綁定 base_publication_id 與 manifest hash。目標必須是該 base 的精確 price 成員版本，Food ID、字典版本、舊食譜發布、provider、地區／店別／促銷及修訂種類須相同。新價格、商品或效期需新來源證據和完整雙審核。發布保留 logical ID 並產生新的不可變版本。
- management-price-v1 以 price-extension-v1.json 封裝相容增補。GET /admin/v1/contracts/price-v1 提供增補版本及完整組合 schema，需 registry.read。舊請求與既有 revisions 可繼續使用，原始 rc1 檔與資料不需 migration。PriceObservation 預設維持原回應，明確使用 include_product_name=true 時才回傳商品原名。Dashboard 使用這個選項。
- 開關啟用後，T13 成本與預算下限讀取同一個已啟用價格版。人工報價優先，商品對應讀取精確 SKU。效期、字典、來源與適用條件不符時保留原因及未知成本。計價結果保留價格 publication 和審核識別碼，原始零售快照不變。
- 歷史結果缺少可確認的食譜版本時，重試無法套用目前管理價。新結果會保存管理價所屬食譜版本。重新查價仍依既有流程另存結果，原結果與採用快照保持原值。

## 資料庫與驗證

`m1107management1` 由 `d1105merge1` 新增管理表、初始空 binding 與不可變保護；不種入人員角色或核准。有管理資料時拒絕破壞式 downgrade。舊 Admin `d1105admin2` 不是可直接升級的已知 Alembic revision；沒有原遷移時，測試中的 synthetic reconciliation 不構成正式升級授權或工具。

SQLite／ASGI 測試：`PYTHONPATH=backend:tests python -m pytest tests/test_management_foundation.py tests/test_management_api.py`（Windows 使用分號分隔 PYTHONPATH）。

PostgreSQL 測試是明確 opt-in：使用新建的 `meal_planner_1102_v3_management_test`、`meal_planner_1102_v3_management_legacy_test`、`meal_planner_1102_v3_management_reference_test`，每個 database comment 設為 `overcooked:disposable:management`。`MANAGEMENT_TEST_DATABASE_URL` 指向第一個庫；測試先檢查實際庫名及 marker，然後只重建這三個庫的 public schema。禁止給正式或匯入資料庫加這個標記。`python -m pytest tests/test_management_postgres.py`。
