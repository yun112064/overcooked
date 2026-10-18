# 今天吃啥：家庭餐食規劃系統

目前整合 T06 系統骨架、T03 營養目標、T04 食譜營養估算，以及 T07 登入與家庭成員、T08 當餐問卷、T10 持久工作與額度控制。各模組的程式、資料與驗收狀態請依各自文件核對。

| 目錄 | 用途 | 文件 |
|---|---|---|
| `nutrition_target/` | T03：個人、餐別及家庭合計營養目標，含規則、測試與交接文件 | [T03 README](nutrition_target/README.md) |
| `nutrition_model/` | T04：TFDA 食材與烹調營養估算 | [T04 README](nutrition_model/README.md) |
| `backend/`、`frontend/` | T06 骨架與日誌；T07 登入／家庭／成員；T08 問卷草稿；T10 背景工作（`app/jobs/`）與付費額度 | 設定見 `.env.example`、`compose.yaml` |
| `config/model_pricing.json` | T10 付費呼叫預留與結算用的模型價格表（未列價模型一律拒絕呼叫） | 正式接入前須依官方價格重新核對 |
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

## 系統啟動（T06–T10）

`docker compose up -d --build` 會啟動資料庫、後端 API（啟動時自動執行 Alembic 遷移）、生成工作程序 `worker`、單一排程程序 `scheduler` 與前端。先依 `.env.example` 建立 `.env`，至少設定 `POSTGRES_PASSWORD`。

- 工作程序：`python -m app.jobs.worker`，可視負載開多個副本；以短交易與 `SKIP LOCKED` 領取工作。
- 排程程序：`python -m app.jobs.scheduler`，以 PostgreSQL advisory lock 保證只有一個在執行，負責回收逾時工作與每日日誌清理。
- 費用追查：`python -m app.jobs.admin budget` 顯示本月額度帳與未結清呼叫；`python -m app.jobs.admin reconcile <id> <實際金額> <說明>` 依帳單核對結果不明的呼叫。
- 菜單生成（Ticket 14）尚未完成：送出生成會建立工作並明確回報「生成功能尚未開放」，問卷保留可再送出。

需要資料庫的 API 測試以 `TEST_DATABASE_URL` 指定一個可清空的 PostgreSQL（含 pgvector），測試會以 Alembic 重建資料表；未設定時這些測試會略過。

```sh
TEST_DATABASE_URL=postgresql+psycopg://postgres:<密碼>@localhost:5432/meal_planner_test PYTHONPATH=backend python -m unittest discover -s tests -p 'test_*.py' -v
```
