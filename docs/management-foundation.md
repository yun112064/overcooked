# 共用管理骨架

這個模組提供 `management-v1.0.0-rc1` 的共用授權、來源證據、候選修訂、驗證、雙範圍審核及不可變發布。現有食譜、營養、價格與已採用餐單保留原有儲存方式。

## 執行邊界

- `MANAGEMENT_ENABLED` 預設 false。API 使用既有 session 與 Origin 檢查；家庭 membership 不授予管理權限。
- 正式啟用前配置非 owner PostgreSQL 應用角色，僅授予所需表權限。管理不可變表不授予 UPDATE／DELETE／TRUNCATE；應用角色不得具 schema CREATE、CREATEROLE、CREATEDB 或 superuser。API 檢查不安全角色並拒絕服務。
- 首位 access_admin 只以離線 `python -m app.management.bootstrap --user-id <existing-user-uuid> --reason <reason>` 授予既有帳號；沒有預設帳密或自動帳號建立。
- Validation API 建立可追蹤任務。後台可要求立即執行純資料驗證，也可由原申請人執行自己的排隊任務。原有 `python -m app.management.validate_task --task-id <id>` 仍可用。執行時重新核對發起者權限、提案狀態和修訂版本。
- 新發布寫入 management 表。價格通道由 `MANAGEMENT_PRICE_POLICY_ENABLED` 控制，食譜通道由 `MANAGEMENT_RECIPE_CATALOG_ENABLED` 控制，兩者皆預設 false。啟用需要 PostgreSQL 和對應領域的 activate 權限。食材與來源政策的啟用通道仍待接入。
- 價格候選依指定食材發布、Food ID、來源證據、觀察時間及效期驗證。新 Food ID 仍須另外完成下游相容驗收。來源政策只允許已註冊 fixture targets。

## API 進度

支援 `/admin/v1` 的 capabilities、grant／revoke、artifact 上傳、proposal 建立／詳情／修訂／驗證／送審／審核／撤回、publication build／manifest、驗證任務狀態及 activation plan。`GET /tasks/{task_id}/result` 讀取完成的 ValidationResult；capabilities 可用 `domain` 查詢受 scope 限制的動作。這兩個是讀取面的相容補充。

Dashboard 提供列表、diff、版本選單、原始觀察商品全名與分權審核。連線狀態保留未設定語意，真實採集與模型執行保持停用。App 內有完整輸入 schema，部署使用封裝的契約。

總覽依授權領域彙整待審、待補件與失敗驗證，食譜、食材與價格可直接開啟對應清單。提案保留分頁審查意見，稽核帳號只可查閱其稽核領域內的操作紀錄。權限管理員可用既有帳號 UUID 查詢、授予或撤銷角色，權限檢查與稽核沿用共用服務。

主資料庫監控從目前應用連線取得 PostgreSQL 探測耗時、連線數及資料庫容量。排程頁區分 API 環境設定與 PostgreSQL 保存的每日查價執行紀錄。排程程序心跳、下一次執行、其他排程歷史及操作控制尚未接入。

## 菜單確認資料流

- 在獨立管理網站開啟「資料庫與任務 → 即時資料流」。具 `operations.read` 權限的帳號可追蹤最近的確認紀錄。事件只顯示追蹤 ID、菜單模式、里程碑及時間。
- 兩種菜單採用入口都在同一筆交易內寫入菜單快照與事件。確認成功後可看到採用歷史，以及快照實際保存的食材、營養或成本結果。舊採用紀錄保留原樣，事件表從遷移後的新確認開始累積。
- 頁面每兩秒讀取已提交的事件。連線失敗時停在最後一次成功回應確認的位置與時間，重連後用新回應同步。動畫只呈現已確認的里程碑。
- 部署時由遷移角色將 schema 升至 `m1109mealflow1`，應用程式角色需有 `meal_confirmation_events` 的 `SELECT` 及 `INSERT` 權限。

## 食譜版本的啟用與回復

