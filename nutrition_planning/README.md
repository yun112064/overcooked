# T12 基準份量與營養估算

此模組提供可獨立測試的 Python 計算介面，並非 HTTP API 或採用交易。輸入由可信任的後端組裝，包含 T03 合計、完整菜單食材、TFDA 對應和經核准的換算依據。規則、樣品對應及證據核准狀態不可直接採信 AI 或瀏覽器輸入。

`targets_from_t03()` 接受 T03 `aggregate_meal_targets()` 的 `total_n`、`target`、`reference_exact`，以 Fraction 保留循環小數。`align_baseline()` 將每道來源食譜的 `original_servings` 換算為 1n 基準，資料庫模式只調整已審核範圍；AI 模式驗證候選，不捏造資料庫來源。`scale_to_cooking_units()` 按各食材明確提供的克數步長調整；`validate_meal()` 再次計算及分別比較熱量與蛋白質。

`calculate_nutrition()` 以 T04 的 `ingredient_master.json` 精確讀取十進位原始值。T04 `total_nutrition_unrounded` 雖未做顯示取整，但目前運算使用 Python float，不能保證精確邊界；因此 T12 在本模組獨立以 Fraction 計算，不將 float 轉換後冒充精確值。樣品必須經 `NutritionPolicy.sample_mappings` 明確對應並有 TFDA 來源及版本。烹調產率、保留率與可食部轉換必須從後端政策的 `approved_evidence` 完整核准紀錄取得，並與樣品、方式相符；不能使用候選自填係數或未核准的 LLM 係數。生熟重量與每項來源保存在結果中，熟食營養不再乘保留率；已有食材、油與調味料均需各自列入菜單。

缺必要資料以 `None` 和原因回傳，不用零補齊；明確提供的真實零值仍可使用。必要欄位由可信後端提供，尚未決議的額外必要欄位不可自行視為非必要。`nutrition_passed` 僅代表本模組所配置欄位的營養驗證，不代表可以採用；T03 規則與營養政策未通過時另列 `adoption_blockers`，過敏、預算、指定菜、權限、持久化及最終採用由其他 Ticket 負責。

目前只接受已知克數與明確克數步長，不推測杯、匙、顆、原份數或未知可食率；無可靠換算時無法產生可驗證的最終方案。現有介面沒有自動從資料庫載入已審核範圍、對應或證據的端到端整合；G03、G04 與必要欄位決議仍須完成後才能正式採用。資料庫模式取整失敗時搜尋審核範圍內的離散用量；超過 4096 種組合則回報搜尋上限，不能宣稱已證明無解。

驗證：`python -m pytest nutrition_planning/tests --cov=nutrition_planning --cov-report=term-missing -q`。開發依賴見 `requirements-dev.txt`；不需呼叫模型或外部 API。
