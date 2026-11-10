# 今天吃啥：家庭餐食規劃系統

目前整合 T06 系統骨架、T03 營養目標、T04 食譜營養估算，以及 T07 登入與家庭成員、T08 當餐問卷、T10 持久工作與額度控制。各模組的程式、資料與驗收狀態請依各自文件核對。

## 交付摘要

這次交付涵蓋食譜候選審核、價格版本管理與 T13 成本接線，以及管理 Dashboard。三個分支依序相依，請先看共用管理與食譜流程，再看價格與 T13，最後看 Dashboard。

### 功能與審查順序

| 順序 | 分支 | 完成功能與審查重點 |
|---|---|---|
| 1 | `T01_V4` | 共用管理權限、來源證據、修訂、驗證、內容與來源權利分開審核，以及不可變發布。T01 可匯入本機 JSON 食譜與 AI 候選樣本，清理文字與數值，保留原始證據、批次／份量語意與未知值，並提出重複候選供人工判斷。審查權限、遷移與候選生命週期。[main → T01 差異](https://github.com/yun112064/overcooked/compare/main...T01_V4) |
| 2 | `T02_V4` | 保留原始價格觀察，支援樣本取得、商品對應、人工價格提案與審核。價格發布與啟用分開，已發布人工價可指名替換並重新審核，也可回復至曾啟用的版本。T13 成本與預算下限優先使用符合食材版本、條件及效期的已啟用人工價。審查價格來源、有效期、人工價優先及歷史快照保護。[T01 → T02 差異](https://github.com/yun112064/overcooked/compare/T01_V4...T02_V4) |
| 3 | `Admin_dashboard_V2` | `/admin` 提供食譜與 AI 候選審核、食譜／食材修訂、價格版本選單、原始商品全名、價格啟用與替換、任務與連線狀態，以及標明可操作範圍的資料清冊。審查角色可見範圍、審核操作和未知／未設定狀態的顯示。[T02 → Dashboard 差異](https://github.com/yun112064/overcooked/compare/T02_V4...Admin_dashboard_V2) |

### 實際驗收結果

| 驗收對象 | 已執行結果 |
|---|---|
| T01 `ad404de` | 59 項測試通過，涵蓋管理權限、遷移、候選流程與 T01 匯入／版本回歸。 |
| T02 `68b2ed6` | 105 項測試及 25 個子測試通過，涵蓋價格版本、人工價優先與 T13 回歸。1 項歷史價格發布測試因缺少選用的歷史價格資料包而略過。 |
| Dashboard `e066f49` | 41 項後端回歸測試通過，前端 lint 與建置通過。 |
| 整合瀏覽器驗收 | 隔離環境的 Chromium 流程 12 項通過。涵蓋登入與拒絕未授權使用者、候選匯入與去重、食譜及食材修訂、分權審核與發布、價格啟用後 T13 採用人工價、人工價替換及版本回復、任務與清冊。前後比對歷史已採用餐單、原始報價及舊食譜的完整資料列雜湊，結果一致。 |

以上為既有隔離驗收結果。本次文件更新僅改動 README。正式 V3 資料、T04 核准、真實爬蟲與付費模型均未納入此次交付驗收。管理功能與 T13 管理價格預設關閉。舊 Admin 資料庫的正式升級仍需另行評估。測試環境與管理 API 說明見 [共用管理骨架](docs/management-foundation.md)。

## 專案模組

| 目錄 | 用途 | 文件 |
|---|---|---|
| `nutrition_target/` | T03：個人、餐別及家庭合計營養目標，含規則、測試與交接文件 | [T03 README](nutrition_target/README.md) |
| `nutrition_model/` | T04：TFDA 食材與烹調營養估算 | [T04 README](nutrition_model/README.md) |
| `nutrition_planning/` | T12：目標對齊、資料庫食譜接線、烹調營養驗證與缺值回報 | [T12 README](nutrition_planning/README.md) |
| `backend/`、`frontend/` | T06 骨架與日誌；T07 登入／家庭／成員；T08 問卷草稿；T10 背景工作（`app/jobs/`）與付費額度 | 設定見 `.env.example`、`compose.yaml` |
| `config/model_pricing.json` | T10 付費呼叫預留與結算用的模型價格表（未列價模型一律拒絕呼叫） | 正式接入前須依官方價格重新核對 |
| `inference/` | T09 團隊電腦 YOLO26s 推論服務（OpenCV 降噪前處理），權重不進 Git | [推論服務 README](inference/README.md) |
| `knowledge_rag/` | T11：結構化食譜查詢、昨日去重、地端營養知識檢索與Excel／JSON維護 | [T11 README](knowledge_rag/README.md) |
| `assistant_rag/` | R14／T21：獨立的飲食客服 RAG（健康醫療網＋Hello醫師、六種檢索方法比較），與 T11 資料分開 | [飲食客服 README](assistant_rag/README.md) |
| `migrations/` | 資料庫遷移 | `alembic.ini` |
| `tests/` | T06 日誌與隱私、T07／T08／T10 API 與資料庫測試；T03 測試已移入模組 | CI 見 `.github/workflows/t06-ci.yml` |
| `docs/` | 跨模組決議及 T04 驗收／交接記錄 | [決議與整合待辦](docs/T04-T06-merge-pending.md) |

T11知識維護資料已更新為123筆網頁／論文來源（2026-10-30），食材ID依提供的標準表A／C欄。逐筆來源內容核對123筆符合使用者準則（含接受Hello醫師與健康醫療網的平台來源）；尚未匯入正式庫或發布索引，詳見上方T11 README。

T03 預設沿用 main 的 `hpa-dri8-mvp-0.4.1`；舊 `0.3.0` 規則仍可由 `NUTRITION_RULES_PATH` 明確指定。main 的資料庫選單預覽／換一份／採用及雲端知識說明，與 V3 的正式生成、批次查價、補價、結果決策及採用快照並存。

V3 正式流程仍綁定明確的食譜、營養匯入與 mapping 版本，只使用核准對照和烹調憑據。`config/v3_staples.json` 與 `config/v3_nutrient_category_zero.json` 保留待審設定；main 的預覽設定不會替 V3 核准資料。四人真實案例目前仍停於 `NUTRITION_INCOMPLETE`。詳細檢查與限制見 [V3 驗證紀錄](docs/1102_V3/VALIDATION.md)。

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
- 開發環境預設使用專用容器 `overcooked-1102-v3-db`、資料庫 `meal_planner_1102_v3`、本機連接埠 `55435`。沿用既有容器時，`.env.dev` 的密碼須與該容器相符。腳本只檢查 schema；目前程式要求 Alembic head `d1105merge1`。
- 新環境請依 `.env.example` 和 `.env.dev.example` 準備本機設定，並依 [資料庫整合規格](docs/1102_V3/DATABASE.md) 完成專用 V3 的遷移與來源安裝。1103_V3 工作簿與資料包另行提供。匯入前先核對發行清單與檔案 SHA-256；既有 active release 可直接使用庫內資料。
- 後端（Windows 隔離程序與檔案變更重啟）、worker、前端、YOLO 全部在同一個視窗執行，輸出以 `[backend]`、`[frontend]` 等前綴區分；在該視窗按 Ctrl+C 即全部關閉。YOLO 第一次會自動建立 `.venv-yolo`；設 `DEV_SKIP_YOLO=1` 可略過，辨識顯示不可用，仍可手動輸入食材。開發環境不啟動 scheduler，所以不會在本機每天抓取全聯價格。

`.env.dev` 裡的其他設定（例如之後的 API key）會原樣傳給後端與 worker。只有新增獨立程序（例如 T11 的知識 RAG 服務）時，才需要在腳本加一個 `Start-Service-Window`。

## 系統啟動（T06–T10）

`docker compose up -d --build` 會啟動資料庫、後端 API（先由 schema gate 確認版本；遷移須於部署前備份後執行）、生成工作程序 `worker`、單一排程程序 `scheduler` 與前端。先依 `.env.example` 建立 `.env`，至少設定 `POSTGRES_PASSWORD`。

- 工作程序：`python -m app.jobs.worker`，可視負載開多個副本；以短交易與 `SKIP LOCKED` 領取工作。
- 排程程序：`python -m app.jobs.scheduler`，以 PostgreSQL advisory lock 保證只有一個在執行，負責回收逾時工作與每日日誌清理。
- 費用追查：`python -m app.jobs.admin budget` 顯示本月額度帳與未結清呼叫；`python -m app.jobs.admin reconcile <id> <實際金額> <說明>` 依帳單核對結果不明的呼叫。
- 正式生成與結果重試使用持久工作程序；缺用量、必要營養、版本對照或主食政策時回報阻擋，問卷與未知值保留。外部服務需另外完成設定。

後端測試統一使用 pytest，同時執行 unittest 類別與 pytest 函式／參數化測試。先安裝 `requirements-dev.txt`。需要資料庫的 API 測試以 `TEST_DATABASE_URL` 指定一個可清空的 PostgreSQL（含 pgvector），測試會以 Alembic 重建資料表；未設定時這些測試會略過。以下命令在專案根目錄執行，`DATABASE_URL` 也指向同一個測試資料庫。

```sh
DATABASE_URL='postgresql+psycopg://postgres:<密碼>@localhost:5432/meal_planner_test' \
TEST_DATABASE_URL='postgresql+psycopg://postgres:<密碼>@localhost:5432/meal_planner_test' \
PYTHONPATH=backend:tests python -m pytest tests -v
```

Windows PowerShell：

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://postgres:<密碼>@localhost:5432/meal_planner_test'
$env:TEST_DATABASE_URL = $env:DATABASE_URL
$env:PYTHONPATH = 'backend;tests'
python -m pytest tests -v
```
