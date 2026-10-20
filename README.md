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
| `migrations/` | 資料庫遷移 | `alembic.ini` |
| `tests/` | T06 日誌與隱私、T07／T08／T10 API 與資料庫測試；T03 測試已移入模組 | CI 見 `.github/workflows/t06-ci.yml` |
| `docs/` | 跨模組決議及 T04 驗收／交接記錄 | [決議與整合待辦](docs/T04-T06-merge-pending.md) |

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
- 資料庫容器 `overcooked-dev-db` 不存在就建立（資料存在具名 volume `overcooked-dev-pgdata`），每次都會執行 `alembic upgrade head`；資料庫還沒有食譜時，自動從 `data/recipe_dataset_1017.xlsx` 匯入。
- 後端（含 `--reload`）、worker、前端、YOLO 各開一個視窗。YOLO 路徑沒設定時會略過，辨識顯示不可用，仍可手動輸入食材。開發環境不啟動 scheduler，所以不會在本機每天抓取全聯價格。

`.env.dev` 裡的其他設定（例如之後的 API key）會原樣傳給後端與 worker。只有新增獨立程序（例如 T11 的知識 RAG 服務）時，才需要在腳本加一個 `Start-Service-Window`。

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
