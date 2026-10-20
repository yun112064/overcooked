# T16 採用交易與日期歷史查詢

程式：`backend/app/services/meal_plans.py`（採用交易、快照、讀取）、`backend/app/api/meal_plans.py`（路由）、
前端 `MealResultView` 的採用按鈕與 `frontend/src/pages/MealHistoryPage.tsx`（`/history`）。
測試：`tests/test_t16_meal_plans.py`（問卷 → 工作 → 結果 → 採用 → 次日生成，整條經測試資料庫，付費服務以假物件取代）。
Migration：`d9a4b7e2c5f1`（接在 `c8e3f6a1d2b4` 後）以 ALTER 擴充 T11 的 `adopted_meal_plans`，不另建平行表。

## API（皆限本家庭；他家庭一律 404）

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/meal-plans` | `{meal_request_id, result_version, request_version, price_exception_decision_id?}`＋`Idempotency-Key`；新建 201、重送 200 |
| GET | `/meal-plans?date=YYYY-MM-DD` | 當日全部餐別的已採用餐食（早→午→晚），每筆含完整快照 |
| GET | `/meal-plans` | 最近有紀錄的日期與筆數（新到舊） |
| GET | `/meal-plans/{id}` | 單筆快照 |

## 採用交易（單一資料庫交易，任一步失敗全部回滾）

1. 家庭層級 `pg_advisory_xact_lock`：同家庭的採用與昨日歷史重驗依序進行。
2. 冪等：同鍵同命令回傳同一筆；同鍵不同內容 `409 IDEMPOTENCY_CONFLICT`。同一問卷只能採用一次：
   換鍵重送同一版本回傳同一筆，採用其他版本 `409 VERSION_CONFLICT`（`reason = ALREADY_ADOPTED`）。
3. 鎖問卷列；生成中、問卷版本不符或結果是舊問卷版本產生的 → `409 VERSION_CONFLICT`；結果版本不存在 → 404。
4. 重新呼叫 T15 `meal_results.eligibility`（`meal_validation.evaluate`＋該版本決定），不可採用 → `422`，
   錯誤碼為第一個阻擋原因（如 `BUDGET_EXCEEDED`、`NUTRITION_OUT_OF_RANGE`、`PRICE_INCOMPLETE`），`details.blocks` 列全部。
5. 以缺價例外採用時，請求必須帶 T15 接受紀錄的 `price_exception_decision_id`
   （未帶 `PRICE_EXCEPTION_NOT_CONFIRMED`；不需要卻帶了 `PRICE_EXCEPTION_NOT_APPLICABLE`，皆 422 `INVALID_INPUT`）。
6. `meal_history.recheck_previous_day` 重新讀取前一日（餐食日期減一天、全部餐別、只算已採用）紀錄；
   生成後的變動造成撞菜 → `409 PREVIOUS_DAY_DISH_CONFLICT`（`details.conflicting_dishes`）。沒撞菜則保存新的比對。
7. 寫入 `adopted_meal_plans`（菜色識別、別名、模式供 T11 昨日排除）與 `snapshot`，問卷改為 `confirmed`。

交易內只讀寫資料庫：價格、營養與知識說明都取自已保存的結果，不呼叫模型、知識服務或價格來源。

## 快照（`adopted_meal_plans.snapshot`，MealPlanSnapshot）

深拷貝的結果 payload（菜色、整餐用量與步驟、參與項目與目標、T12 計算、T13 價格與報價、知識說明與引用、
模型／prompt／schema／知識索引／定價版本）、採用當下的問卷內容、資料庫菜的來源快照（名稱、網址、匯入批次、內容雜湊）、
採用資格與決定紀錄、缺價接受紀錄、缺值標示（缺價食材、是否接受例外、缺少的其他營養）、
生成時與採用時的昨日比對。讀取只讀快照：來源食譜、價格更新，甚至刪除原問卷（外鍵改為 NULL），歷史都不變。

## 問卷狀態

- `confirmed` 後不能再送出生成或修改（`409 VERSION_CONFLICT`，`reason = ALREADY_ADOPTED`），要規劃新的一餐請另建問卷。
- 同一日期與餐別可有多筆已採用紀錄（不同問卷）；昨日排除讀取全部。

## 已知限制

- 已採用紀錄目前沒有取消／改採用流程（`status = superseded` 保留給之後）。
- 採用只重驗前一日歷史；之後才採用的前一天餐食不會回頭讓已採用的次日紀錄失效（spec 未要求）。
