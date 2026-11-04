# T12 基準份量與營養估算

此模組提供可獨立測試的 Python 計算介面及可信後端接線，並非採用交易。輸入包含 T03 合計、完整菜單食材、Food_ID 營養觀測值和 T04 烹調依據。規則、樣品對應及逐列資料不可直接採信 AI 或瀏覽器輸入。`DatabaseMenuResult.to_dict()` 是後端交給前端的穩定結果格式：缺值一律在 `input_issues` 以中文原因呈現，絕不以 0 代替。

`targets_from_t03()` 接受 T03 `aggregate_meal_targets()` 的 `total_n`、`target`、`reference_exact`，以 Fraction 保留循環小數。`align_baseline()` 將每道來源食譜的 `original_servings` 換算為 1n 基準，資料庫模式只調整已審核範圍；AI 模式驗證候選，不捏造資料庫來源。`scale_to_cooking_units()` 按各食材明確提供的克數步長調整；`validate_meal()` 再次計算及分別比較熱量與蛋白質。

`validate_database_menu()` 使用資料庫現行的 `nutrition_imports`、`food_nutrition`、`nutrition_sources`、`nutrition_source_links` 資料：未傳入 `nutrition_import_id` 時，自動選擇 `imported_at` 最新的一批；傳入 ID 僅供重播／測試。Food_ID 是食譜與營養表的主鍵：程式會依食譜生熟、烹調方式與前處理自動選取唯一資料；有多筆相符資料時，預設 LLM 判斷器會收到食譜、完整步驟、食材列和候選資料，只能回傳候選清單中的 `nutrition_id`。呼叫端也可提供 `variant_resolver`，但都必須保存判斷依據與版本。來源樣品 ID 只供追溯與版本核對，不是計算入口或固定 FOOD→樣品對映。同名同狀態的唯一官方平均值優先，原始名稱不明或 LLM 判斷不一致仍阻擋。資料表不存在會回報 `NUTRITION_DATA_UNAVAILABLE`，缺值／歧義則列出原因，均不回退舊 JSON。詳見 [接線與資料來源紀錄](../docs/T12-feature-data-import.md)。

`calculate_nutrition()` 以這批匯入資料所連結、實際納入計算的 TFDA 原始來源每 100g 欄位精確重算。All 彙整可能包含 USDA 或食譜倒算，不能將其數值冒充 TFDA；無可唯一判定的 TFDA 來源時不可計算。必要欄位缺失仍為 None，真實 0 保留，保存匯入版本、nutrition_id、原始樣品 ID、來源列、來源檔雜湊、原始欄位及來源關聯快照。依 2026-10-30 使用者決議，`t04_bridge.py` 可直接接收 T04 已驗證的 LLM 產率／保留係數，不要求逐筆人工核准；除了輸入、樣品與烹調條件，現在還必須綁定相同的匯入版本、營養紀錄及來源 ID。已有食材、油與調味料均須列入菜單，熟食不重複套保留率。

缺必要資料以 `None` 和原因回傳，不用零補齊；明確提供的真實零值仍可使用。必要欄位由可信後端提供，尚未決議的額外必要欄位不可自行視為非必要。`nutrition_passed` 僅代表本模組所配置欄位的營養驗證，不代表可以採用；T03 規則與營養政策未通過時另列 `adoption_blockers`，過敏、預算、指定菜、權限、持久化及最終採用由其他 Ticket 負責。

PR17 的 `recipe_dataset_1017.xlsx` 已人工確認所有食材列的 `weight_g`；T12 直接以 `recipe_ingredients.weight_g` 作為計算克數，原始杯、匙、顆、斤、碗等 `unit` 僅保留顯示與追溯，絕不重新換算或交給 LLM 猜測。`integration.py` 從既有 `recipes`／`recipe_ingredients` 表讀取 T11 選出的實際菜色及全部食材列，呼叫 T03 合計，套用可信後端逐列審核資料、Food_ID 營養觀測及可選的 T04 結果，再執行 T12。資料庫故障、食譜不存在、重量／樣品／係數缺失分開回報；任一列缺必要依據不會悄悄略過。資料庫模式取整失敗時搜尋審核範圍內的離散用量；超過 4096 種組合則回報搜尋上限，不能宣稱已證明無解。

烹調方式採食譜層級：T12 讀取 `recipes.cooking_methods` 及 `recipe_steps.cooking_method`。唯一方式用於熟食觀測選擇與未判定資料的 LLM 估算；多種方式則把完整步驟交給 LLM 判斷。T12 不要求每筆食材另存烹調方式、產率、烹調依據或可調範圍；相符熟食資料直接按已審核克數計算，未判定資料才由 T04／LLM 回傳產率與保留率。沒有個別可調範圍時，食譜克數固定依原份數與總 n 精確縮放，無法達到熱量或蛋白質 ±10% 就明確回報無可行解。

