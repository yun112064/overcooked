# 食譜資料庫實作與驗收

## 範圍
本階段僅新增食譜 PostgreSQL Schema、Excel 匯入工具與開發紀錄；不變更團隊既有 FastAPI、營養模組、pgvector 使用方式及主分支。Alembic head：`3d4a4d3b0e61`；前置版：`c4afb1c5751e`。

## 資料表
| 資料表 | 用途與主要關聯 |
|---|---|
| `recipe_source_archive` | 原始來源資料，主鍵 `raw_id` |
| `recipes` | 食譜主資料，主鍵 `recipe_id`，`raw_id` 可關聯來源表 |
| `recipe_ingredients` | 食材與用量，`recipe_id` 關聯食譜；同食譜 `sequence` 不可重複 |
| `recipe_steps` | 步驟，`recipe_id` 關聯食譜；同食譜 `step_no` 不可重複 |
| `recipe_ingredient_aliases` | 食材標準名稱與別名；目前 `ingredient_id` 未設外鍵 |
| `recipe_allergens` | 過敏原參考詞條 |

匯入工作表對應：`source_archive`→`recipe_source_archive`、`recipes`→`recipes`、`recipe_ingredients`→`recipe_ingredients`、`recipe_steps`→`recipe_steps`、`ingredient_aliases`→`recipe_ingredient_aliases`、`allergens`→`recipe_allergens`。

## 使用方式（專案根目錄 PowerShell）
先確認 `.env` 使用本機 PostgreSQL 連線；勿提交真實密碼。1017 Excel 存於 `local_data/`。

```powershell
# 預檢（新資料庫六張食譜表全空時使用）
.\.venv\Scripts\python.exe scripts\import_recipes.py --file local_data\recipe_dataset_1017_combine_3.xlsx

# 正式匯入（只執行一次）
.\.venv\Scripts\python.exe scripts\import_recipes.py --file local_data\recipe_dataset_1017_combine_3.xlsx --apply
```

匯入完成後，原匯入工具偵測到任何既有食譜資料就會拒絕再次預檢／匯入；這是避免覆寫與重複插入的保護機制，不應以清表方式重試。

## 驗收 SQL
```sql
SELECT 'recipe_source_archive' AS table_name, COUNT(*) FROM recipe_source_archive
UNION ALL SELECT 'recipes', COUNT(*) FROM recipes
UNION ALL SELECT 'recipe_ingredients', COUNT(*) FROM recipe_ingredients
UNION ALL SELECT 'recipe_steps', COUNT(*) FROM recipe_steps
UNION ALL SELECT 'recipe_ingredient_aliases', COUNT(*) FROM recipe_ingredient_aliases
UNION ALL SELECT 'recipe_allergens', COUNT(*) FROM recipe_allergens;

-- 食材的孤兒食譜外鍵（預期 0）
SELECT COUNT(*) AS orphan_ingredients
FROM recipe_ingredients i LEFT JOIN recipes r ON r.recipe_id = i.recipe_id
WHERE r.recipe_id IS NULL;

-- 步驟的孤兒食譜外鍵（預期 0）
SELECT COUNT(*) AS orphan_steps
FROM recipe_steps s LEFT JOIN recipes r ON r.recipe_id = s.recipe_id
WHERE r.recipe_id IS NULL;

-- 食材順序重複群組數（預期 0）
SELECT COUNT(*) AS duplicated_ingredient_orders FROM (
  SELECT recipe_id, sequence FROM recipe_ingredients
  GROUP BY recipe_id, sequence HAVING COUNT(*) > 1
) t;

-- 步驟順序重複群組數（預期 0）
SELECT COUNT(*) AS duplicated_step_orders FROM (
  SELECT recipe_id, step_no FROM recipe_steps
  GROUP BY recipe_id, step_no HAVING COUNT(*) > 1
) t;
```

PowerShell 可用 `docker compose exec db psql -U postgres -d meal_planner -c "...SQL..."` 執行單段 SQL。

## 已驗證與限制
- 使用者回報六張表匯入後分別為 293、293、2,403、1,507、209、15 筆，合計 4,720 筆。
- Alembic 版本已確認為 `3d4a4d3b0e61`。
- 外鍵孤兒與重複順序 SQL 尚待實際執行，不應視為已通過。
- 此階段尚未加入 REST 食譜查詢 API、部署環境驗收或零售價格來源。
- 原始食譜可能含第三方著作；來源與使用權限需審核，不應將原始 Excel 直接推送至公開 repo。
