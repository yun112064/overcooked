# T12 基準份量與營養估算

此模組提供可獨立測試的 Python 計算介面及可信後端接線，並非 HTTP API 或採用交易。輸入包含 T03 合計、完整菜單食材、FOOD→TFDA 對應和 T04 烹調依據。規則、樣品對應及逐列資料不可直接採信 AI 或瀏覽器輸入。

`targets_from_t03()` 接受 T03 `aggregate_meal_targets()` 的 `total_n`、`target`、`reference_exact`，以 Fraction 保留循環小數。`align_baseline()` 將每道來源食譜的 `original_servings` 換算為 1n 基準，資料庫模式只調整已審核範圍；AI 模式驗證候選，不捏造資料庫來源。`scale_to_cooking_units()` 按各食材明確提供的克數步長調整；`validate_meal()` 再次計算及分別比較熱量與蛋白質。

2026-10-31 起，`validate_database_menu()` 預設使用 `feature/data-import` 的 `nutrition_imports`、`food_nutrition`、`nutrition_sources`、`nutrition_source_links`。必須指定 `nutrition_import_id`，並為全部食材列提供 `ImportedNutritionSelection`（營養紀錄 ID、生熟、方式、前處理；有多筆 TFDA 來源時再指定 source_id）。相同 FOOD 可以在不同食材列選擇不同營養紀錄，不再以 FOOD→單一舊樣品表推定全部狀態。資料表不存在會回報 `NUTRITION_DATA_UNAVAILABLE`，缺值／歧義則列出原因，均不回退舊 JSON。詳見 [接線與資料來源紀錄](../docs/T12-feature-data-import.md)。

`calculate_nutrition()` 以這批匯入資料所連結、實際納入計算的 TFDA 原始來源每 100g 欄位精確重算。All 彙整可能包含 USDA 或食譜倒算，不能將其數值冒充 TFDA；無可唯一判定的 TFDA 來源時不可計算。必要欄位缺失仍為 None，真實 0 保留，保存匯入版本、nutrition_id、原始樣品 ID、來源列、來源檔雜湊、原始欄位及來源關聯快照。依 2026-10-30 使用者決議，`t04_bridge.py` 可直接接收 T04 已驗證的 LLM 產率／保留係數，不要求逐筆人工核准；除了輸入、樣品與烹調條件，現在還必須綁定相同的匯入版本、營養紀錄及來源 ID。已有食材、油與調味料均須列入菜單，熟食不重複套保留率。

缺必要資料以 `None` 和原因回傳，不用零補齊；明確提供的真實零值仍可使用。必要欄位由可信後端提供，尚未決議的額外必要欄位不可自行視為非必要。`nutrition_passed` 僅代表本模組所配置欄位的營養驗證，不代表可以採用；T03 規則與營養政策未通過時另列 `adoption_blockers`，過敏、預算、指定菜、權限、持久化及最終採用由其他 Ticket 負責。

目前只接受已知克數與明確克數步長，不推測杯、匙、顆、原份數或未知可食率。`integration.py` 從既有 `recipes`／`recipe_ingredients` 表讀取 T11 選出的實際菜色及全部食材列，呼叫 T03 合計，套用可信後端逐列審核資料、FOOD→TFDA 對映及可選的 T04 結果，再執行 T12。資料庫故障、食譜不存在、食材單位／樣品／係數缺失分開回報；任一列缺必要依據不會悄悄略過。資料庫模式取整失敗時搜尋審核範圍內的離散用量；超過 4096 種組合則回報搜尋上限，不能宣稱已證明無解。

`nutrition_model/data/food_id_tfda_mapping.json` 與 `ingredient_master.json` 保留供歷史重現。只有明確設定 `nutrition_source='legacy_tfda'` 才會在資料庫接線使用這條舊路徑；不能同時傳入新版選擇或在新版路徑套用舊對照。T14 使用 `validate_database_menu()` 接線時須提供所選匯入資料及既有逐列單位／烹調依據；模型呼叫、價格、過敏、權限、採用交易仍由對應 Ticket 負責。

新增的 SQLite 測試使用 feature/data-import 的實際營養 ORM 結構，測試新版資料表→TFDA 來源→T03／T04→T12 的串接；合成數值不冒充正式匯入資料。分支未包含完整新版 Excel 或匯入後資料庫，目前本機同名 All_Food 檔案的雜湊與該分支紀錄不同，仍待取得新版資料庫／來源檔後做實際資料驗證。PostgreSQL、網站端到端與 G03／G04 完整驗收也不由此測試代替。必要營養欄位的正式採用政策仍需跨 Ticket 對齊。

驗證：`python -m pytest nutrition_planning/tests --cov=nutrition_planning --cov-report=term-missing -q`。開發依賴見 `requirements-dev.txt`；不需呼叫模型或外部 API。

型別檢查：`python -m pyright --project nutrition_planning/pyrightconfig.json`（本機使用專案 `.venv`）。
