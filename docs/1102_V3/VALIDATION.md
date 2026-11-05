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

- 2026-11-05 依使用者要求，於本機 main 工作區將 293 張已審核圖片及來源清單複製到 data/recipe_pic。來源與複本逐張 SHA-256 比對通過。該路徑受 Git ignore 規則涵蓋。本次僅驗證檔案複製。

- 2026-11-05 重讀 `main@838e6bd`。本機 `main` 與現有 `origin/main` 參照一致，與 `origin/1102_V3@e8f6b6b` 的 Git tree 相同。以 Git 讀取 37 個 migration 檔案的 revision 關聯，確認單一程式 head 為 `d1104t02mass1`。追讀 Worker 生成、T04 回饋與預覽、T12 份量、食譜封面及 schema gate。工作區無 `.venv` 執行檔，本輪未執行 Alembic CLI、測試、資料庫檢查或服務啟動。

## 2026-11-05 main 的 T13 實測

- 建立本機 `T13_1105`，起點為本機 `main@ece7acc`。GitHub `main` 查得 `838e6bd`。比對兩版的應用、migration、測試與設定範本，無差異。以 Git archive 隔離 `ece7acc`。Windows tar 無法解出中文檔名，改用 Python tarfile 解壓成功。系統 Python 缺 Argon2，將指定版本安裝到隔離目錄後，FastAPI 應用成功匯入。
- 非 PostgreSQL 選集 **57 passed、34 skipped**。另新建 `meal_planner_1102_v3_t13_1105_test`，確認實際庫名與 public 空表數後才設定 `TEST_DATABASE_URL`。隔離庫執行 `test_t13_meal_cost.py`、`test_t02_pricing.py`、`test_t02_uni_prosperity.py`，**65 passed**。兩輪有重疊測試，結果分列。原 V3 僅做唯讀交易。
- 啟動先前停止的 V3 專用容器。實庫 schema `d1104t01vol1` 與 main 程式 head `d1104t02mass1` 不符。乾淨快照沒有本機 `.env` 或 1103_V3 工作簿。四人問卷可由服務讀取、T03 目標可得，`/recipes` 和 `/nutrition-preview` 對應函式實測均因 `_get_owned` 未定義拋出 `NameError`。直接呼叫 T11 服務選到 `R0001`；正式 T12 回傳 `NUTRITION_INCOMPLETE`，設定版次有 2,192 筆待審、0 筆核准，實庫仍無生成結果。
- T13 以該草稿未驗證的整餐克數與實庫價格試算，整包 NT$116、比例成本 NT$51.98666666666666666666666666，NT$200 預算判定 `OK`，四項需購食材執行 4 次報價查詢。台北 09:59 以 main 設定查價 430 項，其中 252 項可用、178 項缺可用報價，查詢 580 次。缺價分類為 128 項匹配未通過、19 項單位不符、9 項歷史待更新、7 項查無商品、6 項估價、5 項規格無法解析、3 項缺貨或供貨不明、1 項選價門檻未過。main 尚未將 `FOOD000120` 標為烹調用水免查價，故需查價數比先前 V3 工作樹多 1 項。
- 正式載入器仍將 `R0254` 的整批 1,000／500／500 g 載成每 `1n`。精度 10 壓力測試使用雞蛋實庫 300 g 包裝，合成缺量 300.0000001 g 算成 1 包，精確包數為 2。未送出生成、補價、採用，未呼叫外部服務或改動產品程式。本機 V3 容器保持運行，隔離測試庫保留。

## 2026-11-05 T13_1105 整合驗證

- 還原程式 head `d1104t01vol1`，核對實庫 `recipe_ingredients.volume_ml` 為可空 numeric(16,6)，`volume_evidence` 為可空 text。建立 Git 忽略的本機 `.env`／`.env.dev`，`python -m app.core.schema_gate` 通過。實庫沒有執行 migration 或 stamp。隔離測試庫升降版及 `alembic check` 通過。以 `R0016` 實庫列核對估算體積仍顯示「約」且保留克數計算。
- 桌面 `recipe_dataset_1103_V3.xlsx` 的 SHA-256 為 `f9eccf80b73483b6f0ab45c30ef40c565dfe5a8d290d9adf46875482c0a17ec4`，與 active release `recipe-1103-v3-dd4f75c13772` 的來源雜湊不符。以資料庫既有 active release 驗證，工作簿匯入暫停。
- 台北時間 10:12，使用專用 V3 的 `REPEATABLE READ READ ONLY` 交易，對實際四人早餐問卷呼叫兩個 API 的處理函式。食譜查詢選到 `R0001`，跨家庭呼叫為 404。獨立營養預覽回傳 `calculation_incomplete`，含兩筆 `tfda_source_missing_or_ambiguous`。正式 T12 回傳 `NUTRITION_INCOMPLETE`，設定版次仍是 2,192 筆待審、0 筆核准。停在營養驗算，未送出 Worker、補價、採用或歷史流程。
- 同交易用未驗證整餐需求獨立跑 T13，四人採購 NT$116、比例成本 NT$51.98666666666666666666666666，NT$200 預算狀態 `OK`。同批快照比對 `main` 與新分支的 432 個食材，逐筆報價與原因一致，成本評估從 4 次 SQL 降至 1 次。免計價水 `FOOD000120` 從查價範圍移出，429 項需查價中 252 項可用、177 項仍缺。歷史待更新與估價保留不可用狀態。
- 實庫 `R0254` 仍保留批次配方及可選資格，載入成人份時三列均標示 `batch_servings_unresolved`。`R0252` 的 950 人份可正常縮放。雞蛋 300 g 實庫包裝以精度 10、合成需求 300.0000001 g 驗證，修正後為 2 包。
- 隔離庫 T02／T12／T13 **116 passed**，含精確包數與批次讀取、缺價原因、已有食材不查價的回歸。較廣合成選集 **265 passed、37 skipped、7 failed**，同 7 項在隔離 `main` 的 nutrition_planning baseline 重現，為舊映射檔缺失及白飯／糙米飯政策待審。前端 lint／build 通過，有既有 chunk 大小警告。npm 在一般 sandbox 遇 DNS 錯誤，授權後依 lockfile 安裝成功。
