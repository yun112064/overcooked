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
  - 只推估熱量與蛋白質的保留率；其他營養若無實測，依 decisions #40 以生組成、保留率 1 近似（見下方 #40），生組成空白者維持未知（不擋採用、不填 0）。
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

## 食譜食材 → TFDA 樣本對照（decisions #28，2026-10-21）

使用者實測發現多數食譜營養顯示「未知」：T04 `nutrition_model` 只以名稱查 TFDA，食譜用語常對不到（空心菜→蕹菜有 10 筆品系
無平均值、青蔥／蔥對到多筆、食鹽／鹽沒有同名樣本）。新增 migration `b7f2c9e4a8d1`（接在 `e8b2d5f7a1c3` 之後，維持單一 head）。

- 解析順序（`app/services/tfda_mapping.TfdaResolver.resolve(food_id, name)`）：
  1. `exact`：食譜名稱本身由 T04 `find_ingredient` 解得到；
  2. `alias`：工作簿 `ingredient_aliases` 的別名（排除歧義別名）由 T04 解得到；
  3. `synonym`：`config/ingredient_synonyms.json` 已確認（confirmed）群組中其他成員的名稱；
  4. `human`：對照快取中人工核可（approved）或覆寫（overridden）的樣本；
  5. `llm_mapped`：對照快取中未覆核的 gpt-5.4-mini 推薦；
  6. 都沒有 → 維持 `ingredient_not_found`（不填 0）。`rejected` 永不使用；模型回答「無合適樣本」的列只用於批次冪等。
- 對照只決定「用哪一筆 TFDA 樣本」：T12 把該樣本可被 T04 解回的名稱（正式名稱優先，其次別名）交給 T04，營養仍由
  Python 以 Decimal 計算（T04 main 設計）。
- 快取沿用 `conversion_factor_estimates`：`kind = tfda_mapping`、`factor_key = "tfda"`、新欄位 `tfda_sample_id`
  （NULL＝模型回答 NONE），其餘追溯欄位（model、prompt／schema 版本、input_snapshot、call_key、覆核欄位）與 #19 相同。
- 追溯與標示：每項食材 trace `tfda_mapping = {source, sample_id, tfda_name, input_name, via_name, llm_mapped, estimate}`；
  結果列 `tfda_mapping_source`、`tfda_sample_id`、`llm_mapped`；結果層級 `llm_mappings = {used, food_ids}`。T15 結果 API 加
  `mapping_label = "LLM 對照"`、`nutrition.llm_mappings.label`，前端在食材旁與營養表上方顯示「LLM 對照」。
- `cached_factor_calculator(db)` 每個生成工作載入一次別名、同義詞與對照快取；`tfda_calculator`（無資料庫）只用精確名稱。

### #19 烹調換算的修正（同日）

- 多種烹調方式：查表與批次都用組合鍵（`combined_method_key`，如 `boil+stir`），路徑本身正確（測試以假快取值確認大蒜、
  高麗菜、對照後的空心菜都算得出）。
- **修正**：TFDA 狀態為 `unspecified` 的樣本（2213 筆中 1186 筆，如大蒜 E2100101「乾貨」、多數調味料）先前套不上快取係數
  （T12 只接受 state=raw），批次也不會為它們產生烹調缺口；單一烹調方式時 T04 烹調管線更直接回報 `ingredient_not_found`。
  現在 T12 與批次把「非 cooked」的樣本都當作生組成套保留率（`_raw_composition`），與 T04 無烹調方式時採用的樣本一致。
  因此烹調缺口數比 2026-10-20 的 191 組多。

### 對照批次 runbook

```bash
python -m app.estimation run --dry-run                       # 列出全部缺口（對照前），不呼叫模型
python -m app.estimation run --kind tfda_mapping             # 1. 先跑對照
python -m app.estimation run --kind cooking                  # 2. 對照寫入後重新掃描烹調缺口再推估
python -m app.estimation run --kind unit                     # 3. 單位（目前應為 0）
python -m app.estimation run                                 # 或一次全部：自動先對照、再重新掃描 cooking／unit
python -m app.estimation list --kind tfda_mapping
python -m app.estimation override --kind tfda_mapping --food FOOD000509 --key tfda --sample E5600101 --reviewer 營養師A
python -m app.estimation approve|reject <id> --reviewer 營養師A
```

- 缺口：合格食譜中以精確名稱、別名、同義詞都解不到 TFDA 的食材（每個 food_id 一組）。候選以字串相似度
  （完全相同 1.0 > 包含 0.9 > SequenceMatcher 比例，別名權重 0.9，門檻 0.5）從 TFDA 2213 筆縮小到最多 15 筆；
  沒有候選的食材不呼叫模型，列在報告 `no_candidates`。
