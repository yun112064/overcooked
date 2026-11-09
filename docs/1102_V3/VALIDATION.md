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

## 2026-11-05 T13_1105 剩餘問題複核

- 台北 10:25 以專用 V3 唯讀交易複核，head `d1104t01vol1`、active recipe release `recipe-1103-v3-dd4f75c13772`，營養映射仍為 2,192 筆待審、0 筆核准。生成結果與採用快照皆 0，429 項需查價食材中 252 項可用、177 項缺可用報價。桌面同名工作簿 SHA-256 仍與 active release 不符。
- 合成 payload 測得 `_prices` 未投影 `unquantified_food_ids`。當已量化食材都有報價時，API 仍可能回傳 `price_complete=true` 和成本金額，價格區無法直接識別未計入的缺克數食材。正式 T12 仍有獨立的缺量阻擋。此輪未修改產品程式或重跑完整測試。

## 2026-11-05 T04 審核、T02 缺價與 T01 版本對照

- 10:39 台北時間以專用 V3 唯讀交易匯出，核對 2,192 筆唯一待審 mapping_id（1,556 個 Food_ID）與 177 個缺可用報價的食材。價格母集是 active 食譜實際使用的 432 個 Food_ID，扣除免計價後為 429；252 項可用。回讀三份 Excel，逐一檢查筆數、唯一識別、下拉選單、回填空白與差異值，並檢視 11 張工作表的渲染預覽。
- 桌面工作簿最後修改時間 11/04 19:45，庫內啟用版於 11/03 18:14 匯入，兩者 SHA-256 不同。依可對照的工作簿欄位逐列比較，扣除 1,029 個在庫內小數六位精度相同的 Excel 浮點尾差，只剩 `RI001056` 食材名稱由「水 各」變為「水」。食譜、步驟、食材列數相同。未匯入桌面版本。
- T13 結果 API 缺克數時回傳待補清單、隱藏整餐總額並保留已知小計。已知成本已超預算時仍顯示超預算。加上回歸案例後，隔離 PostgreSQL 選集 **117 passed**，前端 lint／build 通過。
- 以實際四人問卷和啟用版走正式 T12 驗收，粉漿蛋餅仍有 3 個待審、2 個缺正式映射，暫停於營養驗算。執行 T04 原始來源驗證腳本停在缺 `source-selection.json`，正式 source manifest 也不在本機。已查得 Git 歷史的 434 筆舊對照表，其 catalog hash 與目前檔案不同，未用來覆蓋 V3。庫內白飯有熟飯候選，糙米飯目前只有生糙米候選，正式主食設定仍待定義。未核准映射、未補零、未送生成或採用。

## 2026-11-05 T04 逐筆建議與來源復原

- 從專用 V3 唯讀取得 active T04 manifest 與 SHA-256，由 `00fad2f` 取回 1017 歷史食譜證據及 FOOD1018 原始營養檔。兩份原檔、兩份壓縮檔與 manifest 均重現發布紀錄雜湊。以原檔重新解析，對帳 V3 的 1,996 筆觀測、3,869 筆來源、3,848 筆關聯及 2,192 筆映射身份欄位。來源包內的本機 source-selection 依 active manifest 重建，非原檔復原。
- 逐筆依來源 ID、Food_ID、生熟狀態、烹調方式、前處理、原納入標記、必要數值、來源原名與同條件候選數審查。結果為 **787 建議採用來源、612 建議暫不採用、793 需人工決定**，後者分成 **381 組**，其中 **60 組**涉及啟用食譜。另有 357 個現行食譜 Food_ID 在本批映射中沒有符合上述來源與狀態條件的候選。正式烹調憑據為 0。
- 粉漿蛋餅的低筋麵粉 `A0320202` 建議採用來源。青蔥及生雞蛋仍有多筆品種候選；太白粉有補充候選 `B0720101` 馬鈴薯粉，需確認原料，水缺正式映射。Excel 回讀驗證 2,192 筆建議、381 個唯一待決定群組、空白人工欄、原資料與下拉驗證。檢查新增及修改區域的渲染預覽。
- 原啟用 T01 工作簿雜湊 `dd4f75c1377299fdef858c98d8625cfbdee1e21dd7699f3f78e210048cf0289b`。搜尋桌面、文件、下載、專案資料資料夾與可用 Git 歷史，未找到相符版本。桌面新版與 Git 中同名檔的雜湊均不同。原始 `before.json`／`import-result.json` 也未找到。未執行完整舊版匯入驗證器、未改資料庫的審核狀態。

