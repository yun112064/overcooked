# T14 資料庫菜單生成、AI 自動回退與知識說明

程式：`backend/app/services/generation.py`（流程與驗證）、`backend/app/services/llm_client.py`（`OpenAIResponder`）、
`backend/app/jobs/worker.py`（`HANDLERS["meal_generation"]`）、`backend/app/services/staples.py`（主食）。
測試：`tests/test_t14_generation.py`、`tests/test_t14_staple.py`、`tests/test_t14_llm_client.py`。
Migration：`b5d2e8f41c07`（`generation_results`，接在 `a1c4e7f20b39` 後）。

## 一次工作嘗試的流程（工作階段 stage）

| stage | 內容 |
|---|---|
| `retrieving` | 讀鎖定問卷版本、必要限制（過敏／禁吃＋同義展開）、主食、T03 本餐目標；T11 `retrieve_database_menu` |
| `ai_generating` | 只有查無可行資料庫菜單時：Responses API＋Structured Outputs（strict JSON Schema 由 Pydantic 產生） |
| `validating` | Pydantic 解析＋後端檢查（見下） |
| `calculating` | T12 `calculate_meal`（資料庫 `database`、AI `ai` 模式）＋T13 `meal_cost_from_db` |
| `knowledge`／`explaining` | 依最終食材 ID 做 T11 知識 RAG（向量化經額度預留），再請模型只引用取得的片段寫說明 |

- 資料庫模式不呼叫模型組菜，只用 T11 實際查得的食譜（不可能新增庫外菜）。
- 資料庫模式依 T11 排名逐組驗證候選組合（`alternatives`，最多 20 組；pre-review #2）：先 `check_menu`，再以 T12 計算。
  第一個可採用者勝出；都不可採用時，若有只缺資料（`NUTRITION_INCOMPLETE`／`INGREDIENT_UNRESOLVED`）的組合，保留第一個讓使用者查看與決定；
  全部都是 `NUTRITION_OUT_OF_RANGE`／`NO_FEASIBLE_BASELINE`（在可調範圍內不可行）才視為「查無可行資料庫菜單」，
  以 `DATABASE_MENU_NUTRITION_INFEASIBLE` 自動 AI 回退（`fallback_details.tried` 記錄每組的阻擋原因）。仍遵守兩次嘗試上限與額度預留。
  每組評估紀錄存在 `payload.database_candidates`。
- 查無（`NO_MATCHING_RECIPES`、`SPECIFIED_DISH_NOT_IN_DATABASE`、`ALL_EXCLUDED_BY_PREVIOUS_DAY`、`DATABASE_MENU_NUTRITION_INFEASIBLE`）直接進 AI，不詢問使用者，原因存 `fallback_reason`。
- 指定菜與禁忌衝突（T11 `specified_dish_conflict`）：以 `SPECIFIED_DISH_DIETARY_CONFLICT` 失敗回報衝突的菜與食材，**不轉 AI、不生成同名換料的菜**，問卷保留供修改（spec「不偷偷換菜」、AC09；pre-review #1）。
- 主食含過敏／禁吃食材：`STAPLE_DIETARY_CONFLICT` 回報，不替換主食。
- 指定菜多義（`DISH_IDENTITY_UNRESOLVED`）直接失敗回報，不猜、不回退。
- 食譜資料庫故障：錯誤碼 `RECIPE_DATABASE_UNAVAILABLE`，可重試，不回退 AI。
- 未設定 `OPENAI_API_KEY`：資料庫菜單照常；需要 AI 時以 `GENERATION_FAILED` 明確失敗，不假裝產生菜單。

## 後端驗證（兩種模式都做 `check_menu`）

- 菜數、重複菜、指定菜都在、禁忌食材（含同義展開）、昨日未指定菜（`PREVIOUS_DAY_DISH_CONFLICT`）。
  AI 模式重新計算昨日比對：指定菜即使不在資料庫也只豁免它自己，其他昨日菜仍排除。
- AI 候選另外檢查：拒答 `MODEL_REFUSED`、截斷 `OUTPUT_TRUNCATED`、JSON／schema 錯誤 `INVALID_FORMAT`（含 schema 外欄位，例如模型自報價格）、
  食材名稱對不到標準字典 `INGREDIENT_UNRESOLVED`／`INGREDIENT_AMBIGUOUS`、模型給的 FOOD 編號與名稱矛盾 `INGREDIENT_ID_MISMATCH`、
  用量非正或過大 `INVALID_QUANTITY`、缺步驟或烹調方式。
- 無效候選 → `RetryableJobError(code=GENERATION_FAILED)`；該次呼叫已結算，重試不重複計費。T10 上限兩次嘗試，
  回退或說明不重置次數，第二次仍無效即失敗並回報原因，不無限重送。
- 營養不符（±10%、缺營養）與價格只影響可採用判定，存進結果，不算無效候選。模型輸出沒有價格、營養或來源欄位，AI 菜 `recipe_id` 一律為 null、`source = ai`。

## 主食（decisions #16、P1）