- 模型：Structured Outputs strict，`tfda_sample_id` 為 enum（候選 ID ＋ `NONE`）；回傳清單外 ID（`CHOICE_NOT_IN_CANDIDATES`）、
  拒答、截斷、格式錯誤一律不寫入。經 T10 `spend.paid_call`（用途 `tfda_mapping_estimation`）。冪等與 `--refresh` 同 #19。
- 2026-10-21 以真實工作簿（合格 281 道）在測試資料庫 dry-run：對照缺口 299 組（送模型 277、無候選 22），
  烹調缺口 592 組（對照前；對照後會再增加），單位 0 組。
- 測試：`tests/test_tfda_mapping.py`（假 responder，不呼叫真實 OpenAI）。

## 必要營養缺值、水、食鹽、熟食克數（decisions #32–#35，2026-10-21）

協調者以生成用的 `cached_factor_calculator` 對 281 道合格食譜計算，只有 34 道能完整算出熱量＋蛋白質。追溯後的四類原因
與處理如下。新增 migration `c6a9d2e4f817`（接在 `b7f2c9e4a8d1` 之後，維持單一 head）。

### #32 TFDA 必要營養空白 → LLM 推估（`kind = nutrient_fill`）

- 例：砂糖→方糖 N0100101、冰糖 N0100201、白醋→米醋 P0600101、香油 P0900201 的 TFDA 蛋白質欄位空白，熱量有值，
  整道菜因此缺必要營養。
- 快取沿用 `conversion_factor_estimates`：`kind = nutrient_fill`、`food_id` 為 **TFDA 樣本 ID**、`factor_key` 為營養素
  （`energy_kcal`／`protein_g`），新欄位 `nutrient_per_100g`（Numeric(10,4)，資料庫 CHECK 0–900）。人工覆寫優先、同層取最新、
  `rejected` 永不使用（同 #19）。查表：`FactorSnapshot.nutrient_fill(sample_id, nutrient)`。
- 只補必要營養（熱量、蛋白質）中 **TFDA 空白者**；TFDA 有值一律以 TFDA 為準（批次不掃描、人工覆寫拒絕、計算時不套用）。
- 計算（`make_tfda_calculator(nutrient_fills=...)`，`cached_factor_calculator` 自動帶入）：T04 算完、快取烹調係數套完後，
  該食材所用 TFDA 樣本的必要營養仍為 None 且樣本該欄空白時，套用推估值：
  - 有套快取烹調係數 → 生樣本 × 該營養保留率（熟樣本 × 產率），與同列其他營養一致；
  - 否則（T04 直接以樣本組成計算）→ 每 100 g × 克數。
  - 至少要有一項營養已由該樣本算出才補，避免只補出孤立的單一營養。
- 標示：trace `estimated_factors` 加入 `kind = nutrient_fill` 的標記（模型、prompt／schema 版本、估計 ID、`nutrient_per_100g`）、
  `estimated_nutrients` 加入該營養、`nutrient_fills = {營養: {sample_id, per_100g, source, status}}`；結果列 `llm_estimated`、
  `estimated_nutrients`，結果層級 `llm_estimates`。前端沿用既有「LLM 推估」標示（食材旁與營養表上方），不需改畫面；
  人工覆寫值不標「LLM 推估」。
- 批次：`python -m app.estimation run --kind nutrient_fill`。缺口＝合格食譜實際用到（經 #28 對照後）的 TFDA 樣本中，必要營養
  欄位空白的 (樣本 ID, 營養素)。輸入快照：樣本名稱、別名、生熟狀態、描述、已知每 100 g 營養、使用它的食譜食材名稱與食譜編號。
  Structured Outputs strict（`value_per_100g`、`assumptions`），經 T10 `spend.paid_call`（用途 `nutrient_fill_estimation`）。
  驗證（`store.check_fill`，人工覆寫共用）：熱量 0–900 kcal、蛋白質 0–100 g，且 `蛋白質 × 4 ≤ 熱量 × 1.1 + 1`；拒答、截斷、
  格式錯誤、超出範圍一律不寫入。冪等、`--dry-run`、`--refresh`、`--limit` 同 #19。
  不指定 `--kind` 時 nutrient_fill 與 cooking／unit 同在第二階段（對照寫入後重新掃描）。
- 2026-10-21 以真實工作簿在測試資料庫 dry-run（只有精確名稱／別名／同義詞，尚無 LLM 對照）：5 組（冰糖、香油、啤酒、可樂、
  麥芽糖的蛋白質）；開發資料庫已有 LLM 對照（砂糖、白醋等），實際組數會較多（預估 10–30 組）。每次約 1k 輸入／0.1k 輸出
  token，30 組合計不到新台幣 2 元。