- 在 PostgreSQL 環境，API 和生成 worker 都設 `MANAGEMENT_RECIPE_CATALOG_ENABLED=true`，管理 API 另需 `MANAGEMENT_ENABLED=true` 與非 owner 應用角色。管理者在「食譜與 AI 候選 → 發布版本」選取已雙審核發布的版本，輸入原因，建立並確認五分鐘有效的計畫。每次切換會重驗基底、來源擷取雜湊、正式 Food ID 與指標修訂號。回復可選曾啟用的管理版，或選「回復原始 T01 食譜」。
- 啟用後，選菜目錄、T11 正式查詢、T12 預覽、生成 worker 與預算下限讀取同一個發布指標。既有 T01 發布與 Food ID 字典作為基底，被修訂的食譜按管理發布清單替換，其餘沿用基底成員。新的生成工作在讀取食譜時固定該次版本，已保存結果及已採用餐單保留原快照。
- 管理食譜保有自己的配方與食材列識別碼，逐列烹調憑據綁定新版重量和步驟。T04 與 T13 繼續使用基底的已指定來源版本。未核准的營養對照、缺少烹調依據和無法換算的批次份量仍按原有規則呈現缺口。

### iCook 單頁預覽

從專案根目錄執行 `python scripts/preview_icook_recipe.py https://icook.tw/recipes/33570` 可取得 JSON。已下載的 HTML 可用 `--html` 重播。

管理功能啟用後，具 `recipe.read` 與 `recipe.edit` 權限的帳號可呼叫 `POST /admin/v1/recipe-candidates/icook/preview`，送出 `{"url":"https://icook.tw/recipes/33570"}`。此入口只接受 iCook 的 HTTPS 數字食譜網址，依 robots.txt 決定是否擷取，並拒絕轉址。

回應的 `tables` 對應 1103_V3 工作簿，含原始來源、食譜、步驟和食材列。資料列 ID 用於預覽內的關聯，正式入庫仍需配置 ID 並完成內容及來源使用權審核。`Food_ID`、過敏原、營養和來源缺少的數值保留為 `null`。只有可從原文確定的質量單位會換算成 `weight_g`，以整份來源食譜的烹調前重量為基準。回應包含擷取時間、HTML SHA-256 和待審狀態。預覽不建立管理提案，也不寫入食譜庫。

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

### `d1105admin2` 的 V3 接續工具 v1

`scripts/adopt_v3_management_v1.py` 只支援已核對的 V3 結構。正式作業先停寫並建立可還原的加密備份，另外保存角色與部署設定。確認備份能解密和還原，並在同一個唯讀快照或同一個停寫區間擷取來源基線。

1. 設定程序環境變數 `V3_ADOPTION_DATABASE_URL` 為 V3 來源連線，執行 `python scripts/adopt_v3_management_v1.py capture --backup <加密備份> --target <獨立目標庫> --approval <變更核准識別碼>`。保存標準輸出的 JSON manifest，連同備份雜湊、核准紀錄及還原證明留存。`capture` 使用唯讀交易。
2. 還原庫須使用 `meal_planner_1102_v3_management_target_*` 的新名稱、取消 `PUBLIC` 的 `CONNECT` 權限，並由操作員以 `COMMENT ON DATABASE` 標記 `overcooked:v3-management-target:<變更核准識別碼>`。設連線變數為目標庫，執行 `preflight --backup <同一份備份> --manifest <已核准清冊>`。工具比對備份雜湊、還原資料、序列與已釘選的結構指紋。
3. 目標庫保持停寫且無其他連線。以相同參數改執行 `apply`。工具鎖定目標，在同一交易補上 `meal_menu_rejections`、再次核對、標記 `d1105merge1`、升級 `m1107management1` 並留下稽核。提交前檢查完整的管理表與不可變觸發器、空的角色／提案／發布狀態及 `alembic check`。遇到不符或執行失敗，交易回滾。
4. 遷移後另行設定非 owner 的後台角色及驗證執行器。帳號資料和舊食譜相關表按使用需求給 `SELECT`，登入工作階段按需求給 `INSERT`／`DELETE`。管理流程只給必要的 `SELECT`、`INSERT` 及可變欄位的 `UPDATE`。PostgreSQL 列鎖需要 `management_access_state.revision`、`household_memberships.id` 的欄位權限。驗收須包含應用帳號可存草稿及重驗、無法修改食譜庫與來源原文、無法改寫不可變證據或新增發布。工具本身不建立登入角色、不切換連線或啟用管理後台。

