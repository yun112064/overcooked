# T16 食譜庫預覽採用路徑修正 QA 報告（2026-11-08）

- 分支：`fix/t16-preview-adoption`（以 `origin/main` `a5ad10d` 為基礎）
- 範圍：`POST /meal-plans` 帶 `recipe_ids` 的預覽採用路徑（`database_menu_plans.adopt`）
- 未變更：已核准的產品規則（缺價不擋採用、已知成本超預算不可採用、昨日去重與指定菜例外、±10% 營養範圍）

## 1. 問題與修正

| # | 問題 | 修正 |
|---|---|---|
| 1 | 預覽採用只檢查 T12 營養，**沒有計算 T13 成本**，已知成本超過預算的菜單仍可採用 | 依 T12 驗證後的整餐用量（不含主食）以 `meal_cost_from_db` 計算成本，再交給與生成結果採用相同的 `meal_validation.evaluate` 判定；超預算回 `422 BUDGET_EXCEEDED`，沒有可計價用量回 `422 PRICE_INCOMPLETE`，缺價可採用並標示 `price_exception_accepted` |
| 2 | `_snapshot()` 缺少昨日比對、來源及規則版本，回看歷史時無法追溯 | 快照新增 `previous_day`（`at_generation`／`at_adoption`／`history_changed`）、`recipe_sources`、`versions`（快照／採用規則／T03 營養規則／食譜與營養發布／對照版本）、`cost`、`eligibility`、`missing`、`adopted_versions`、`request`；`schema_version` 升為 `database_menu_snapshot/v2` |
| 附帶 | 預覽採用沒有在採用前重讀昨日紀錄 | 改用與正式路徑相同的 `require_previous_day`，撞菜回 `409 PREVIOUS_DAY_DISH_CONFLICT` |

### 兩種採用路徑共用的邏輯（`meal_plans.py`）

- `require_adoptable(evaluation)`：同一張決策表（預算、缺價、版本），同一組錯誤碼。
- `require_previous_day(db, context, dishes)`：採用前重讀前一日與同日其他餐。
- `previous_day_snapshot(at_generation, recheck)`：快照中昨日比對的格式。
- 另共用 `_recipe_sources`、`ADOPTION_RULE_VERSION`、`RECORD_LABEL`、`confirm_request`、`_lock_household`。

正式生成結果路徑改為呼叫上述函式，行為不變（原有測試全數通過）。

## 2. 測試

測試環境：本機一次性 PostgreSQL（pgserver + pgvector 0.6.2，位於暫存目錄），未連線任何正式資料庫；價格以合成 `PriceQuote` 替身提供，不對外連線。

| 範圍 | 結果 |
|---|---|
| `tests/test_meal_plans.py`（T16 預覽採用） | 12 通過（原 6 項＋新增 6 項） |
| 新增 6 項在未修正的 `main` 上執行 | 6 項全部失敗（確認能抓到問題） |
| 完整後端 `pytest tests` | 720 通過、1 略過、0 失敗 |
| `nutrition_planning/tests` | 174 通過 |

新增測試：

1. `test_preview_adoption_refuses_known_cost_over_budget`：約 1333 元超過預算 300 元 → `BUDGET_EXCEEDED`，不寫入紀錄，問卷不改為 confirmed。
2. `test_preview_adoption_saves_cost_within_budget`：快照保存 `cost`，`budget_status` 為 `OK`。
3. `test_preview_adoption_with_missing_price_is_adoptable_but_flagged`：缺價仍可採用，`missing.price_food_ids` 列出缺價食材。
4. `test_preview_adoption_without_final_quantities_is_refused`：沒有可計價用量 → `PRICE_INCOMPLETE`（與正式路徑一致）。
5. `test_preview_adoption_rechecks_previous_day_history`：8/19 已採用同菜 → 8/20 採用被拒。
6. `test_preview_snapshot_keeps_previous_day_sources_and_rule_versions`：快照含昨日比對、來源、版本。

原有測試的斷言均未修改。唯一的資料調整：`PASSED` 替身的食鹽補上 `food_id: FOOD000691`（真實 T12 結果都帶有 food_id；食鹽屬調味料，成本為 0）。測試類別改為共用 `_MealPlanApiBase`，以免新類別重跑原有測試。

略過 1 項：`test_t02_release.py:50`，本機歷史價格發布檔不隨程式碼發布（與本次修正無關）。

## 3. 阻擋與未完成項目

- **`test/t16-acceptance` 分支與其未提交的驗收測試、原 QA 報告，在本機找不到**（本機 repo 為全新 clone，遠端也沒有此分支）。原本 3 個失敗測試的斷言未能直接驗證；本次依描述的 2 類問題補上等價測試。取得該分支後應將其測試帶入本分支重跑。
- **真實資料驗收尚未完成**：測試中 T11 菜單與 T12 營養都是替身。仍需在測試／預備環境以真實食譜、營養與 T02 價格資料驗證：
  - 真實 T12 預覽 `dishes` 的克數加總是否與生成路徑的 `calculation.requirements` 一致；
  - 目錄式菜色身分（`identity_resolution = catalog-v1`）下，資料庫菜的昨日重驗；
  - 從真實採用到次日生成排除的完整鏈路（AC19、AC29–AC32），以及兩日回看與來源更新後快照不變。
- 前端尚未顯示預覽採用的超預算錯誤訊息與快照中的成本；新增的快照欄位均為加法，不影響既有畫面。