- 2026-11-05 T13 遠端現況核對。透過 GitHub connector 讀取當下 `main@b0a2f0a92cbde0aff57a8d837a4c175eae8aac7d` 的完整 Git tree（360 項，未截斷）、T13 ticket、spec 及回退提交。T13 ticket 仍在且標示尚未實作，R08／AC14／AC15 規格仍在。`613f737` 回退移除了 `meal_cost.py`、`generation.py`、`price_scope.py` 與 `test_t13_meal_cost.py`，當下遠端 main 未恢復這些檔案。先前以本機 `origin/main@838e6bd` 判斷遠端現況的說法已過時。本輪為遠端唯讀核對，未執行測試或資料庫操作。


## 2026-11-05 PR #25 對齊最新 main 的衝突處理

- 對 `T13_1105@c88617d` 執行 `git merge --no-commit --no-ff origin/main`，對齊 `main@b0a2f0a92cbde0aff57a8d837a4c175eae8aac7d`。推送前再次 fetch，main 未變。保留 V3 被回退的正式生成、T02/T04 版本綁定、T13 批次查價與精確包數、補價／結果決策、快照與封面；整合 main 的選單預覽、換一份、預覽採用、目標規則 0.4.1、七項營養／每人顯示、來源匯入改善、雲端知識說明、Windows 程序重載及 CI 修正。
- 共用 `/meal-plans` 依命令格式處理生成結果採用及預覽採用，皆以家庭→問卷順序鎖定。預覽採用檢查 `adoption_blocked` 及命令雜湊；原正式採用的完整營養、預算、歷史重驗及冪等契約保留。新版歷史畫面可連到正式餐食的營養／採購明細；原 T04 前端回饋、問卷限制、預算下限與取消生成亦保留。
- 新增 `d1105merge1` 合併 schema。兩條已發布採用 migration 對共用欄位／約束作存在檢查；由 main 升級時用原預覽命令重建冪等雜湊，不改既有快照。實測包含空庫升降再升、帶 main 採用快照升到 head、分支降版後保留另一側欄位及重新升版。
- `config/v3_staples.json`、`config/v3_nutrient_category_zero.json` 保留原 V3 的待審內容；main 原設定仍供預覽使用，不能核准 V3 SQL 對照。T03 預設保留 main 的 0.4.1，另以明確指定 0.3.0 重跑舊版數值與精確運算測試。正式 T12 的雙向 ±10% 驗證不變。

實際執行的驗證：

- Python 3.12：`compileall` 覆蓋 backend、migrations、tests、nutrition_target、nutrition_model、nutrition_planning，通過。
- 在確認名稱及來源內容後，僅對 `meal_planner_1102_v3_pr25_merge_test` 設定 `TEST_DATABASE_URL`；`pytest tests` **697 passed、7 skipped**。略過為另需固定 T02 測試庫的 6 項，以及不隨程式散布的本機歷史來源包 1 項。
- 固定 `meal_planner_1102_v3_t02_test` 原有兩筆 `r1/r2`、`P1` 合成報價；先確認只有 legacy 發布、零食譜、無正式發布，才清理此拋棄式測試庫的測試報價並升級。`pytest tests/test_t02_release.py` **17 passed、1 skipped**；僅本機歷史來源包缺檔略過。CI 保留 V3 原有專用 T02 測試庫準備步驟，以及 main 的完整 pytest 與 T12 測試。
- `pytest nutrition_planning/tests nutrition_target/tests` **207 passed**；T04 `unittest discover` **70 passed**；辨識服務契約 **9 passed**。辨識測試初次缺 cv2，於忽略的本機隔離目錄安裝 CI 指定 NumPy 2.5.4／OpenCV 5.0.0.93 後重跑成功，沒有修改全域環境或載入真實 YOLO 模型。
- 前端 `npm run lint`、`npm run build` 通過；保留既有大於 500 KB 的 bundle 提醒。Python 本機依賴、測試紀錄、資料庫密碼、`.env`、Excel 審核表與來源包均不加入 Git。

最新真實資料驗證與界線：

- 當下本機正式 V3 已由另一 `Admin_Dashboard` 工作樹升到 `d1105admin2`。本次對原庫沒有執行 migration、stamp、降版、清理、審核或寫入。以 pg_dump／pg_restore 串流複製到 `meal_planner_1102_v3_pr25_real_test`，293 道食譜保留；在本機隔離 migration overlay 讀入兩份 Admin 遷移，僅對複本加上本 PR 的遷移，保留 `d1105admin2` 與 `d1105merge1` 兩個 head。Admin 檔案沒有加入本 PR，也未修改其工作樹。
- 複本以 `REPEATABLE READ READ ONLY` 讀最新四人草稿（爸爸 65、媽媽 52、兒子 25、女兒 17）。兩個問卷端點可執行，T11 選到 `R0001`；目標規則為 0.4.1。預覽回報 `calculation_incomplete`、`adoption_blocked=true`，含 SQL mapping 待審與來源缺口；正式 T12 回報 `NUTRITION_INCOMPLETE`。SQL 對照仍為 **2,192 待審、0 核准**。未送出 Worker、爬蟲、付費呼叫、正式生成或採用。
- 同一唯讀複本，以來源克數獨立試算 T13，報價 SQL **1 次**，當下採購 **NT$94**、比例成本 **NT$49.05333333333333333333333333**、預算 **NT$200 / OK**。這是最新庫內報價的獨立價格驗證；不是營養已通過的整餐，也不是沿用先前 NT$116 的舊快照。
- 原始 V3 的 Admin schema 不在此 PR 的 migration 圖，因此原庫目前不能直接通過本分支 schema gate；須等待 Admin 分支與本分支另作 schema 整合。PR #25 的 main/V3 合併與兩個已發布起點的隔離驗證已完成，不將原庫版本 stamp 成其他版本來假裝通過。

