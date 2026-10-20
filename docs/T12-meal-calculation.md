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
- 顯示用量（P4，decisions #21）：`display_amount`／`display_unit`／`display_text`。有原數量＋原單位（非 g）時**一律顯示原單位**
  （即使已有克數；倍率＝整餐克數 ÷ 每 1n 克數，含熱量對齊，沒有克數時用 n）：大匙、小匙、匙、杯、米杯、ml 取到 0.5，
  其他單位照原單位（到 0.01）；原單位是 g 或只有克數 → 克到 1 g；都沒有就顯示原文（例如「胡椒 少許」）。
  克數只用於營養計算，保留在 `whole_meal_grams`／`display_grams` 供追溯。
- 克數來源（decisions #20）：單位 g → 原文克數（`weight_source = "recipe_grams"`）；其他單位 → 工作簿該列 `weight_g`
  （T01 存於 `source_weight_g`，視為生重，`weight_source = "workbook"`，不標示 LLM 推估）；工作簿缺值才用快取的每單位克數推估
  （`weight_source = "unit_estimate"`，人工值優先、rejected 不用）；都沒有 → 未知（`INGREDIENT_UNRESOLVED`）。
  批次推估的單位掃描只看 `weight_g` 與 `source_weight_g` 皆缺的列。
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

## LLM 推估換算係數（decisions #19，2026-10-20）

使用者決定覆蓋 AC13「缺依據不可採用」：缺烹調換算（產率／保留率）或「匙」等單位克數時，改用**事先批次推估並快取**的
LLM 係數，推估後可採用。本節取代上方「已知限制」中「不使用 LLM 烹調係數」與「『匙』等未審核單位不可採用」兩點，
並新增 migration `e8b2d5f7a1c3`（上方「不新增資料表」只適用原 T12）。

- 模型：`gpt-5.4-mini-2026-03-17`（`settings.generation_model`），經 `services/llm_client.OpenAIResponder`（Responses API＋
  Structured Outputs strict）與 T10 `spend.paid_call` 預留／結算。取代 T04 Gemini 估算器作為正式路徑；T04 main 的設計不變：
  **LLM 只推估係數，營養由 Python 以 Decimal 計算**。
- 快取表 `conversion_factor_estimates`：
  - `kind = cooking`：鍵 (food_id, 正規化烹調方式)；值 `yield_factor`（熟重／生重）、`energy_retention`、`protein_retention`（0–1）。
    食譜有多種烹調方式時 T12 不猜是哪一種，改以組合鍵（各方式正規化後排序、以 `+` 連接，如 `boil+stir`）推估與查表。
  - `kind = unit`：鍵 (food_id, 原單位)；值 `grams_per_unit`。
  - 每列：`source`（tfda／human／llm_estimated）、`status`（llm_estimated／approved／rejected／overridden）、`model`、
    `prompt_version`、`schema_version`、`input_snapshot`、`assumptions`、`call_key`（對應 `usage_reservations`）、
    `reviewer`／`review_note`／`reviewed_at`、`created_at`。數值為 Numeric（Decimal，4 位小數），另有資料庫 CHECK 範圍。
- 取用優先順序：**TFDA／USDA 實測 > 人工核可／覆寫 > LLM 推估**；同層取最新；`rejected` 永不使用。
  TFDA 實測不入表：只有 T04 以實測算不出熱量或蛋白質時才套快取係數（`make_tfda_calculator(cooking_factors=...)`）。
  - TFDA 有熟樣品但缺產率 → 熟樣品每 100 g × 生重 × 產率；只有生樣品 → 生樣品每 100 g × 生重 × 保留率。
  - 只推估熱量與蛋白質的保留率；其他營養若無實測維持未知（不擋採用、不填 0）。
- 計算時**絕不呼叫 LLM**：`tfda_calculator` 傳給 T04 的估算器一律回報未設定（T04 的 `estimator=None` 會改用環境變數設定的
  Gemini，已排除）。生成工作每次以 `cached_factor_calculator(db)` 載入一次快取；`load_recipe_lines` 以快取的每單位克數
  解出「1 匙」等列的克數（`weight_estimate`）。
