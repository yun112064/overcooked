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

Dashboard、完整列表／diff／監控端點與模型 provider 不在此骨架。App 內有完整輸入 schema；部署不依賴開發機工作紀錄或外部文件路徑。

### iCook 單頁預覽

從專案根目錄執行 `python scripts/preview_icook_recipe.py https://icook.tw/recipes/33570` 可取得 JSON。已下載的 HTML 可用 `--html` 重播。

管理功能啟用後，具 `recipe.read` 與 `recipe.edit` 權限的帳號可呼叫 `POST /admin/v1/recipe-candidates/icook/preview`，送出 `{"url":"https://icook.tw/recipes/33570"}`。此入口只接受 iCook 的 HTTPS 數字食譜網址，依 robots.txt 決定是否擷取，並拒絕轉址。

回應的 `tables` 對應 1103_V3 工作簿，含原始來源、食譜、步驟和食材列。資料列 ID 用於預覽內的關聯，正式入庫仍需配置 ID 並完成內容及來源使用權審核。`Food_ID`、過敏原、營養和來源缺少的數值保留為 `null`。只有可從原文確定的質量單位會換算成 `weight_g`，以整份來源食譜的烹調前重量為基準。回應包含擷取時間、HTML SHA-256 和待審狀態。預覽不建立管理提案，也不寫入食譜庫。

## 資料庫與驗證

`m1107management1` 由 `d1105merge1` 新增管理表、初始空 binding 與不可變保護；不種入人員角色或核准。有管理資料時拒絕破壞式 downgrade。舊 Admin `d1105admin2` 不是可直接升級的已知 Alembic revision；沒有原遷移時，測試中的 synthetic reconciliation 不構成正式升級授權或工具。

### `d1105admin2` 的 V3 接續工具 v1

`scripts/adopt_v3_management_v1.py` 只支援已核對的 V3 結構。正式作業先停寫並建立可還原的加密備份，另外保存角色與部署設定。確認備份能解密和還原，並在同一個唯讀快照或同一個停寫區間擷取來源基線。

1. 設定程序環境變數 `V3_ADOPTION_DATABASE_URL` 為 V3 來源連線，執行 `python scripts/adopt_v3_management_v1.py capture --backup <加密備份> --target <獨立目標庫> --approval <變更核准識別碼>`。保存標準輸出的 JSON manifest，連同備份雜湊、核准紀錄及還原證明留存。`capture` 使用唯讀交易。
2. 還原庫須使用 `meal_planner_1102_v3_management_target_*` 的新名稱、取消 `PUBLIC` 的 `CONNECT` 權限，並由操作員以 `COMMENT ON DATABASE` 標記 `overcooked:v3-management-target:<變更核准識別碼>`。設連線變數為目標庫，執行 `preflight --backup <同一份備份> --manifest <已核准清冊>`。工具比對備份雜湊、還原資料、序列與已釘選的結構指紋。
3. 目標庫保持停寫且無其他連線。以相同參數改執行 `apply`。工具鎖定目標，在同一交易補上 `meal_menu_rejections`、再次核對、標記 `d1105merge1`、升級 `m1107management1` 並留下稽核。提交前檢查完整的管理表與不可變觸發器、空的角色／提案／發布狀態及 `alembic check`。遇到不符或執行失敗，交易回滾。
4. 遷移後另行設定非 owner 的後台角色及驗證執行器。帳號資料和舊食譜相關表按使用需求給 `SELECT`，登入工作階段按需求給 `INSERT`／`DELETE`。管理流程只給必要的 `SELECT`、`INSERT` 及可變欄位的 `UPDATE`。PostgreSQL 列鎖需要 `management_access_state.revision`、`household_memberships.id` 的欄位權限。驗收須包含應用帳號可存草稿及重驗、無法修改食譜庫與來源原文、無法改寫不可變證據或新增發布。工具本身不建立登入角色、不切換連線或啟用管理後台。

遷移失敗時先核對目標庫的版本標記、管理表與舊資料指紋。未開放新庫寫入時，可保持原庫作為回復點或重新從加密備份還原。新庫開始接收寫入後，先停寫、備份新庫並核對增量資料，再決定回切或向前修復。

SQLite／ASGI 測試：`PYTHONPATH=backend:tests python -m pytest tests/test_management_foundation.py tests/test_management_api.py`（Windows 使用分號分隔 PYTHONPATH）。

PostgreSQL 測試是明確 opt-in：使用新建的 `meal_planner_1102_v3_management_test`、`meal_planner_1102_v3_management_legacy_test`、`meal_planner_1102_v3_management_reference_test`，每個 database comment 設為 `overcooked:disposable:management`。`MANAGEMENT_TEST_DATABASE_URL` 指向第一個庫；測試先檢查實際庫名及 marker，然後只重建這三個庫的 public schema。禁止給正式或匯入資料庫加這個標記。`python -m pytest tests/test_management_postgres.py`。