- 提交前補查：專用 T02 測試庫的 `schema_gate` 為 `ok (d1105merge1)`，`alembic check` 回報沒有新升級操作。暫存後的 API 金鑰掃描與 V3 接線契約 **6 passed**；以 `cr-at-eol` 核對暫存 diff 無空白錯誤（版本化規則的原始 CRLF 依 `.gitattributes` 保留，不改其 SHA-256）。`.env`、`.env.dev`、`.test-runtime`、`.worktrees` 均無被追蹤檔案。

## 2026-11-05 測試時鐘修正（PR #25 合併後）

- 基底為 `main@4fc740783d71f45f60fe712863d7fa9188a7aafa`。PR #25 已先合併，其 main CI [41998410782](https://github.com/yun112064/overcooked/actions/runs/41998410782) 三個工作均成功。
- 獨立 Windows／Docker 驗證曾捕捉到測試 helper 以 PostgreSQL `now()` 寫入的 available_at 比 worker 的 Python 判斷時間晚 0.104–1.591 毫秒，造成 queued 工作未被領取。只將 `GenerationTestCase.run_job()` 與 `T10Tests.make_available()` 改用 `jobs._now() - timedelta(seconds=1)` 並以 SQL 參數寫入；正式 worker 的領取條件及退避邏輯保持原樣。
- Windows／Python 3.12.10、全新 pgvector 0.8.6／PostgreSQL 17.11 容器，建立兩個經核對的 V3 拋棄式測試庫。原 5 項失敗案例各執行 10 輪，**50/50 通過**，使用原測試命令且未載入診斷外掛。
- 完整後端（archive 中排除 2 項需 Git 的測試）**701 passed、1 skipped、153 subtests passed**；兩項 Git 機密／忽略規則測試另以獨立 index 執行，**2 passed**。唯一後端略過原因為未隨程式散布的本機歷史價格來源包。
- T12 **174 passed**、T03 **33 項通過**；Alembic upgrade／current／check、schema gate、database／pgvector health 均通過，head 為 `d1105merge1`。拋棄式容器已清理，未連接既有 V1／V3／Admin 資料庫，也未啟動爬蟲、付費模型或真實 YOLO。

## 2026-11-09 Sim’s Supermarket V1

- `Sim_Supermarket_V1` 以 `main@a5ad10d` 為基底。超市只讀取指定或單一啟用的 T01 發布，目錄依食譜實際使用的 Food ID 去重。包裝及售價採用模擬商品規則。購物車依使用者、家庭、商品發布與餐單分隔，模擬訂單只存在瀏覽器分頁。
- `pytest tests/test_sim_market.py` 使用合成 SQLite，**3 項通過**。涵蓋登入要求、已用食材去重、發布選擇和指定發布缺失時的空目錄。前端的包數、未知版本、配對及購物車隔離邏輯也以合成輸入驗證。
- 前端 `npm run lint` 與 `npm run build` 通過。
- T01 發布、T13 缺料成本、V3 契約與採用餐單回歸共 **33 項通過、13 項略過**。略過項目依賴 PostgreSQL。另用合成 SQLite 與實際餐單 API 確認同家庭可讀精確缺量、未知用量及 T01 版本，其他家庭收到 404，採用資料列雜湊在兩次請求前後相同。
- 本機 Chromium 以正式前端建置和合成 API 驗證 **8 條流程**，涵蓋採用後直達結帳、精確包數、調整後重開與刷新、模擬訂單、家庭隔離、版本不符、版本未知、無缺料及手機端選購。結帳無額外 API 寫入。
- 驗收使用合成資料與本機服務。正式 V3、真實零售查詢、爬蟲與付費模型未參與；手動挑菜的預覽採用流程未接入購物車。

### 同日追加手動挑菜預覽接線