- `config/staples.json`（版本 `staples-2026-10-21`，`review_status = pending_review`）：白飯→TFDA 白飯（熟重 150 g，可調 100–250）、
  麵→乾麵條（乾重 80 g）、麵包→土司（60 g）、地瓜→黃肉甘藷（150 g）；糙米飯沒有 TFDA 樣品，選它時營養無法計算（不可採用，不填 0）。
  「不需要主食」等沒有主食列；其他自由文字標 `staple_not_supported`。份量與範圍是開發者先填的合理值，待營養審核者確認。
- 主食列（`role = staple`）計入營養與 ±10% 驗證；資料庫模式與菜色一起在可調範圍內對齊；AI 模式以預設份量計入，
  模型拿到的每份參考值已扣除主食營養。**不計入價格與缺料**（不在 T12 `requirements`、不查價、不查知識）。
- 結果 `payload.staple` 保存對應、份量與設定版本；`evidence.staple_config_version`。

## 知識說明

- 缺知識（`not_found`）：省略說明，不呼叫說明模型，不阻擋採用。
- 服務故障：`explanation.status = unavailable`、`code = KNOWLEDGE_UNAVAILABLE`、附原因，結果照常產生，供 T15 顯示與重試；不偽裝成零筆。
- T10 額度不足（`BudgetExhausted`）或可能已計費（`UncertainBilling`）發生在知識步驟時，工作不失敗：菜單照常保存，
  知識標 `KNOWLEDGE_UNAVAILABLE`（原因 `BUDGET_EXHAUSTED`／`BILLING_UNCERTAIN`），之後可用 T15「只重試知識說明」（P7）。
- 知識步驟是 `generation.knowledge_section`，生成與 T15 的知識重試共用。
- 只保留引用全部對上本次快照（knowledge_id＋source_version＋ingredient_id）且與說明食材相同的段落；其餘列在 `dropped`（原因代碼），不得顯示。
- 說明模型拒答、截斷或格式錯 → `unavailable`（`EXPLANATION_*`），菜單不受影響。知識在配方確定後才查，不回頭改菜或份量。

## 結果儲存（給 T15／T16）

`generation_results`：每個成功的生成工作恰好一筆，寫在 `jobs.complete` 的同一交易（已確認租約；租約遺失則不寫）。

| 欄位 | 說明 |
|---|---|
| `meal_request_id`＋`result_version` | 唯一；同一問卷依序 1, 2, …（重新生成、修改問卷後再生成都是新版本）。T16 以此指定採用版本 |
| `job_id` | 唯一；同一冪等鍵重送拿回同一工作，不會多一筆結果 |
| `request_version` | 產生此結果的問卷鎖定版本 |
| `mode`、`fallback_reason` | `database_menu`（reason 必為 null）或 `ai_generated_menu`（reason 必填），DB CHECK 保證 |
| `payload` | 完整快照（JSONB；Decimal 以字串保存精確值），見下 |

`payload` 主要鍵：`schema_version`（`generation_result/v1`）、`mode`、`mode_label`（「資料庫菜單」／「AI 生成菜單」）、`fallback_reason`、
`fallback_details`、`request`、`query_source`（T11 查詢結果，含候選與 `alternatives`）、`database_candidates`、`dishes`（標準菜色 ID、別名、來源、recipe_id、是否指定、步驟、每 1n 食材）、`staple`、
`restrictions`、`targets`（T03）、`previous_day`（PreviousDayContext）、`calculation`（T12 完整輸出，含 `requirements`）、
`cost`（T13；`budget_status` OK／PRICE_INCOMPLETE／BUDGET_EXCEEDED）、`validation`（必要條件已通過、營養可採用、預算狀態）、
`knowledge`（RAG 快照）、`explanation`、`evidence`（模型／prompt／schema 版本、知識索引與向量模型、價格表版本、嘗試次數、付費呼叫鍵）、`result_version`。

`background_jobs.result` 只存摘要 `{generation_result_id, result_version, mode, mode_label, fallback_reason}`，
`background_jobs.result_version` 等於結果版本；`GET /jobs` 的 `result_summary` 回傳此摘要（前端顯示「AI 生成菜單」）。
讀取結果 API、`awaiting_decision` 與缺值決策見 T15。

## 模型與額度

- 模型 `settings.generation_model = gpt-5.4-mini-2026-03-17`（`config/model_pricing.json` 已列價），呼叫名稱 `menu`、`explain`（向量化 `embed_query`），
  呼叫鍵 `job_id:attempt:name`。輸入 token 上限以 UTF-8 位元組數（含 instructions 與 schema）保守估計；輸出上限
  `generation_max_output_tokens = 6000`、`explanation_max_output_tokens = 2000`。
- 問卷文字、菜名與知識片段只放在 `input`（JSON），規則只在 `instructions`；驗證規則全在後端。
- 不記錄模型原始輸入／輸出或金鑰。

## 已知限制

- 候選組合以「指定菜各食譜 × 其他菜依排名取組合」產生，最多 20 組；同一道菜的其他來源食譜只用於指定菜，不保證窮舉所有組合。
- AI 菜的生熟重由模型標示，營養仍由 T12／TFDA 計算；沒有熟食樣品的生食同 T12 限制。
- 知識說明的文字內容無法逐句比對片段，只能保證引用正確；正式知識來源仍待 G07。
