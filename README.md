# 今天吃啥：家庭餐食規劃系統

## 1102_V3：T01–T13 架構整合，資料來源暫緩

目前工作分支為 `1102_V3`。已比對 15 個來源分支，排除 `t11-t20-demo`；以 main 為基礎選取可用前後端、工作程序、知識檢索及營養／成本模組。既有資料集和匯入指令保持原版，沒有執行任何匯入或資料庫遷移。

- [分支與架構整合清單](docs/1102_V3/README.md)
- [T01–T13 原資料來源、版本差異及暫緩項目](docs/1102_V3/SOURCES.md)
- [統一資料庫結構與後續資料整合方式](docs/1102_V3/DATABASE.md)
- [此分支實際驗證結果](docs/1102_V3/VALIDATION.md)

**此階段只整合架構；資料內容尚未統一或匯入。** 開發啟動與 Docker 後端只檢查 schema，不再自動執行遷移／食譜 seed；查價 scheduler 預設停用。下方保留的模組介紹屬原 main 文件，現行資料政策以上述 V3 文件為準。

目前整合 T06 系統骨架、T03 營養目標、T04 食譜營養估算，以及 T07 登入與家庭成員、T08 當餐問卷、T10 持久工作與額度控制。各模組的程式、資料與驗收狀態請依各自文件核對。

| 目錄 | 用途 | 文件 |
|---|---|---|
| `nutrition_target/` | T03：個人、餐別及家庭合計營養目標，含規則、測試與交接文件 | [T03 README](nutrition_target/README.md) |
| `nutrition_model/` | T04：TFDA 食材與烹調營養估算 | [T04 README](nutrition_model/README.md) |
| `backend/`、`frontend/` | T06 骨架與日誌；T07 登入／家庭／成員；T08 問卷草稿；T10 背景工作（`app/jobs/`）與付費額度 | 設定見 `.env.example`、`compose.yaml` |
| `config/model_pricing.json` | T10 付費呼叫預留與結算用的模型價格表（未列價模型一律拒絕呼叫） | 正式接入前須依官方價格重新核對 |
| `inference/` | T09 團隊電腦 YOLO26s 推論服務（OpenCV 降噪前處理），權重不進 Git | [推論服務 README](inference/README.md) |
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
- 資料庫容器 `overcooked-dev-db` 不存在就建立（資料存在具名 volume `overcooked-dev-pgdata`）；1102_V3 只檢查 schema，不自動遷移或從工作簿匯入。來源版本確認前不執行開發啟動流程。
- 後端（含 `--reload`）、worker、前端、YOLO 全部在同一個視窗執行，輸出以 `[backend]`、`[frontend]` 等前綴區分；在該視窗按 Ctrl+C 即全部關閉。YOLO 第一次會自動建立 `.venv-yolo`；設 `DEV_SKIP_YOLO=1` 可略過，辨識顯示不可用，仍可手動輸入食材。開發環境不啟動 scheduler，所以不會在本機每天抓取全聯價格。

`.env.dev` 裡的其他設定（例如之後的 API key）會原樣傳給後端與 worker。只有新增獨立程序（例如 T11 的知識 RAG 服務）時，才需要在腳本加一個 `Start-Service-Window`。

## 系統啟動（T06–T10）

`docker compose up -d --build` 的後端先以唯讀 schema gate 檢查既有資料庫，版本不符即停止；不會自動遷移或匯入。`scheduler` 已移入 `maintenance` profile，價格收集預設關閉。1102_V3 此階段僅完成架構驗證，尚未啟動這些服務。

- 工作程序：`python -m app.jobs.worker`，可視負載開多個副本；以短交易與 `SKIP LOCKED` 領取工作。
- 排程程序：`python -m app.jobs.scheduler`，以 PostgreSQL advisory lock 保證只有一個在執行，負責回收逾時工作與每日日誌清理。
- 費用追查：`python -m app.jobs.admin budget` 顯示本月額度帳與未結清呼叫；`python -m app.jobs.admin reconcile <id> <實際金額> <說明>` 依帳單核對結果不明的呼叫。
- 菜單生成（Ticket 14）尚未完成：送出生成會建立工作並明確回報「生成功能尚未開放」，問卷保留可再送出。

需要資料庫的 API 測試以 `TEST_DATABASE_URL` 指定一個可清空的 PostgreSQL（含 pgvector），測試會以 Alembic 重建資料表；未設定時這些測試會略過。

```sh
TEST_DATABASE_URL=postgresql+psycopg://postgres:<密碼>@localhost:5432/meal_planner_test PYTHONPATH=backend python -m unittest discover -s tests -p 'test_*.py' -v
```
