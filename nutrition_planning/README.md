# T12 基準份量與營養估算

此模組提供可獨立測試的 Python 計算介面及可信後端接線，並非 HTTP API 或採用交易。輸入包含 T03 合計、完整菜單食材、FOOD→TFDA 對應和 T04 烹調依據。規則、樣品對應及逐列資料不可直接採信 AI 或瀏覽器輸入。

`targets_from_t03()` 接受 T03 `aggregate_meal_targets()` 的 `total_n`、`target`、`reference_exact`，以 Fraction 保留循環小數。`align_baseline()` 將每道來源食譜的 `original_servings` 換算為 1n 基準，資料庫模式只調整已審核範圍；AI 模式驗證候選，不捏造資料庫來源。`scale_to_cooking_units()` 按各食材明確提供的克數步長調整；`validate_meal()` 再次計算及分別比較熱量與蛋白質。

`calculate_nutrition()` 以 T04 的 `ingredient_master.json` 精確讀取十進位原始值。T04 `total_nutrition_unrounded` 使用 Python float，不能保證精確邊界；T12 以 Fraction 重算，不將該總值冒充精確值。樣品必須有明確 TFDA 來源及版本。依 2026-10-30 使用者決議，`t04_bridge.py` 可直接接收 T04 已驗證的 LLM 產率／保留係數，不要求逐筆人工核准；仍須核對輸入快照、樣品、料理方式、條件、範圍和參考 ID，保存估算標示、假設及版本。候選自填係數不可用，缺值不可補猜；熟食營養不重複套保留率。已有食材、油與調味料均須列入菜單。

缺必要資料以 `None` 和原因回傳，不用零補齊；明確提供的真實零值仍可使用。必要欄位由可信後端提供，尚未決議的額外必要欄位不可自行視為非必要。`nutrition_passed` 僅代表本模組所配置欄位的營養驗證，不代表可以採用；T03 規則與營養政策未通過時另列 `adoption_blockers`，過敏、預算、指定菜、權限、持久化及最終採用由其他 Ticket 負責。

目前只接受已知克數與明確克數步長，不推測杯、匙、顆、原份數或未知可食率。`integration.py` 從既有 `recipes`／`recipe_ingredients` 表讀取 T11 選出的實際菜色及全部食材列，呼叫 T03 合計，套用可信後端逐列審核資料、FOOD→TFDA 對映及可選的 T04 結果，再執行 T12。資料庫故障、食譜不存在、食材單位／樣品／係數缺失分開回報；任一列缺必要依據不會悄悄略過。資料庫模式取整失敗時搜尋審核範圍內的離散用量；超過 4096 種組合則回報搜尋上限，不能宣稱已證明無解。

`nutrition_model/data/food_id_tfda_mapping.json` 是版本化的 FOOD→TFDA 樣品對照。434 種食譜食材的 FOOD ID 均在 All_Food；93 種依明確名稱、唯一相符官方平均值或既有 T04 明確對映選定樣品，341 種待確認。`nutrition_model.food_id_mapping.load_food_id_mappings()` 會核對目錄雜湊、FOOD ID 與食譜名稱；更新來源資料後須重新產生並審核對照，不可只改 JSON 的狀態。FOOD 身分查得不等於 TFDA 營養可算：樣品狀態不明、重量／單位或必要營養缺失仍須回報並阻擋採用。T14 可使用 `validate_database_menu()` 接線；模型呼叫、價格、過敏、權限、採用交易由對應 Ticket 負責。

接線測試：本機 `nutrition_planning/tests` 67 個通過；型別檢查 0 錯誤。真實 T11 SQLAlchemy 表結構曾以 SQLite 測試讀取及計算；PostgreSQL、正式烹調案例、網站端到端與 G03／G04 完整驗收尚未通過，不能以本模組測試代替。必要營養欄位的正式採用政策仍需跨 Ticket 對齊。

驗證：`python -m pytest nutrition_planning/tests --cov=nutrition_planning --cov-report=term-missing -q`。開發依賴見 `requirements-dev.txt`；不需呼叫模型或外部 API。
