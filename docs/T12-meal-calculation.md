# T12 基準營養對齊、n 倍與烹調營養計算

程式：`backend/app/services/meal_calculation.py`（MealCalculationService）。測試：`tests/test_t12_meal_calculation.py`。
不新增資料表或 migration。

## 公開介面

| 函式 | 用途 |
|---|---|
| `compose_targets([ParticipationBaseline])` | R06：N = Σn、E/P_target = Σ B×n、reference = target ÷ N（Fraction 精確值）。個別 n 限五種選項，合計 N 不限。 |
| `targets_from_t03(aggregate)` | 直接沿用 T03 `aggregate_meal_targets` 的 total_n 與 target。 |
| `load_recipe_lines(db, recipe_id)` | 資料庫模式：T01 食譜全部食材 → 每 1n 基準克數（weight_g ÷ original_servings）與已審核可調範圍。 |
| `calculate_meal(lines, targets, mode=, calculator=)` | 基準對齊 → 乘 N → 整餐重新計算 → ±10% 判定；回傳驗證結果與 `requirements`。 |
| `tfda_calculator` | 以 T04 `nutrition_model`（TFDA，`estimator=None`）計算每項食材營養與追溯資料。 |

`requirements` 為 `list[IngredientRequirement(food_id, grams)]`（`backend/app/meal_calc/types.py`，與 T13 共用）：
整餐、已乘 N、未取整的克數，同一 food_id 跨菜合併。基準未通過或資料不完整時為空清單。

## 規則與決定

- 比較一律用 Fraction／Decimal 精確值，90%、110% 含邊界；`display_grams` 只供畫面（四捨五入到 1 g），驗證與 T13 都用未取整值（decisions #3）。
- 必要營養：熱量、蛋白質（decisions #4）。其他營養只顯示，缺值為 None 並列在 `missing_nutrients`，不擋採用。
- 資料庫模式只調整 `adjustment_status = approved` 的食材，在可調範圍內做格點搜尋（每項最多 41 點，總點數上限 200,000），
  選最接近參考值的用量，再以 calculator 重新計算確認。找不到即回報 `NO_FEASIBLE_BASELINE`，不改目標或容差。
  格點搜尋不保證窮舉所有連續解。
- AI 模式不調整候選用量，只做同等驗證，不要求資料庫來源。
- 阻擋原因代碼：`INGREDIENT_UNRESOLVED`、`NUTRITION_INCOMPLETE`、`NUTRITION_OUT_OF_RANGE`（含 stage baseline／final 與上下界）、`NO_FEASIBLE_BASELINE`。

## 已知限制

- 資料庫未保存各食材的生熟狀態，`load_recipe_lines` 預設 `weight_basis = raw`；烹調方式只在食譜恰有一種時套用。
- 不使用 LLM 烹調係數（P01 未核准）。沒有 TFDA 熟食樣品或實測熟重的生食，營養會是缺值而不可採用；
  「匙」等未審核單位同樣不可採用。真實食譜的可採用覆蓋率因此可能偏低。
- T04 以 float 計算，`tfda_calculator` 以 `Decimal(str(v))` 轉回；邊界的精確性由 calculator 輸出值決定。
- 後端映像需要 `nutrition_model/`（Dockerfile 已複製）；程式會自行把它加入 `sys.path`。
