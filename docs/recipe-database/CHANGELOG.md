# 食譜資料庫開發紀錄

## 2026-10-17 — `feature/recipe_database`

### 目標
在不修改團隊現有 FastAPI、營養模組及既有 Migration 的前提下，建立結構化食譜資料庫和可重現的 Excel 匯入流程。

### 新增
- `migrations/versions/3d4a4d3b0e61_create_recipe_tables.py`：以 `c4afb1c5751e` 為前置版本，新增六張食譜資料表及必要索引、外鍵與順序唯一約束。
- `scripts/import_recipes.py`：提供 Excel 預檢與 `--apply` 正式匯入；預設不寫入資料庫；只允許匯入至六張皆為空的食譜表，使用資料庫交易避免部分提交。
- `.gitignore`：忽略 `/local_data/` 內的本機原始資料。

### 資料清理
- 原始 `recipe_dataset_1016_combine_3.xlsx` 的 `recipe_ingredients.sequence` 存在小數排序值（例如 5.01）。
- 另存 `recipe_dataset_1017_combine_3.xlsx`，將受影響食譜的食材排序轉為整數，同時保留原有順序；原始檔不覆蓋。
- 1017 工作簿為本機開發資料，未納入 Git。

### 環境問題與排除
- 修正本機 `.env` 的資料庫密碼及 `DATABASE_URL` 結構，使用 `127.0.0.1:5432` 避免 localhost 的 IPv6 逾時；未修改團隊連線程式。
- Alembic 成功自 `c4afb1c5751e` 升至 `3d4a4d3b0e61`。

### 驗證紀錄（使用者於 2026-10-17 回報）
| 資料表 | 記錄數 |
|---|---:|
| recipe_source_archive | 293 |
| recipes | 293 |
| recipe_ingredients | 2,403 |
| recipe_steps | 1,507 |
| recipe_ingredient_aliases | 209 |
| recipe_allergens | 15 |
| **合計** | **4,720** |

### 待辦
- 執行並保存外鍵／重複順序驗證 SQL 的結果。
- 依需求增加食譜查詢 API 及測試（目前尚未納入）。
- 公開發布前完成第三方食譜內容授權及來源審查。
- 零售價格資料功能另開分支，不與食譜資料庫 Commit 混用。
