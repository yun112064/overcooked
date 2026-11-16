# 今天吃啥：家庭餐食規劃系統

目前整合 T06 系統骨架、T03 營養目標、T04 食譜營養估算，以及 T07 登入與家庭成員、T08 當餐問卷、T10 持久工作與額度控制。各模組的程式、資料與驗收狀態請依各自文件核對。

| 目錄 | 用途 | 文件 |
|---|---|---|
| `nutrition_target/` | T03：個人、餐別及家庭合計營養目標，含規則、測試與交接文件 | [T03 README](nutrition_target/README.md) |
| `nutrition_model/` | T04：TFDA 食材與烹調營養估算 | [T04 README](nutrition_model/README.md) |
| `backend/`、`frontend/` | T06 骨架與日誌；T07 登入／家庭／成員；T08 問卷草稿；T10 背景工作（`app/jobs/`）與付費額度 | 設定見 `.env.example`、`compose.yaml` |
| `config/model_pricing.json` | T10 付費呼叫預留與結算用的模型價格表（未列價模型一律拒絕呼叫） | 正式接入前須依官方價格重新核對 |
| `inference/` | T09 團隊電腦 YOLO26s 推論服務（OpenCV 降噪前處理），權重不進 Git | [推論服務 README](inference/README.md) |
| `knowledge_rag/`、`backend/app/services/recipe_retrieval.py` 等 | T11 結構化食譜查詢、昨日去重、團隊電腦營養知識 RAG（只驗證合成資料，G07 未通過） | [T11 文件](docs/T11-recipe-retrieval-knowledge.md) |
| `backend/app/services/meal_calculation.py` | T12：基準營養對齊、n 倍縮放與整餐 ±10% 營養驗證；輸出整餐食材需求量給 T13 | [T12 說明](docs/T12-meal-calculation.md) |
| `backend/app/services/generation.py`、`llm_client.py` | T14：資料庫菜單優先、查無可行方案自動「AI 生成菜單」、最終食材知識說明；結果存 `generation_results`（依問卷遞增版本） | [T14 說明](docs/T14-generation.md) |
| `backend/app/services/meal_validation.py`、`meal_results.py`、`api/meal_results.py` | T15：結果讀取（全家配方、缺料雙價格、數值營養、知識說明、昨日比對）、R09 採用資格判定、缺價重試／接受例外與缺營養繼續查看的決定紀錄 | [T15 說明](docs/T15-meal-results.md) |
| `backend/app/services/meal_plans.py`、`api/meal_plans.py`、`frontend/src/pages/MealHistoryPage.tsx` | T16：單一交易採用指定結果版本（重驗版本、資格、缺價接受與昨日歷史，冪等）、完整快照與依日期回看已採用餐食紀錄 | [T16 說明](docs/T16-adoption-history.md) |
| `scripts/load/` | T18：五人負載腳本、每月總費用試算、預留防繞過驗證與記錄範本（工具已備；實機驗收尚未執行，G06 未驗證） | [T18 說明](docs/T18-load-and-cost.md) |
| `docs/qa/` | T19：完整使用情境 QA 清單、測試者計時表與缺陷紀錄範本（**尚未由真人執行**，待 T17 實際環境） | [T19 情境](docs/qa/T19-scenarios.md)、[狀態](docs/qa/T19-status.md) |
| `migrations/` | 資料庫遷移 | `alembic.ini` |
| `tests/` | T06 日誌與隱私、T07／T08／T10 API 與資料庫測試；T03 測試已移入模組 | CI 見 `.github/workflows/t06-ci.yml` |
| `docs/` | 跨模組決議及 T04 驗收／交接記錄 | [決議與整合待辦](docs/T04-T06-merge-pending.md) |
| `docs/acceptance/` | T20：AC／Gate 驗收矩陣、操作維護展示交接、限制說明（**MVP 尚未完成**，T18／T19 與正式環境證據待補） | [驗收矩陣](docs/acceptance/matrix.md)、[交接](docs/acceptance/handoff.md)、[限制](docs/acceptance/limitations.md) |