- 食譜庫挑菜完成後先顯示預覽，正式生成可手動送出。預覽採用時以同一筆快照保存整餐缺料與 T01 發布 ID；同一 Food ID 的精確克數先合併，再換算成模擬商品包數。用量未知或目錄無對應商品時，結帳頁另列未加入原因。既有預覽採用紀錄若沒有這份缺料快照，仍可查看歷史。
- 執行 `tests/test_sim_market.py`、`tests/test_meal_plans.py`、`tests/test_t13_meal_cost.py`、`tests/test_v3_contracts.py`，**33 項通過、13 項略過**。新案例以記憶體 SQLite 和 T11／T12 替身檢查採用、精確缺量、同鍵重送、重新開啟、快照固定與跨家庭拒絕。SQLite 以替身略過 PostgreSQL advisory lock，依賴 PostgreSQL 的案例未執行。
- `npm run lint`、`npm run build` 通過。以無頭 Chrome、前端建置與本機合成 API 實測從勾選成員與菜色到預覽、採用、一鍵加入、調整包數、重新整理與模擬訂單。另驗證正式餐單結帳入口資料格式相容、使用者／家庭／餐單隔離與發布不符時封鎖結帳。模擬結帳無額外 API 寫入。測試未連正式資料庫、零售服務或外部模型。

### 同日超市頁面設計第一版

- `npm run lint`、`npm run build` 通過。使用本機合成 API 及無頭 Chrome，在 1440、390 與 320 px 寬度檢查目錄和購物車摘要。桌面摘要跟隨捲動，手機摘要固定在底部導覽上方，所測畫面無水平溢出。
- 用六項合成商品驗證分類、搜尋、清除篩選和新增商品，並重跑手動挑菜至模擬結帳、正式餐單購物車、重新整理、使用者／家庭／餐單隔離及發布不符時封鎖結帳。正式資料庫及外部服務未參與。

### 同日參考 10/10 調整超市版型

- 參考黑白頁首、主題橫幅、桌面左側分類欄與手機展開式分類。素材以內建 imagegen 生成並轉為 WebP，商品照統一標示為類別示意。 [10/10 首頁](https://1010online.com.tw/) · [10/10 分類頁](https://1010online.com.tw/face)
- `npm run lint` 與 `npm run build` 通過。Vite 提醒入口 chunk 約 508 kB，超過預設 500 kB 提示值。
- 無頭 Chrome 搭配本機合成 API 驗證 1440、390、320 px 寬度、瀏覽食材跳點、桌面與手機分類、搜尋、清除篩選、加購和單品結帳按鈕的位置。另通過手動挑菜採用、正式餐單購物車、模擬訂單、重新整理及使用者、家庭、餐單與發布隔離的回歸。測試使用六項合成商品，未連接正式資料庫。

<details>
<summary>圖像素材與生成提示</summary>

- 主題照片 [`sim-market-hero.webp`](../../frontend/src/assets/sim-market-hero.webp)

  > Use case: photorealistic-natural. Create a single original 3:2 landscape photograph for a premium Taiwanese recipe grocery storefront homepage. Editorial food still life, overhead with a slight perspective, artfully spaced napa cabbage, bok choy, earthy shiitake mushrooms, a cluster of small tomatoes and two eggs, with a warm natural off-white linen cloth, on pale warm grey limestone. Fill the frame naturally, with a strong centered grouping and gently visible texture around the edges, cropped produce at a couple edges. Quiet, beautifully tactile, soft sunlight from top left and believable shadows, subtle moss green and muted terracotta accents. High-end magazine photography. No people, no packaging, no words, no brand, no logo, no watermark, no border, no layout or UI elements. The photo will be displayed beside real HTML text.

- 商品類別示意 [`sim-market-categories.webp`](../../frontend/src/assets/sim-market-categories.webp)

  > Use case: product-mockup. One original 1:1 photo contact sheet asset for a Taiwanese grocery website, four exactly equal quadrants in a 2-by-2 grid with no visible borders. A perfectly clean pure white studio seamless background in all four quadrants. Each quadrant is independent and its subject occupies only the central 55 percent of width and height within that quadrant with ample clear white margin, so the four subjects never touch or cross. Top left: two bok choy and a couple of shiitake mushrooms to represent vegetables. Top right: one salmon fillet and two raw prawns on a small white ceramic plate to represent meat and seafood. Bottom left: three brown eggs next to a neatly cut block of firm tofu to represent eggs and soy. Bottom right: a low white ceramic scoop of short-grain brown rice and a tidy small bundle of dry noodles to represent grains. Refined photo-real grocery catalogue images with restrained soft natural shadows, straight clean backgrounds, no hands, no packaging, no cut-off subjects, no text, no labels, no frame, no watermark. The four quadrants must share precisely the same overall dimensions and balanced margins.

</details>
