# T15 生成結果、AI 標示、知識說明與缺值決策

程式：`backend/app/services/meal_validation.py`（R09 判定，純函式）、`backend/app/services/meal_results.py`（結果檢視、決定、重試申請）、
`backend/app/services/result_retry.py`（只重試知識說明、缺價定向查價的背景工作）、
`backend/app/api/meal_results.py`（路由）、前端 `frontend/src/components/MealResultView.tsx`（由 `GenerationPanel` 顯示）。
測試：`tests/test_t15_meal_validation.py`（決策表）、`tests/test_t15_meal_results.py`（問卷 → 工作 → 結果 → 決定，整條經測試資料庫）。
Migration：`c8e3f6a1d2b4`（接在 `b5d2e8f41c07` 後）；`a3d6e9b1c4f7`（重試工作：`background_jobs.params`，重試產生的版本同時有 `job_id` 與 `derived_from_result_id`）。

## API（皆限本家庭；他家庭一律 404）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/meal-requests/{id}/results` | 結果版本清單（新到舊），含 `adoptable` |
| GET | `/meal-requests/{id}/results/{v}` | 結果頁資料：`dishes`（整餐用量、食材分組 `group`、步驟原文 `steps` 與顯示用 `steps_display`；`display_amount`／`display_unit`／`display_text`）、`staple`、`prices`（只列需補買食材、採購價／成本價、來源與時間；未知為 `null`）、`nutrition`（含 `quantities_verified`、`suggestions`）、`knowledge`（含 `retry_available`）、`retries`（價格／知識重試工作狀態）、`previous_day`（`excluded_dishes`、`specified_repeats`）、`previous_day_check`、`mode_label`／`fallback_message`、`eligibility` |
| POST | `/meal-requests/{id}/results/{v}/decisions` | `{kind, request_version}`＋`Idempotency-Key`；`kind` 為 `accept_missing_price` 或 `continue_missing_nutrition` |
| POST | `/meal-requests/{id}/results/{v}/price-retry` | `{request_version}`＋`Idempotency-Key` → `202 {job}`；缺價食材排入 T02 定向查價背景工作，完成後產生新結果版本（P3） |
| POST | `/meal-requests/{id}/results/{v}/knowledge-retry` | `{request_version}`＋`Idempotency-Key` → `202 {job}`；只重試知識說明，產生新結果版本（pre-review #3） |

重新生成沿用 `POST /meal-requests/{id}/submit`（換新的冪等鍵即新工作、新結果版本）。
錯誤：問卷版本不符或生成中 `409 VERSION_CONFLICT`；同鍵不同命令 `409 IDEMPOTENCY_CONFLICT`；
不需要或不允許的決定（例如已知成本超預算時接受缺價）`422 INVALID_INPUT`，`details.reason = DECISION_NOT_APPLICABLE`。

## 不可採用時仍可查看（P2、P4）

- 缺營養、基準無解或食材無法換算時，仍列出每項整餐用量（`nutrition.quantities_verified = false`）與可計算的成本；
  無法換算克數的食材列在 `cost.unquantified_food_ids`。
- `nutrition.suggestions`：超出範圍的營養要往哪個方向調、差多少，各食材在已審核可調範圍內最多能補多少，
  以及範圍內是否補得足；缺資料時列出要補的食材。後端的差額與可調範圍是每 1 份（1n）基準值，
  畫面乘上 `nutrition.total_n` 換成整餐值並標明「整餐，共 N 份」，與整餐營養表一致（決策 #53）。
- 營養未知（決策 #53）：`nutrition.known_partial`（值為 null 的營養之已知部分整餐合計，沒有任何已知值時為 null）與
  `nutrition.missing_by_nutrient`（缺值食材名稱，去重）只列值為 null 的營養。優先採用 T12 在 `calculation.final` 提供的同名欄位，
  舊結果沒有時由 `calculation.ingredients[].nutrients` 推得（缺值食材再以 `missing_nutrients` 為準）。畫面顯示
  「未知（已知 X，缺：A、B）」，比照價格「未知（已知 X 元）」，仍不以 0 代替。營養表標題為「營養估算（整餐，共 N 份）」；
  已採用紀錄頁用同一個 `NutritionTable` 顯示 7 種營養素，以 null 判斷未知（0 照常顯示）。
- 主食缺資料的說明依 `staple.unresolved_reason` 區分：`no_tfda_cooked_sample`／`staple_without_tfda_sample` 顯示
  「主食「X」目前沒有營養資料，整餐營養無法計算；請改選其他主食」，`staple_not_supported` 顯示不在清單；
  只有真正缺克數換算的食材才沿用「補上克數換算後重新計算」。
- 顯示用量：克取到 1 g；其他單位（顆、片、匙、大匙、杯…）四捨五入成整數、大於 0 最少 1（decisions #43，取代 0.5；舊結果讀取時也照此取整）；沒有數量的照原文（例如「少許」）。計算一律用未取整值。

## 重試（背景工作，產生新版本）

| 種類 | 何時可用 | 做什麼 | 限制 |
|---|---|---|---|
| `knowledge_retry` | 說明為 `KNOWLEDGE_UNAVAILABLE`（服務故障、額度不足）；缺知識（`not_found`）不需要 | 同一結果的最終菜色食材重新檢索審核知識並產生說明（向量化、說明經 T10 額度）；菜單、份量、營養、價格原樣沿用 | 每戶每日 10 次；需有剩餘額度 |
| `price_retry` | 預算狀態 `PRICE_INCOMPLETE` | 對缺價食材（最多 10 種）呼叫 T02 `PriceCollectionService.run(food_ids=…)` 定向查價（PX Mart、請求間隔 ≥2 秒），再以最新快照重算成本 | 每戶每小時 3 次；與每日排程互斥，鎖住時自動稍後重試 |