T03 已決議以 Spec 為準，使用版本化規則 `hpa-dri8-mvp-0.3.0` 的目標限制。LLM 烹調係數用途及採用所需的必要營養欄位仍待決議。

**程式碼合併與目錄整理不代表 G03／G04／G07 通過，也不代表主系統 API、容器或營養方案採用驗收完成。** 詳細限制與實際測試記錄見上方文件。

T03 快速驗證，在專案根目錄執行：

```sh
python -m unittest discover -s nutrition_target/tests -t . -v
python -m nutrition_target --rules nutrition_target/rules/hpa-dri8-mvp-0.3.0.json --input nutrition_target/tests/fixtures/member-A.json --operation daily
```

## 本機開發一鍵啟動

需要 Docker Desktop、Python 3.12、Node.js。在專案根目錄雙擊 `start-dev.cmd`（或執行 `scripts\start-dev.ps1`），完成後開啟 http://localhost:5173 ；雙擊 `stop-dev.cmd` 全部關閉（資料庫資料保留）。

腳本每次啟動都會自動同步，平常新增功能不必修改它：

- 第一次執行時由 `.env.dev.example` 建立 `.env.dev`，密碼與 token 隨機產生；YOLO 權重等路徑請依自己的電腦修改。
- 沒有 `.venv` 就自動建立；`requirements.txt` 或 `package-lock.json` 有變動時重新安裝套件。
- 資料庫容器 `overcooked-dev-db` 不存在就建立（資料存在具名 volume `overcooked-dev-pgdata`），每次都會執行 `alembic upgrade head`；資料庫還沒有食譜時，自動從 `data/recipe_dataset_1027.xlsx` 匯入。
- 後端（含 `--reload`）、worker、前端、YOLO 全部在同一個視窗執行，輸出以 `[backend]`、`[frontend]` 等前綴區分；在該視窗按 Ctrl+C 即全部關閉。YOLO 第一次會自動建立 `.venv-yolo`；設 `DEV_SKIP_YOLO=1` 可略過，辨識顯示不可用，仍可手動輸入食材。開發環境不啟動 scheduler，所以不會在本機每天抓取全聯價格。

`.env.dev` 裡的其他設定（例如之後的 API key）會原樣傳給後端與 worker。只有新增獨立程序（例如 T11 的知識 RAG 服務）時，才需要在腳本加一個 `Start-Service-Window`。

## 首次啟動匯入資料種子

`data/seed/` 是從開發資料庫匯出、已整理好的資料（不含任何家庭、成員、需求、生成結果、採用紀錄、工作佇列、額度或帳號資料）：

| 檔案 | 內容 |
|---|---|
| `conversion_factor_estimates.json` | 烹調產率／保留率、單位克數、TFDA 樣本對照（含人工對照）、TFDA 空白營養推估；保留 status／source／reviewer 等審核欄位 |
| `knowledge_records.json`、`knowledge_source_articles.json` | 健康醫療網營養知識（現行版本、已核准、分類為營養知識）與對應文章中繼資料；不含向量 |
| `price_collection_runs.json`、`price_quote_snapshots.json` | 全聯、萬家福價格快照 |
| `manifest.json` | 匯出時間、筆數、sha256、alembic 版本、來源與授權註記 |

授權：健康醫療網內容為電話口頭授權、顯示時必須帶出處（決策 #25）；萬家福條款風險由使用者承擔（決策 #26）。repo 僅供團隊使用，不對外散布。

clone 後第一次執行 `start-dev.cmd`（建立資料庫、套用 migration、建立知識庫）之後，在 repo 根目錄的 PowerShell：

```powershell
.\scripts\dev-run.ps1 -m app.jobs.import_seed_data --dry-run   # 先試算會新增幾筆（不寫入）
.\scripts\dev-run.ps1 -m app.jobs.import_seed_data             # 匯入業務庫與知識庫（免費、冪等，重跑不會重複）
.\scripts\dev-run.ps1 -m knowledge_rag.admin index build --version seed-<日期>   # 需後端執行中；經雲端額度付費向量化（約 200 筆）
```

