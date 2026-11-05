# T01–T13 原資料來源清冊

日期：2026-11-03。以下是從 Git 程式、manifest、歷史交接文件讀到的**既有來源**；不是本次新核准，也未向外部網站重新驗證授權、價格或營養內容。原分支提到的「已核准」僅保留作歷史證據。

## 功能與來源對照

| 功能 | 原資料來源／格式 | 原使用位置／資料表 | 本次處理 |
|---|---|---|---|
| **T01 食譜** | main／PR17 `data/recipe_dataset_1017.xlsx`：食譜、食材克數、步驟、標準食材、來源；`config/recipe_reviews.json`、`recipe_sources.json` 提供審核與來源資訊 | `source_archive`、`ingredient_aliases`、`allergens`、`recipes`、`recipe_steps`、`recipe_ingredients`；`scripts/import_recipes.py` | 保留本機原版，不重新匯入 |
| **T01 食譜後續版** | V1 的 `recipe_data_1027.sql.gz`、`recipe_data_manifest.json` 與 `data/releases/` 統一 SQL；稽核分支 `data/recipe_dataset_1102.xlsx` | V1 `recipe_data_releases`、bootstrap；稽核分支改用 1102 workbook | 只盤點，不複製 SQL／Excel／seed，不決定哪版正式生效 |
| **T02 價格** | 全聯 PX Mart 商品搜尋／商品頁；萬家福 `online.uni-prosperity.com.tw`；商品包裝重量、價格、幣別、觀察時間、效期、地區及促銷條件 | `app/source_adapters/pxmart.py`、`uni_prosperity.py`；`price_collection_runs`、`price_quote_snapshots`、`retailer_product_mappings` | 介面整合；不連網抓價，不匯入歷史快照 |
| **T02／T13 舊價格管線** | T13 分支 `pricing_sources/PXMart_api.py`、`Carrefour_crawler.py`（其程式的 STORE_ORIGIN 實際為萬家福）；零售 CSV／JSON／Excel 前處理 | 舊 `retail_products`、`retail_product_ingredients`、`retail_price_snapshots`；一系列 `import_retail_prices`／`run_pxmart_db_pipeline` | 舊命名不能當作家樂福來源證據；不併入第二套表或執行匯入 |
| **T03 個人／家庭營養目標** | `nutrition_target/rules/hpa-dri8-mvp-0.1.0`／`0.2.0`／`0.3.0.json`，引用國健署 DRI8；參與成員的年齡、體重、活動、目標、餐別及 n 為使用者輸入 | `NutritionTargetService`、members、問卷與目標快照 | 0.3.0 沿用；V1 0.4.0／未成年表及額外每日營養參考先列待選，不自動取新表 |
| **T04 nutrition 數值營養** | `nutrition_model/data/source/食材營養成分表.xlsx`：TFDA、USDA；衍生 `workbook_foods.json`、`ingredient_master.json`、`ingredient_id_master.json`、生／熟 mapping、USDA cooked JSON | 既有 T04 `nutrition_model` JSON catalog 路徑 | 原檔凍結，不重生 JSON，不混作新正式 SQL 資料 |
| **T04 新 FOOD 版營養** | `All_Food_ID_1018.xlsx`、`done.xlsx`、食譜 Excel；All／原始來源與判定 sheets；TFDA／USDA／食譜推導及來源納入排除分開保存 | `nutrition_imports`、`food_nutrition`、`nutrition_sources`、`nutrition_source_links`；原 `nutrition_import.py`／`scripts/import_food_workbooks.py` | 共用 schema 保留；三份檔案版本待核對，不重新匯入 |
| **T04 烹調依據** | `cooking_yields.json`、TFDA 熟食／生食對照、USDA 產率參考；LLM 所估 yield／retention／單位克數與人工映射 | `conversion_factor_estimates`、`tfda_food_mappings`、`nutrient_fill_estimates`、`t04_cooking_receipts` | 保留程式合約，原始觀測與估算分層；不匯入歷史估值、不呼叫模型 |
| **T05 辨識模型** | YOLO26s 權重及 `inference/model_manifest.json`、39 個 manifest 類別、OpenCV medianBlur；歷史 food41 訓練／測試集路徑只在交接資料出現 | `inference/server.py`、`preprocess.py`、`config/recognition_labels.json` | 使用 main 既有檔案；未重新載入模型或取得訓練集。歷史 AC22 不代表本次通過 |
| **T06 系統／日誌** | 環境設定、runtime 日誌、隱私規則，無外部食譜／營養資料集 | API、日誌、Compose、DB session | 整合架構；未讀取實際帳號或正式 log |
| **T07 帳號／家庭／成員** | 使用者註冊與自行填寫；食材字典來自 T01 `ingredient_aliases`；過敏／禁吃／n 與成員資料 | users、sessions、households、household_memberships、members | 不搬移其他環境帳號或健康資料 |
| **T08 當餐問卷** | 當餐參與人、便當、日期、預算、菜數、已有食材種類、指定菜與主食 | meal_requests、meal_request_revisions | 僅程式與資料結構，不匯入既有草稿 |
| **T09 辨識輸入** | 使用者上傳照片、T05 團隊電腦推論與 FOOD 標籤映射 | recognitions、project_data_root 下上傳檔 | 不讀／搬既有照片，不自動載入資料集 |
| **T10 工作／費用** | jobs／idempotency／spend ledger；`config/model_pricing.json` 模型單價、匯率及預算 | generation_jobs、spend_*、付費呼叫紀錄 | 原價格設定保留，新增模型單價待核對；未列價模型維持拒絕呼叫，沒有搬歷史帳 |
| **T11 結構化食譜／昨日去重** | T01 合格食譜、標準菜色 ID／別名、同家庭前一日已採用快照；不使用食譜 embedding | recipe_retrieval、meal_history、adopted_meal_plans | 程式接入；沒有匯入使用者歷史或重寫舊菜色 ID |
| **T11 知識 RAG** | 稽核／t11 分支 `knowledge_rag/sources/All_Food_ID_1018.xlsx` 與 RAG 網站／論文對齊試算表；`candidates/web_paper_2026-10-30.json`、reviews、current-source manifest；V1 健康醫療網文章／知識 seed | 獨立 knowledge_records、source_articles、knowledge_indexes、knowledge_embeddings；本機 Ollama 或另設 OpenAI embedding | 保留服務／審核架構；不載入候選、不繼承資料核准、不向量化、不建或發布索引 |
| **T11 過敏群組** | 稽核分支 `config/t11_allergen_groups.json`，有官方來源說明及人工 FOOD 名單 | `allergen_groups.py` | 只取格式驗證與讀取介面；群組名單暫緩，不以缺檔當作空的核准名單 |
| **T12 營養／份量** | T03 目標＋T01 完整配方＋T04 指定 import／樣品／生熟／烹調依據；主食、食譜重量／攝取修正、營養 mapping／supplements | meal_calculation、nutrition_planning、t12_planning | 引入計算介面，資料與選版分開；T12 營養預覽未指定 import_id 時明確不可用 |
| **T13 缺料／成本** | T01 配方完整克數、T08 已有種類、T02 有效包裝報價；不讀庫存重量 | meal_cost；共用 T02 quote snapshots | 沒有獨立新資料集；同時輸出整包採購與比例成本，未知報價保留未知 |

