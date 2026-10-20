# T12 基準營養對齊、n 倍與烹調營養計算

程式：`backend/app/services/meal_calculation.py`（MealCalculationService）。測試：`tests/test_t12_meal_calculation.py`。
不新增資料表或 migration。

## 公開介面

| 函式 | 用途 |
|---|---|
| `compose_targets([ParticipationBaseline])` | R06：N = Σn、E/P_target = Σ B×n、reference = target ÷ N（Fraction 精確值）。個別 n 限五種選項，合計 N 不限。 |
| `targets_from_t03(aggregate)` | 直接沿用 T03 `aggregate_meal_targets` 的 total_n 與 target。 |
| `load_recipe_lines(db, recipe_id)` | 資料庫模式：T01 食譜全部食材 → 每 1n 基準克數（weight_g ÷ original_servings）、已審核可調範圍、原單位與每 1n 原數量（顯示用）、生熟依據。 |
| `calculate_meal(lines, targets, mode=, calculator=)` | 基準對齊 → 乘 N → 整餐重新計算 → ±10% 判定；回傳驗證結果、`requirements`、`quantities_verified`、`suggestions`。 |
| `tfda_calculator` | 以 T04 `nutrition_model`（TFDA，`estimator=None`）取每 100 g 營養，在 T12 內以 Decimal 乘實際克數；附追溯資料。 |
| `display_half(value)` | 顯示用：容量單位取到 0.5。 |

`requirements` 為 `list[IngredientRequirement(food_id, grams)]`（`backend/app/meal_calc/types.py`，與 T13 共用）：
整餐、已乘 N、未取整的克數，同一 food_id 跨菜合併。主食（`role = staple`，decisions #16）不計價格與缺料，不列入；
克數未知的食材無法計算成本，也不列入。

不可採用時仍可查看（P2，2026-10-21）：缺營養、基準無解或部分食材無法換算時，克數已知的食材照樣以基準用量 × N
列出整餐用量並輸出 `requirements`（`quantities_verified = false`，代表未通過驗證），T13 照常計算成本。
`suggestions` 給具體調整方向：超出範圍的營養要增加或減少多少、各食材往哪個方向調、已審核可調範圍內最多能補多少，
`within_adjustable_ranges` 表示單就這項營養而言範圍內是否補得足；缺資料時列出待補的食材。

## 規則與決定

- 比較一律用 Fraction／Decimal 精確值，90%、110% 含邊界；`display_grams` 只供畫面（四捨五入到 1 g），驗證與 T13 都用未取整值（decisions #3）。
- 顯示用量（P4）：`display_amount`／`display_unit`／`display_text`。有克數 → 克到 1 g；只有原單位時，大匙、小匙、匙、杯、米杯、ml
  取到 0.5，其他單位照原單位（到 0.01），都沒有就顯示原文（例如「胡椒 少許」）。
- 精確度（P5）：`tfda_calculator` 不把克數轉 float 送進 T04；每項食材以 100 g 呼叫 T04 取得每 100 g 營養（T04 對重量線性），
  以 `Decimal(str())` 取值後在 T12 內以 Decimal 乘克數。正好 90%／110% 的真實 TFDA 案例有測試。
- 生熟資料規範（P6，decisions #17）：**食譜工作簿的克數一律視為生重**（`weight_basis = raw`，依據 `recipe_workbook_raw_convention`）；
  若資料表日後新增生熟欄位（`weight_basis` 為 raw／cooked），優先採用該欄位（依據 `recipe_weight_basis_field`）。AI 菜由模型逐項標示（`model_declared`），
  主食依 `config/staples.json`（`staple_config`）。依據寫入每項食材的 `weight_basis_source` 與 trace，結果可追溯。
- 必要營養：熱量、蛋白質（decisions #4）。其他營養只顯示，缺值為 None 並列在 `missing_nutrients`，不擋採用。
- 資料庫模式只調整 `adjustment_status = approved` 的食材，在可調範圍內做格點搜尋（每項最多 41 點，總點數上限 200,000），
  選最接近參考值的用量，再以 calculator 重新計算確認。找不到即回報 `NO_FEASIBLE_BASELINE`，不改目標或容差。
  格點搜尋不保證窮舉所有連續解。
- AI 模式不調整候選用量，只做同等驗證，不要求資料庫來源。
- 阻擋原因代碼：`INGREDIENT_UNRESOLVED`、`NUTRITION_INCOMPLETE`、`NUTRITION_OUT_OF_RANGE`（含 stage baseline／final 與上下界）、`NO_FEASIBLE_BASELINE`。

## 已知限制

- 生重是資料規範（decisions #17），不是逐項實測；烹調方式只在食譜恰有一種時套用。
- 不使用 LLM 烹調係數（P01 未核准）。沒有 TFDA 熟食樣品或實測熟重的生食，營養會是缺值而不可採用；
  「匙」等未審核單位同樣不可採用。真實食譜的可採用覆蓋率因此可能偏低。
- T04 內部仍以 float 讀每 100 g 數值；T12 取 `Decimal(str(v))` 後精確相乘，邊界判定不受克數 float 誤差影響。
- 後端映像需要 `nutrition_model/`（Dockerfile 已複製）；程式會自行把它加入 `sys.path`。