- 冪等：推估以 id、價格批次以 run_key、快照以 run_key＋食材名、知識以 (knowledge_id, 版本) 去重；本機已有的列不覆寫。`--only app|knowledge` 可只匯入其中一個資料庫。
- 匯入前檢查 sha256 與 alembic 版本：資料庫比種子舊時會要求先 `alembic upgrade head`（`start-dev` 會自動執行）。
- 維護者更新種子（唯讀讀取開發資料庫）：`.\scripts\dev-run.ps1 scripts\export_seed_data.py`，確認筆數後 commit `data/seed/`。
- `start-dev` 偵測到推估表為空時會提示上述指令，但不會自動匯入或呼叫付費 API。

## 單機 demo 部署

整套系統放在這台 Windows 電腦（Docker Desktop），經 Cloudflare 快速通道對外，不需網域與 Cloudflare 帳號。在 repo 根目錄的 PowerShell 執行 `.\scripts\demo-up.ps1`：第一次會產生 `.env.demo`（隨機密碼與內部權杖，`OPENAI_API_KEY` 從 `.env.dev` 帶入；不進 Git）、建置映像、套用 migration、匯入食譜與資料種子、在主機啟動 YOLO（沿用 `start-dev` 建好的 `.venv-yolo`，沒有就略過）、建立知識索引，最後印出 `https://<隨機>.trycloudflare.com` 網址。`-SkipYolo`、`-SkipKnowledgeIndex` 可略過對應步驟，`-NoTunnel` 只在本機啟動、不開對外網址。關閉用 `.\scripts\demo-down.ps1`（資料保留在 `overcooked-demo_*` volume；`-Purge` 才刪除，會要求確認）。注意：網址每次執行 `demo-up` 都會變，要重新分享；電腦關機、睡眠或執行 `demo-down` 即離線（重開機後不會自動上線，要再執行 `demo-up`）。主機不開任何連入埠，只有前端經 cloudflared 對外，`/api/internal/*` 對外一律 404；demo 與開發環境（`overcooked-dev-db`）資料完全分開。費用：快速通道免費；第一次建知識索引約 NT$0.1，之後只有使用者觸發的菜單生成與知識查詢會呼叫 OpenAI（一律經 T10 額度預留）。


### 場景式前端（frontend-3d）移植說明

demo 與 compose 的 `frontend` 服務改為建置 `./frontend-3d`（場景式偽 3D 前端）；舊版 `frontend/` 保留在 repo，但不再建置。本機開發：`frontend-3d` 在 http://localhost:5173 （`npm run dev`；`npm run dev:fake` 使用假資料 API），模擬超市 `sim-supermarket` 在 http://localhost:5174 。驗證狀態與尚未執行的項目見 [DEMO-3D-VALIDATION.md](DEMO-3D-VALIDATION.md)；**下列功能只做過離線測試，尚未在真實資料庫或容器上執行。**

新增的後端端點（A–G）：

- A 資料庫菜單：`POST /meal-requests/{id}/nutrition-preview` 預覽食譜庫選菜的營養；採用沿用 `POST /meal-plans`（帶 `recipe_ids` 時改走預覽採用），`/meal-plans` 摘要多了方案欄位。
- B 換一份：`POST /meal-requests/{id}/menu-rejections` 記錄被換掉的菜（204）。
- C 小知識：`POST /meal-requests/{id}/knowledge-tips` 依菜單食材產生營養小知識。
- D T04 營養證據：`POST /meal-requests/{id}/t04-data`（**預設關閉**，見下方）。
- E 模擬超市：`POST /sim-market/handoffs`、`POST /sim-market/checkout`、`GET /sim-market/catalog`。
- F 飲食小幫手：`POST /assistant/chat`（SSE）、`POST /assistant/recipes`；OpenAI 版經 `/internal/assistant/*`（對外 404）。
- G 結果補充：`prices.unquantified_items`、`dishes[].cover_url` 與 `GET /recipe-covers/{release_id}/{recipe_id}`、`volume_ml`。