食譜授權、來源取得時間、來源版本、權利驗證與上游合格狀態由食譜匯入／T11 負責；T12 不重複審核或因這些欄位缺少而阻擋。T12 只回報實際營養計算必要資料不足：Food_ID、`weight_g`、原始份數、食譜層級烹調方式，或可對應的每 100g 營養資料。

`nutrition_model/data/food_id_tfda_mapping.json` 與 `ingredient_master.json` 保留供歷史重現。只有明確設定 `nutrition_source='legacy_tfda'` 才會在資料庫接線使用這條舊路徑；不能同時傳入新版選擇或在新版路徑套用舊對照。T14 使用 `validate_database_menu()` 接線時須提供所選匯入資料及既有逐列單位／烹調依據；模型呼叫、價格、過敏、權限、採用交易仍由對應 Ticket 負責。

新增的 SQLite 測試使用 feature/data-import 的實際營養 ORM 結構，測試新版資料表→TFDA 來源→T03／T04→T12 的串接；合成數值不冒充正式匯入資料。分支未包含完整新版 Excel 或匯入後資料庫，目前本機同名 All_Food 檔案的雜湊與該分支紀錄不同，仍待取得新版資料庫／來源檔後做實際資料驗證。PostgreSQL、網站端到端與 G03／G04 完整驗收也不由此測試代替。必要營養欄位的正式採用政策仍需跨 Ticket 對齊。

問卷選白飯時，`validate_database_menu(staple='白飯')` 依 [主食設定](../config/staples.json)加入 FOOD000296，以 Food_ID 自動尋找可用營養資料。設定保留既有樣品與烹調資訊供追溯，但不要求特定樣品 ID；每 1n 160 克起始及 100～250 克審核範圍會與菜色一起縮放，再檢查熱量、蛋白質 ±10%。缺少資料或 LLM 判斷／估算失敗時回報原因，不以零代替。結果的 `staple_snapshot` 保存設定版本、基準、範圍及可得時的最終克數；`recipe_ids` 僅保留實際選中的資料庫菜色。未選主食須明確傳入 `staple='不需要主食'`。前端問卷接線和畫面缺值提示由後續流程負責。

當匯入資料只有未熟製或未判定 TFDA 紀錄時，`validate_database_menu()` 會使用已設定的 LLM provider 交給 T04 估算；呼叫端可用 `cooking_estimator` 明確覆寫它以利測試或替代供應商。T12 會把已驗證的 Food_ID、原始 TFDA 營養、烹調方式、匯入版本、nutrition_id 及 nutrition_source_id 綁在 T04 收據，再由 T12 以 Fraction 重新計算。產率或保留率缺失、格式／範圍錯誤、服務未設定、連線錯誤或必要營養缺失時，結果會帶具體 `InputIssue.message` 並阻止採用；估算結果的 `is_estimated`、假設、參考 ID 與版本會保留。

正式匯入批次可用 `python scripts/audit_t12_food_ids.py --database-url ... --import-id ...` 進行唯讀稽核。報告會列出食譜 Food_ID 總數、沒有營養觀測值的 ID、多筆觀測值與未知生熟狀態；資料庫尚未提供時不會虛構「434 筆已通過」的結果。

若沒有相符熟食資料，Food_ID 的 `未判定` 營養紀錄可作為 LLM 烹調估算的基準。它會保留原始來源狀態並以 raw pipeline 計算產率與保留率；沒有生食／未判定基準、熱量或蛋白質空白、LLM 未回傳產率、回傳格式錯誤或估算後仍缺必要欄位時，會顯示具體原因且不能採用。熟製、即食、加工、調味、醃漬與飲品資料可直接使用；FOOD000281 白米飯列為明確的即食主食資料。

## 2026-11-01 真實資料決議（`nutrition-preview` 目前採用）

以 PR17 真實食譜與 `All_Food_ID_1018.xlsx` 重測後，使用者決定下列規則。`backend/app/services/t12_planning.py` 以 `conversion='factor_one'` 呼叫 `validate_database_menu()`；本文件上方描述的 T04／LLM 熟食估算仍保留在 `conversion='t04'`（預設值，供測試與日後計算其他營養素）。

