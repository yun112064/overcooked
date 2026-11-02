# 1102_V3 驗證紀錄

執行日期：2026-11-03（Asia/Taipei）。基底 `007e51dff66069195544468c0ba5f7020cafd303`，測試對象為本次 `1102_V3` 工作樹；最終整合 commit 以該分支 Git log 為準。沒有引用 V1 歷史測試數字作為本次通過證據。

## 結果

| 檢查 | 狀態／實際結果 | 證據與限制 |
|---|---|---|
| 遠端分支／逐檔結構盤點 | PASS，15 個來源分支 | `branch-audit.json`；排除 t11-t20-demo |
| 原資料與 importer 凍結 | PASS，28 個既有檔案未改 | `source-freeze.json`；binary 原位元組及 text LF 正規化比對 |
| Python 語法 | PASS | `python -m compileall -q backend migrations knowledge_rag nutrition_planning nutrition_target nutrition_model scripts tests` |
| Alembic 圖與 ORM | PASS（離線結構） | 單一 head `c1e7a4b9d260`；29 張 ORM 表編譯 PostgreSQL DDL，另有 3 張 T02 migration-only 表；`schema-inventory.json` |
| Docker Compose 設定 | PASS（僅 config） | `POSTGRES_PASSWORD` 使用合成 placeholder；`docker compose config --quiet`；沒有啟動 Docker／建立 DB |
| PowerShell 開發入口語法 | PASS | PowerShell AST Parser 檢查 `scripts/start-dev.ps1`，0 errors；沒有執行啟動腳本 |
| 前端依賴／lint／build | PASS | `npm --prefix frontend ci --ignore-scripts --no-audit --no-fund`、`run lint`、`run build`；`npm-ci.log`、`frontend-lint.log`、`frontend-build.log`。保留 bundle size 提示，未作效能驗收 |
| T01–T13 架構回歸選集 | PASS，**313 passed／163 skipped／8 deselected** | `pytest-architecture.log`；包含身份、問卷、工作、T11、T12、T13、T03／T04、synthetic SQLite／workbook；不代表 PostgreSQL 與正式資料通過 |
| 完整 Python 測試 | **FAIL，545 passed／251 skipped／47 failed** | `pytest-full.log`、`pytest-full.xml`；沒有將失敗測試改成略過。逐項原因見 `deferred-source-checks.json` |
| PostgreSQL migration／降版／pgvector | NOT_RUN | 按本次資料暫緩範圍不啟動或遷移 DB；SQLite 不足以驗證 PG 特定 SQL |
| 真實食譜／營養／價格／知識資料統一 | NOT_RUN | 原資料保留，候選資料未引入；不是資料內容已整合的證據 |
| 真實 YOLO／模型／價格網站／RAG／端到端 | NOT_RUN | 沒有模型載入、外部請求、worker／scheduler／UI 端到端實測 |

## 架構選集的範圍

實際命令使用以下檔案／目錄（同一個 pytest run），並排除 `NameVariantTests`、`SeasoningTests`、`test_embedding_model_is_priced`、`test_v2_default_t03_rule_matches_frozen_040_digest` 共 8 項：

```text
tests/test_v3_contracts.py
tests/test_ac26_privacy.py tests/test_health.py tests/test_logging.py
tests/test_t01_recipe_import.py tests/test_food_workbooks.py
tests/test_t02_pricing.py tests/test_t02_uni_prosperity.py
tests/test_t07_identity.py tests/test_t08_meal_requests.py
tests/test_t09_recognition.py tests/test_t10_jobs.py
tests/test_t11_recipe_retrieval.py tests/test_t11_meal_history.py
tests/test_t11_knowledge_service.py tests/test_t11_knowledge_store.py tests/test_t11_server_contract.py
tests/test_t12_meal_calculation.py tests/test_t13_meal_cost.py
nutrition_target/tests nutrition_model
nutrition_planning/tests/test_acceptance.py nutrition_planning/tests/test_dish_portions.py
nutrition_planning/tests/test_integration.py nutrition_planning/tests/test_meal.py
nutrition_planning/tests/test_targets.py nutrition_planning/tests/test_t04_bridge.py
```

排除項目分別需要尚未引入的異體名稱／調味料資料、更新模型單價，以及未選定的 T03 0.4.0；它們仍在完整測試裡執行並記為失敗。需要 PG 的測試因 `TEST_DATABASE_URL` 未設定而略過，並非已證明可在 PostgreSQL 運作。

## 完整測試為何未全過

47 個失敗不是正式驗收完成；`deferred-source-checks.json` 保留測試 ID、FAIL 狀態、實際錯誤摘要及依賴原因。涉及：

- V1 營養類別零值規則、食譜攝取／重量修正、每日額外營養建議與高鈉門檻。
- 白飯／糙米等主食映射、生熟狀態與已核准克數；這些設定目前刻意為空，計算回傳資料不足。
- 完整 FOOD 映射、調味料已備妥清單、異體名稱對照，以及凍結中的舊匯入器正規化合約。
- T11 過敏群組名單、123 筆候選知識／核准紀錄與其發布完整性。
- 舊模型價表沒有新 embedding 單價，付費呼叫仍拒絕；T03 維持 0.3.0，歷史測試期待 0.4.0。

T11 測試所需的 fake responder／knowledge client 與基底 fixture 已從歷史 T14 測試抽出為 `tests/generation_support.py`，不把整份 T14 測試帶入本次範圍。

其中新 schema 的 FK 父表缺失曾導致 synthetic fixture 出錯，已補足測試用 release 父表與合成識別，未修改凍結的匯入器。新增 V3 邊界測試確認：缺少營養版本時不開 DB connection、指定 import／policy 確實傳入共用引擎、DB 讀取故障與資料不足分開，以及營養預覽必須登入。

## 測試環境

Python 3.12；Node 24.21.0／npm 11.19.0。專案 `.venv` 使用既有 Python 套件及額外安裝的 argon2-cffi，完整 requirements 從零重建尚未驗證。前端依 lockfile 安裝。

測試使用 `DATABASE_URL=postgresql+psycopg://v3_disabled:v3_disabled@127.0.0.1:1/v3_no_database`，`TEST_DATABASE_URL`、模型 key／provider 清空，`PROJECT_DATA_ROOT` 指向 Git ignored `.test-runtime`。測試資料只放暫存檔及 SQLite 記憶體；沒有把真實來源匯入 PostgreSQL。

一般 sandbox 初次執行遇到 Windows TemporaryDirectory 權限／npm DNS 限制；取得工具執行權限後重跑，上表只將成功重跑結果算入。這些環境錯誤沒有被當成程式已通過的證據。

## 交付界線

本機 `1102_V3` 的程式、schema、來源盤點與資料凍結邊界已整理；沒有推送遠端或改動 main。下一步是選定 SOURCES 中的來源版次，再於獨立 PostgreSQL 導入／驗證資料，處理上述 47 項來源相依檢查並完成真實端到端驗證。不能把此分支稱為已可正式使用或所有 T01–T13 已通過驗收。