與 main 分支刻意不同之處：

- 預覽以 DEMO 原生轉接層（`backend/app/services/t12_planning.py`，建在 DEMO 的 `calculate_meal` 上）實作 main 的 HTTP 合約；每人份量依各成員熱量目標占比分配（不是 main 的 portion_fit），並另外套用 DEMO 原本只在生成工作中檢查的主食過敏／飲食衝突與主食營養防護。
- 沒有容量計價：`volume_ml` 只是透傳，一律為 `null`。
- 模擬超市沒有真實結帳（無 `/sim-market/orders`、無 `sim_market_orders`）；目錄識別固定為 `recipe_dataset_1027`。
- 小知識使用 DEMO 自己的知識服務，並以 v1 關鍵字清單（`config/nutrition_keywords.json`）作為依據；只出現在 v2 清單的關鍵字所產生的小知識會被捨棄（不顯示沒有依據的內容）。
- 過敏原群組排除只套用在 `/assistant/recipes`；菜單生成的限制條件未擴充（較保守的方向，但兩邊不一致）。
- 有未定量食材的結果，總價與營養總量顯示為未知（`null`、`price_complete: false`），不當成 0。

**T04 營養證據端點預設關閉。** `GENERATION_RECIPE_RELEASE_ID`、`GENERATION_NUTRITION_IMPORT_ID`、`GENERATION_FOOD_MAPPING_VERSION` 三個值預設留空，此時 `POST /t04-data` 一律回 422 `NUTRITION_VERSION_REQUIRED`（畫面顯示為提醒）。`release_data/nutrition-v1` 隨附的版本（`recipe-1103-v3-dd4f75c13772` 等，見 `.env.example` 註解）綁定的是 `recipe_dataset_1103_V3.xlsx` 工作簿，不是 demo 使用的 `recipe_dataset_1027`，因此只能在使用者決定綁定方式後，才在 `.env.demo` 填入三個值並重建 backend。啟用後回應會標示 `recipe_source: recipe_dataset_1027`、`binding: food_id_only`（只依 food_id 對應，不代表食譜版本相符）、`weight_basis: null`（weight_g 不視為已確認的整份烹調前重量）。注意：載入器以 `active = false` 寫入 bundle 的食譜發布版本，而服務要求它為啟用，所以**只填這三個值仍會回 422 `RECIPE_NUTRITION_RELEASE_MISMATCH`**；把那筆紀錄改為啟用等於宣稱它描述 demo 食譜，必須由使用者決定（或另建 demo 專用發布版本）。

新的設定鍵與 demo 參數：

- `.env.demo` 舊檔由 `python -m ops.demo ensure-keys` 只補上缺少的鍵（既有值逐位元組保留，只印鍵名）：`SIM_MARKET_URL`、`SIM_MARKET_HANDOFF_KEY`、`ASSISTANT_SERVICE_TOKEN`、`ASSISTANT_DB_PASSWORD`、`ASSISTANT_DATA_DIR`、`ASSISTANT_RERANKER_MODEL_DIR`、三個 `GENERATION_*`；`DEMO_BACKUP_ROOT` 可選。開發用的鍵見 `.env.example`、`.env.dev.example`（`KNOWLEDGE_EXPLAIN_*`、`ASSISTANT_*`、`RAG_*` 等）。
- `demo-up.ps1` 新增 `-SkipMarket`（不啟動模擬超市與其通道）、`-SkipAssistant`（不建置、不啟動飲食小幫手）、`-BackupRoot <目錄>`。
- `SIM_MARKET_URL` 每次 `demo-up` 都會先清空，再寫入新的第二條快速通道網址；留空時（`-SkipMarket`、`-NoTunnel` 或通道沒起來）超市交接回 503 `MARKET_UNAVAILABLE`，不會發出失效連結。
- 小幫手的兩個主機目錄未設定時預設指向不存在的路徑，`up` 會失敗而不會把 repo 掛成資料目錄。