1. **克數**：計算一律用工作簿已填好的克數。匯入程式在單位不是 g 時把它存在 `source_weight_g`（`weight_status` 為 `unverified_conversion` 或 `source_range`），T12 在 `weight_g` 空白時讀取它；不重新換算、不交給 LLM。工作簿沒有 `is_required` 欄，空白視為必要。
2. **係數 1**：工作簿克數是下鍋前重量，熱量、蛋白質以「生食每 100g 值 × 克數」計算，不估產率或保留率，不呼叫 LLM；來源標示 `conversion_assumption = raw_weight_energy_protein_factor_one`。選擇順序：生食紀錄 → 加工／即食（無特定烹調方式）→ 本身就是熟食的食材（目前只有白飯 FOOD000296）。只有特定方式熟食紀錄（如 USDA 水煮洋蔥）時回報 `nutrition_cooked_only_needs_raw_mapping`；只有食譜反推資料時回報 `nutrition_recipe_derived_only`。
3. **來源**：TFDA 優先；同一變體沒有 TFDA 時，使用唯一的 USDA 來源並標示 `provider=USDA`、`value_basis=usda_fallback_per_100g`。工作簿因「同組已有子項，排除重複官方平均」而未納入平均的 TFDA 官方平均值可以使用；其他排除原因仍阻擋。
4. **生熟狀態**：`cooking_state_kind()` 明列工作簿全部 27 種狀態；以「；」連接的複合值須各部分一致，未知或矛盾一律回報 `nutrition_state_unknown`，不猜測。
5. **營養對照（做法一）**：`config/t12_nutrition_mappings.json` 只供 T12 使用，例如食譜「薑」FOOD001496 的營養改取自 TFDA 老薑 FOOD000359，或在同一 Food_ID 指定 TFDA 樣品（如甘藍平均值 E30001）。**食譜 Food_ID 不變**，T07 禁忌、T11 知識、T13 價格不受影響。只套用 `status=approved` 且有核准人、日期、依據的項目；格式錯誤時回報 `nutrition_mapping_invalid` 而不計算。結果在每個來源保存 `nutrition_mapping` 與版本。可由已填寫的食材問題試算表產生：`python scripts/t12_mapping_from_sheet.py --sheet <xlsx> --all-food <All_Food_ID_1018.xlsx> --approved-by <核准人> --version <新版本>`。
6. **公開資料補充（2026-11-02）**：`config/t12_nutrition_supplements.json` 同樣只供 T12 使用，分兩種：`values` 為 All 表沒有任何 TFDA／USDA 資料的食譜 Food_ID 提供每 100 g 數值（使用者確認的水 = 0，或 USDA FoodData Central 等公開資料，必附來源網址、紀錄編號、查詢日期）；`fill` 只補既有紀錄的空白欄位（例如香油、冰糖的蛋白質確認為 0），不覆寫已有數值。來源分別標示 `provider`（`USER_CONFIRMED`、`USDA` 等）與 `value_basis`（`user_confirmed_per_100g`、`public_supplement_per_100g`），結果的 `nutrition.supplements` 列出使用的版本。對照表優先於補充；格式錯誤時回報 `nutrition_supplement_invalid`。
7. **預覽狀態**：`to_dict()` 新增 `status`（`calculation_incomplete`、`outside_tolerance`、`nutrition_passed`）、`status_message`、`nutrition.baseline_per_serving`、`nutrition.providers`、`nutrition.conversion_assumptions`、`nutrition.nutrition_mappings`。資料不足、未達 ±10%、政策未核准（`adoption_blockers`）三者分開呈現；原有欄位不變。
8. **菜餚份量（2026-11-02）**：原份量無法達到 ±10% 時，`portion_fit.py` 讓每道菜以 0.5–1.0 份（每 0.1 份一級）供應；同一道菜所有食材乘同一係數，比例不變，主食仍用已核准範圍。候選依「改動最少」排序，並再經 `validate_meal()` 精確驗證，±10% 標準不變。結果的 `dish_portions` 記錄版本與各菜係數，`baseline_per_serving` 仍是原份量。半份仍超過範圍的菜照常回報 `outside_tolerance`。
9. **精確克數**：食譜克數除以份數（如 10 g ÷ 3 人份）無法以 0.000001 g 精確表示時，固定食材只允許小於半微克的表示誤差；以烹調單位改變固定食材的情況仍被拒絕。最終 ±10% 以取整後克數計算。

驗證：`python -m pytest nutrition_planning/tests --cov=nutrition_planning --cov-report=term-missing -q`。開發依賴見 `requirements-dev.txt`；不需呼叫模型或外部 API。

型別檢查：`python -m pyright --project nutrition_planning/pyrightconfig.json`（本機使用專案 `.venv`）。