## 已辨識的版本衝突

1. **1017、1027、1102 食譜不是同一版。** main 的 1017 SHA-256 為 `6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304`。V1 SQL manifest 指向 1027 食譜 hash `e61bbd814682fd9d95e5a3cd705728d0a4c062dcdc211aaa4af62db732d2e1ca`；稽核分支又切到 1102。不能只用檔名或日期較新就替換。
2. **同名 All_Food 不同 hash。** 歷史 feature/data-import 文件記錄 `cb0c0f938a26fdd1258cb05b52e3a460d4f146be259048e55d0b7e63e600a959`；另一份歷史本機檔與 V1 mapping manifest 為 `b1ccb1b9d9e89c559580571af8018608b442919df4136ed61cf737e928a6dd5f`。該文件也記錄 done.xlsx hash `5611c9f74f475570c8538d6972bf3f8219c4a861dbea075f720f02ec4adccd10`。本次沒有把這些不同批次合為一批。
3. **T04 舊 workbook 不能當作任意新 FOOD 版。** 本機 `workbook_manifest.json` 記錄來源 hash `bd7410e9ac80e2df5cd739a4617bda3121ea5be2563787f95a83f1eeeec5a5ee`，TFDA 2213／USDA 372／ingredient_ids 461；這是原 manifest 的數字，未重做正式資料驗收。
4. **V1 unified v2 是候選資料包。** manifest 的 recipe_release 為 `recipe-1027-e61bbd81`、nutrition_import 為 `4b142581d8b6f25447275dc12669b1dedcaa0325135bb9233e707d173089c12b`、mapping_version 為 `tfda-food-mapping-v2-b13a77c1df07`；其 2101 筆 mapping candidates 註明全數 pending_review，legacy mapping 又不符合該 recipe release。不能直接視為全套已可用。
5. **T11 有兩組不同知識資料。** 稽核分支 current-source 記載 123 則／76 食材（103 網站＋20 論文），狀態是 `source_reviewed_not_imported`、DB／index 未更新；V1 seed manifest 則記載 199 則知識＋70 篇文章。它們不應混成同一核准版本。
6. **V1 seed 還含價格與估值。** manifest 記載 8 個收集批次、2279 筆價格快照及 1971 筆 conversion estimates。這些是歷史資料，不是架構啟動必要條件，本次未帶入。
7. **T12 有兩種生熟政策。** V1 正式生成路徑偏向固定 SQL 版次＋烹調收據；後續稽核預覽加入生重熱量／蛋白質 factor_one 及標示 USDA fallback。兩者適用條件須另外選定，本次不自動改成 factor_one，也不套用歷史營養補值。

## 這次刻意暫緩的資料與入口

- V1：`data/recipe_data*`、`data/releases/*`、`data/seed/*`、`data/reviews/*`、`bootstrap_recipe_data.py`、SQL release builder、seed importer 及 category apply 指令。
- 稽核分支：`recipe_dataset_1102.xlsx`、`t11_allergen_groups.json`、`t12_nutrition_mappings.json`、`t12_nutrition_supplements.json`、RAG sources／candidates／reviews／current-source 內容；mapping 試算表轉換指令不執行。
- V1 的 `config/name_variants.json`、`recipe_weight_corrections.json`、`recipe_intake_adjustments.json`、`seasoning_ingredients.json`、新增主食／market cooked／零值／每日營養參考資料；main 已存在的設定維持原版。
- T02／T13 零售更新／補抓／匯入腳本與來源網頁抓取。T13 原分支的另一套 recipe／retail schema 不作為正式表。
- TFDA／USDA 下載或重建、Gemini／OpenAI／Ollama 呼叫、知識 approve／index build／publish、照片／模型／帳號／家庭資料搬移。

下一步需要選定：食譜主版（1017／1027／1102）、與它相符的三份 FOOD 營養來源 hash、價格來源與有效條件、T03 規則版、T04／T12 生熟政策、T11 知識發布版。選定前先保留候選，不以新版覆蓋舊版。