```bash
python -m app.estimation run --kind nutrient_fill --dry-run
python -m app.estimation run --kind nutrient_fill
python -m app.estimation list --kind nutrient_fill
python -m app.estimation override --kind nutrient_fill --food N0100101 --key protein_g --value 0 --reviewer 營養師A
python -m app.estimation approve|reject <id> --reviewer 營養師A
```

### #33 水：營養為已知 0

- 判定（`meal_calculation.is_water`）：FOOD 編號屬於價格端「水免計價」設定（`config/price_exempt_ingredients.json` 中名稱為水者，
  目前 FOOD000119 水、FOOD001208 滾水），或標準名稱為水（水、清水、開水、冷水、熱水、溫水、滾水、冰水、飲用水、自來水）。
- 全部營養為 `Decimal(0)`（已知 0，不是缺值）；不送 T04、不查 TFDA 對照，trace `nutrition_basis = nutrition_source = water_known_zero`、
  `sample_id = None`、`tfda_mapping = None`。批次掃描略過水（不產生對照、烹調、單位、缺值推估缺口）。

### #34 食鹽：人工覆寫對照

TFDA 沒有精鹽／食鹽樣本，最接近的是岩鹽 **P0300101**（2 kcal、蛋白質 0.0 g、鈉 34,468 mg／100 g）；低鈉鹽 P0300201 不適合。
開發環境由協調者以既有覆寫指令設定（人工值優先於 LLM 的 NONE）：

```bash
python -m app.estimation override --kind tfda_mapping --food FOOD000691 --key tfda --sample P0300101 \
  --reviewer <名稱> --note "decisions #34：TFDA 無精鹽樣本，以岩鹽近似"
# 同義的「鹽」FOOD001680、「鹽巴」FOOD001681、「海鹽」FOOD000742 可比照設定
```

### #35 熟食名稱＋熟樣本 → 熟重

- 例：白米飯（工作簿克數依 #17 視為生重）對照到熟樣本白飯 A0550601，T04 回報 `cooked_food_requires_cooked_weight_basis`。
- 規則（`_effective_basis`）：只在工作簿生重慣例（`weight_basis_source = recipe_workbook_raw_convention`）下，若對照到的 TFDA
  樣本 `state = cooked` 且食材名稱本身是熟食（`is_cooked_food_name`：以「飯」「粥」結尾或含「熟」），改視為熟重
  （`weight_basis = cooked`，依據 `cooked_food_cooked_sample`），且不再傳烹調方式給 T04（熟樣本已是熟的狀態；另列的油鹽照算）。
- 其他情況維持 #17 生重：明確的生熟欄位（`recipe_weight_basis_field`）、AI 模型標示（`model_declared`）不改；名稱不是熟食
  （如「米」）對到熟樣本仍報錯。結果列與 trace 的 `weight_basis`／`weight_basis_source` 為實際採用的依據。
- 測試：`tests/test_nutrient_gaps.py`（假 responder，不呼叫真實 OpenAI）。

## 非必要營養素烹調後近似（decisions #40，2026-10-21）

- 問題：快取烹調係數（#19）只推估熱量與蛋白質的保留率，其他營養素烹調後沒有係數可套，連雞蛋、豆腐、番茄等 TFDA 有完整數值的食材也變成未知。
- 規則（`_apply_cooking_factor`）：套用快取烹調係數且該係數有產率（`yield_factor`）時，沒有保留率的非必要營養素（碳水、脂肪、糖、鈉、膳食纖維）
  以 **TFDA 生組成每 100 g × 生重**計算（保留率視為 1；產率只換算熟重 `cooked_weight_g`）。熟樣品有值者仍用熟樣品 × 產率。
- 熱量與蛋白質不變：維持 #19 保留率推估與 #32 空白推估。TFDA 生組成本身空白的營養素仍為 None（不填 0）。
- 只在必要營養也由該係數算出時才套用；沒有產率的係數不近似。熟重（#35）、直接食用（`consumed_as_is`）與水（#33）走原邏輯、不標近似。
- 標示：食材 trace `approximate_nutrients`、`nutrient_basis = {營養素: "yield_only_retention_1"}`；結果列 `approximate_nutrients`；
  `calculate_meal` 結果 `approximations = {used, food_ids, nutrients, basis}`；結果檢視 `nutrition.approximations.label = "近似"`，
  前端營養表列出非必要營養素，近似者旁標示「近似」。近似不算 LLM 推估的營養（不列入 `estimated_nutrients`）。
- 已知範圍：無烹調方式且非直接食用的生樣品，T04 仍回報 `no_cooking_method`，全部營養（含熱量）維持未知，此行為未改。
- 測試：`tests/test_other_nutrients.py`（含蕃茄炒蛋類：雞蛋平均值＋牛番茄，炒）。