飲食小幫手前提（缺任一項時小幫手顯示不可用）：

- 語料放在 `ASSISTANT_DATA_DIR`（Data Root 之下、Git 之外）；建立語料需由操作者自行執行爬取（`scripts/assistant-data-demo.ps1`），demo-up 不會爬取。
- 本機 Ollama 需有 `bge-m3`、`embeddinggemma`、`qwen3.5:9b`；重排模型 `bge-reranker-v2-m3` 權重放在 `ASSISTANT_RERANKER_MODEL_DIR`。
- OpenAI 版回答只經後端 T10 額度控制呼叫。
- 回答一律顯示出處（健康醫療網、Hello醫師），只使用檢索到的參考內容，不做診斷、不開處方、不給劑量。

資料庫 migration：

- 只新增一個 revision `e1115demo3d01`（接在 DEMO 的 `d4e8a2c7f913` 之後，不改 T01／T02 資料表）；revision 編號與 main 分支不同，日後改用 main 的鏈需手動 stamp。
- 既有 volume 的資料庫版本落後時，`demo-up` 先以 `ops.backup` 做部署前備份並驗證，失敗即中止，之後才 `alembic upgrade head`。
- **絕對不要在有資料的 volume 上執行 `alembic downgrade`**：這個 revision 的 downgrade 會無條件刪除已載入的 T04 營養證據、食譜封面與換菜紀錄。出錯時以部署前備份還原（`ops.restore`）到新資料庫。
- 在 CI 或經授權的拋棄式測試資料庫跑過這個 migration 之前，不要對現有 demo volume 執行 `demo-up`。
- `demo-down -Purge` 也會刪除飲食小幫手的索引 volume（之後要重新向量化）；語料主機目錄不受影響。

未知的營養值或價格一律保持未知，絕不補 0。

## 系統啟動（T06–T10）

`docker compose up -d --build` 會啟動資料庫、後端 API（啟動時自動執行 Alembic 遷移）、生成工作程序 `worker`、單一排程程序 `scheduler` 與前端。先依 `.env.example` 建立 `.env`，至少設定 `POSTGRES_PASSWORD`。

- 工作程序：`python -m app.jobs.worker`，可視負載開多個副本；以短交易與 `SKIP LOCKED` 領取工作。
- 排程程序：`python -m app.jobs.scheduler`，以 PostgreSQL advisory lock 保證只有一個在執行，負責回收逾時工作與每日日誌清理。
- 費用追查：`python -m app.jobs.admin budget` 顯示本月額度帳與未結清呼叫；`python -m app.jobs.admin reconcile <id> <實際金額> <說明>` 依帳單核對結果不明的呼叫。
- 菜單生成（T14）：worker 先組資料庫菜單，查無可行方案自動改由 OpenAI（`gpt-5.4-mini-2026-03-17`，每筆呼叫經額度預留／結算）生成並標示「AI 生成菜單」；未設定 `OPENAI_API_KEY` 時資料庫菜單照常，AI 回退會明確失敗。詳見 [T14 說明](docs/T14-generation.md)。

需要資料庫的 API 測試以 `TEST_DATABASE_URL` 指定一個可清空的 PostgreSQL（含 pgvector），測試會以 Alembic 重建資料表；未設定時這些測試會略過。

```sh
TEST_DATABASE_URL=postgresql+psycopg://postgres:<密碼>@localhost:5432/meal_planner_test PYTHONPATH=backend python -m unittest discover -s tests -p 'test_*.py' -v
```

Windows 搭配 Docker Desktop 時主機請寫 `127.0.0.1`，不要寫 `localhost`：`localhost` 會先解析成 IPv6 `::1`，Docker Desktop 在 `::1` 上接受連線卻不完成 PostgreSQL 握手，測試會在連線時無限等待。