遷移失敗時先核對目標庫的版本標記、管理表與舊資料指紋。未開放新庫寫入時，可保持原庫作為回復點或重新從加密備份還原。新庫開始接收寫入後，先停寫、備份新庫並核對增量資料，再決定回切或向前修復。

SQLite／ASGI 測試：`PYTHONPATH=backend:tests python -m pytest tests/test_management_foundation.py tests/test_management_api.py`（Windows 使用分號分隔 PYTHONPATH）。

PostgreSQL 測試是明確 opt-in：使用新建的 `meal_planner_1102_v3_management_test`、`meal_planner_1102_v3_management_legacy_test`、`meal_planner_1102_v3_management_reference_test`，每個 database comment 設為 `overcooked:disposable:management`。`MANAGEMENT_TEST_DATABASE_URL` 指向第一個庫；測試先檢查實際庫名及 marker，然後只重建這三個庫的 public schema。禁止給正式或匯入資料庫加這個標記。`python -m pytest tests/test_management_postgres.py`。

## iCook 食譜匯入

- 開啟獨立管理網站的「食譜與 AI 候選」，選「從 iCook 匯入」，貼上食譜網址並預覽。確認後將原始擷取欄位、來源網址、擷取時間與 HTML SHA-256 一併保存為 `web_capture` 證據。儲存使用原預覽快照，同一網址已有提案時可開啟既有提案。
- 具 `recipe.edit` 的帳號可在草稿編輯人份和食材數量，缺值可先保留空白。iCook 草稿的人份必須是正整數，食材用量須是正數並選擇 g 或 ml。人工補填或更正後，由具 `recipe.review` 的帳號對當前修訂確認並留下依據，再執行驗證及送審。需同一帳號填寫並確認時，該帳號須同時具備兩種權限。正式審核仍依既有分權規則辦理。
- `ml` 保留為體積。食譜發布前仍須補齊有依據的整份烹調前克重。iCook 擷取和草稿儲存由管理人員操作，儲存後可繼續修訂。
- API 位於 `/admin/v1/recipe-candidates/icook`。預覽簽章有效 20 分鐘，綁定發起帳號。單一程序預設使用啟動時產生的金鑰，多 worker 或多副本部署須共用高熵的 `MANAGEMENT_ICOOK_PREVIEW_SECRET`。

## 從家庭生成結果匯入 AI 菜色

- 在「資料管理 → 食譜與 AI 候選」開啟「匯入已生成的 AI 菜色」。目前家庭的擁有者須同時具備食譜編輯與來源證據上傳權限。選擇單道 AI 菜色，核對每份用量及步驟，再按「確認並儲存 AI 草稿」。
- 草稿保存單道菜的名稱、食材、步驟、原始 `grams_per_n` 與生成結果版本。人份與整份烹調前克重留待編輯者確認。同一結果的同一道菜已匯入時可開啟既有提案。
- 管理端讀取既有 T14 結果，確認後將單道菜快照存入管理表的來源證據與提案。家庭問卷、營養、成本和歷史欄位不在匯入資料內。草稿保留原有驗證與審核流程。

## 獨立管理網站

管理介面位於 `admin-dashboard/`，有自己的登入、建置與部署入口；啟動與 hostname 設定見 [管理網站說明](../admin-dashboard/README.md)。一般使用者前端不再提供 `/admin` 頁面或管理 API 代理。後端仍沿用 `/admin/v1` 與既有授權／審核／發布契約，不新增管理資料庫或自動核准。