- 結果標示：每項食材 `estimates`（kind、source、status、model、prompt／schema 版本、估計 ID、係數值）、`llm_estimated`、
  `estimated_nutrients`；結果層級 `llm_estimates = {used, food_ids, nutrients}`；trace 的 `estimated_factors`、
  `is_estimated`、`nutrition_basis`（如 `raw_tfda_llm_estimated`）。T15 結果 API 加上 `estimate_label = "LLM 推估"`，
  前端在食材旁與營養表上方顯示。人工覆寫值（source=human）不標「LLM 推估」，但仍保留追溯。
- 沒有推估值（批次未跑、無金鑰、推估被拒或駁回）時維持原本行為：不可採用（`INGREDIENT_UNRESOLVED`／`NUTRITION_INCOMPLETE`），未知不填 0。
- 測試：`tests/test_llm_estimation.py`（假 responder，不呼叫真實 OpenAI）。

### 批次推估 runbook

指令在 backend 環境執行（雲端 worker 容器或本機 `PYTHONPATH=backend`），需要 `DATABASE_URL`；實際推估另需 `OPENAI_API_KEY`。

```bash
python -m app.estimation run --dry-run            # 只列出缺口，不呼叫模型、不寫入、不預留額度
python -m app.estimation run                      # 推估全部未處理的缺口（已處理過的鍵會跳過）
python -m app.estimation run --kind unit --limit 20
python -m app.estimation run --refresh            # 重新推估（新增一列；人工值仍優先）
python -m app.estimation list --status llm_estimated
python -m app.estimation approve <id> --reviewer 營養師A [--note ...]
python -m app.estimation reject  <id> --reviewer 營養師A --note 不合理
python -m app.estimation override --kind cooking --food FOOD000100 --key 炒 --yield 0.8 --energy 1 --protein 0.95 --reviewer 營養師A
python -m app.estimation override --kind unit --food FOOD001541 --key 匙 --grams 15 --reviewer 營養師A
```

- 缺口掃描只看合格食譜（`is_eligible`）：烹調缺口＝T04 以實測算不出熱量／蛋白質但有 TFDA 生或熟樣品的食材；
  單位缺口＝沒有克數、有數量與非克單位、且 TFDA 找得到的食材（容量單位與「顆／片」等計數單位都包含）。
  2026-10-20 以真實工作簿（293 道、合格 281 道）dry-run：**389 組**＝烹調 191 組（單一方式 29、多方式組合 162）＋單位 198 組
  （容量單位 94、計數單位 104）。以每次約 1.5k 輸入／0.2k 輸出 token 估算，全部推估約新台幣 30 元內。
- 驗證：Pydantic strict schema；拒答、截斷、格式錯誤、超出合理範圍（產率 0.3–4、保留率 0.5–1、容量單位克數
  ＝毫升數 × 密度 0.2–2 g/ml、其他單位 0.1–3000 g）一律不寫入，列在報告 `failed`，下次執行會再試。
- 冪等：已有任何紀錄（含 rejected）的鍵預設跳過；`--refresh` 才重新推估。
- 額度：每次呼叫一個 `call_key`（`estimation:<kind>:<food>:<key>:<uuid>`），經 `spend.paid_call`。額度不足（`BUDGET_EXHAUSTED`）、
  連線失敗（`PROVIDER_UNREACHABLE`）或可能已計費（`UNCERTAIN_BILLING`）時整批停止，不自動重送；uncertain 預留以
  `python -m app.jobs.admin budget`／`reconcile` 核對。
- 無 `OPENAI_API_KEY`：`run` 以代碼 2 結束並說明，不寫入任何資料（`--dry-run` 不需要金鑰）。
- 推估後建議由營養審核者抽查 `list` 結果，以 `approve`／`reject`／`override` 覆核；重新匯入食譜後再跑一次 `run` 補新缺口。
- 推估係數不是實測：可採用代表「依推估值落在 ±10%」，並非營養驗收（G04）通過。