- 進行中＝工作 `queued`／`running`（結果頁 `retries.*.status`，前端顯示「查價中…」「重試中…」）；
  失敗＝工作 `failed`，例如 `PRICE_LOOKUP_FAILED`，原結果不變，可用新的識別碼再試。
- 完成後產生新版本：`job_id` 為重試工作、`derived_from_result_id` 指向來源版本，`payload.knowledge_retry`／`payload.price_retry` 記錄來源版本與時間；
  價格重試另在來源版本寫一筆 `retry_missing_price` 決定（`produced_result_id` 指向新版本）。
- 新版本不沿用來源版本的使用者決定，例如缺價例外需在新版本重新接受。重試工作不改問卷的生成狀態；完成時只在新版本是最新、
  且問卷仍在結果狀態時更新 `result_ready`／`awaiting_decision`。生成面板以 `/jobs?kind=meal_generation` 只追蹤生成工作。

## 昨日衝突預先檢查（pre-review #4）

結果讀取時以 `meal_history.recheck_previous_day` 比對目前的前一日已採用紀錄，回傳 `previous_day_check`
（`status` ok／conflict，衝突時含 `conflicting_dishes` 與說明）。前端衝突時停用採用並說明；T16 採用交易內仍會再驗一次。

## 採用資格（`meal_validation.evaluate`）

| 情況 | 可採用 | 待決定 |
|---|---|---|
| 必要條件完整、成本不超預算、熱量與蛋白質 ±10% 內 | 是 | — |
| 缺價、已知成本未超預算、其他條件都通過 | 接受缺價例外後可（`price_exception_accepted`） | `accept_missing_price` |
| 已知成本超預算（不論是否缺價） | 否 | — |
| 缺熱量或蛋白質 | 否（補齊重驗後才可） | `continue_missing_nutrition`（只確認繼續查看） |
| ±10% 不符、基準無解、食材無法換算、禁忌／指定菜／昨日違規、問卷已改版 | 否 | — |

- 決定綁定單一結果版本；接受缺價例外時保存當時缺價食材集合，集合不同（例如重試後）即失效。
- 其他條件已不通過時不詢問缺價（接受也不能採用），只列出阻擋原因。
- 任何決定都不放寬禁忌、指定菜、±10% 或昨日去重。缺知識不影響資格。

## 問卷狀態

- 生成工作成功時，有待決定的缺值 → `awaiting_decision`，否則 `result_ready`（`result_ready` 不代表可採用）。
  由 `generation.persist_result` 在同一交易算出摘要的 `request_status`，`jobs.complete` 依此回寫問卷。
- 決定或重試後，只有最新結果版本、且問卷仍在結果狀態時才更新狀態。修改問卷照舊回到 `draft`（新版本）。
- 失敗（`failed`）不清空問卷；查無資料庫菜單自動 AI 生成，沒有詢問同意的對話。

## 給 T16（採用）

- 以 `(meal_request_id, result_version)` 指定版本；決定在 `meal_result_decisions`
  （`kind`、`result_version`、`request_version`、`details.missing_food_ids`、`details.known_cost_total`／`budget`）。
- 採用時必須在交易內重新呼叫 `meal_results.eligibility(db, request, result)`（即 `meal_validation.evaluate`＋該版本的決定），
  要求 `adoptable`，並另外執行 `meal_history.recheck_previous_day`；不可信任前端或先前的回應。
- 重試產生的版本 `derived_from_result_id` 指向來源版本（重試工作產生時 `job_id` 也有值）；`payload.price_retry`／`payload.knowledge_retry` 記錄來源版本與時間。

## 已知限制

- 定向查價只走 PX Mart 公開頁、低頻率；真實覆蓋率仍屬 G02 未驗證項目。
- 主食份量設定（`config/staples.json`）待營養審核者確認。

## 顯示調整（decisions #41–#44，2026-10-21）

- **步驟分組展開（#41）**：資料庫菜色的步驟原文（`steps`）不改；`steps_display` 把「[調味]」「[醃料]」等作者分組代稱展開成
  「調味（沙茶醬、醬油、糖or味琳）」。結果頁與已採用紀錄頁（`frontend/src/components/DishDetail.tsx` 共用）顯示 `steps_display`，
  舊結果沒有時用原文。食材清單先列沒有分組的食材，再依分組加小標（如「調味：沙茶醬 1 匙、醬油 1 匙…」）。規則見 docs/T12。
- **不顯示推估字樣（#42）**：結果頁、已採用紀錄頁、營養表不再出現「LLM 推估」「LLM 對照」「近似」等標籤與說明文字；
  `llm_estimated`、`estimate_label`、`llm_mappings`、`approximations` 等欄位仍在 API 與 payload／trace 中供稽核。
  缺克數換算的建議文字改為「補上克數換算後重新計算」。
- **份量整數（#43）**：見上方「顯示用量」。
- **調味料不列缺料（#44）**：`config/seasoning_ingredients.json` 內的調味料在成本行為 `price_status = "seasoning"`，
  不出現在「需補買食材」與價格合計，也不觸發缺價例外；營養照算。已存在的舊結果版本維持當時的價格行（重新生成或重新查價後才套用）。
