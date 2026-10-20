# T15 生成結果、AI 標示、知識說明與缺值決策

程式：`backend/app/services/meal_validation.py`（R09 判定，純函式）、`backend/app/services/meal_results.py`（結果檢視、決定、缺價重試）、
`backend/app/api/meal_results.py`（路由）、前端 `frontend/src/components/MealResultView.tsx`（由 `GenerationPanel` 顯示）。
測試：`tests/test_t15_meal_validation.py`（決策表）、`tests/test_t15_meal_results.py`（問卷 → 工作 → 結果 → 決定，整條經測試資料庫）。
Migration：`c8e3f6a1d2b4`（接在 `b5d2e8f41c07` 後）。

## API（皆限本家庭；他家庭一律 404）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/meal-requests/{id}/results` | 結果版本清單（新到舊），含 `adoptable` |
| GET | `/meal-requests/{id}/results/{v}` | 結果頁資料：`dishes`（整餐克數、步驟）、`prices`（只列需補買食材、採購價／成本價、來源與時間；未知為 `null`）、`nutrition`、`knowledge`、`previous_day`（`excluded_dishes`、`specified_repeats`）、`mode_label`／`fallback_message`、`eligibility` |
| POST | `/meal-requests/{id}/results/{v}/decisions` | `{kind, request_version}`＋`Idempotency-Key`；`kind` 為 `accept_missing_price` 或 `continue_missing_nutrition` |
| POST | `/meal-requests/{id}/results/{v}/price-retry` | `{request_version}`＋`Idempotency-Key`；重讀最新 T02 價格快照產生新結果版本 |

重新生成沿用 `POST /meal-requests/{id}/submit`（換新的冪等鍵即新工作、新結果版本）。
錯誤：問卷版本不符或生成中 `409 VERSION_CONFLICT`；同鍵不同命令 `409 IDEMPOTENCY_CONFLICT`；
不需要或不允許的決定（例如已知成本超預算時接受缺價）`422 INVALID_INPUT`，`details.reason = DECISION_NOT_APPLICABLE`。

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
- 缺價重試產生的版本 `job_id` 為 NULL、`derived_from_result_id` 指向來源版本；`payload.price_retry` 記錄來源版本與時間。

## 已知限制

- 缺價重試只重讀資料庫中最新的 T02 快照，不即時向 PX Mart 查價；新價格要等 T02 排程收集。
- 前端尚未提供採用按鈕（T16）；結果頁只顯示後端判定的資格與原因。
