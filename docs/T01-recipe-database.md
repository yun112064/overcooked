# T01 食譜資料庫

本模組將六個工作表唯讀匯入 PostgreSQL 的六張同責任資料表。`Food_ID` 是食材跨模組識別；菜名、食材名稱及別名只作顯示或解析，不取代 ID。

## 資料表

| 資料表 | 用途 |
|---|---|
| `source_archive` | 保存原始來源欄位、授權／版本／取得時間及匯入雜湊 |
| `recipes` | 食譜、標準菜色 ID、菜名別名、原始份數、方法與品質狀態 |
| `recipe_steps` | 有序烹調步驟及人工審核狀態 |
| `recipe_ingredients` | `Food_ID`、原始值、可驗證數量、單位、重量狀態及可調範圍 |
| `ingredient_aliases` | 每個 `Food_ID` 的標準名稱、無歧義別名及待審歧義別名 |
| `allergens` | 過敏原字典、參考依據與版本狀態 |

食譜資料表不包含 embedding、vector 欄位或向量索引。既有 pgvector extension 由其他 Ticket 使用，T01 不依賴它。

## 環境準備

在專案根目錄建立虛擬環境並安裝依賴：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

設定 PostgreSQL URL；不要提交 `.env`：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:<password>@localhost:5432/meal_planner"
```

套用 migration 前應先備份既有資料庫。本 Ticket 只需執行升級：

```powershell
alembic -c alembic.ini upgrade head
```

## 驗證與匯入

先執行 dry-run。此步驟不連線資料庫、不修改 Excel：

```powershell
python scripts/import_recipes.py `
  --excel data/recipe_dataset_1017.xlsx `
  --sources config/recipe_sources.json `
  --dry-run `
  --report docs/T01-acceptance-evidence.json
```

確認報告後再匯入：

```powershell
python scripts/import_recipes.py `
  --excel data/recipe_dataset_1017.xlsx `
  --sources config/recipe_sources.json `
  --database-url $env:DATABASE_URL `
  --report docs/T01-import-report.json
```

匯入以六張表的主鍵執行 upsert。同一檔案重跑會更新相同主鍵，不會新增重複紀錄。整批匯入使用同一交易；失敗時由呼叫端回滾。原始 Excel 永遠以唯讀方式開啟。

## 缺值及轉換規則

- 原始 `amount`、`unit`、`weight_g` 分別保存於 `source_amount`、`source_unit`、`source_weight_g`。
- 「適量／少許」且原文沒有數字時，正式 `amount` 與 `weight_g` 為 NULL。
- 範圍值保存於 `amount_min`／`amount_max`，不自行選中點。
- 只有原文直接提供明確 g 數且不是範圍時，`weight_g` 才標為 `source_explicit`。
- 顆、片、匙等換算重量若沒有依據，只保留 `source_weight_g` 並標為 `unverified_conversion`。
- 可調範圍沒有人工證據時保持 NULL，`adjustment_status=not_reviewed`。
- 推測出的烹調方法只保留為候選，`cooking_method_status=unreviewed`。

## 測試

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:<password>@localhost:5432/meal_planner_test"
$env:PYTHONPATH = "backend"
python -m unittest discover -s tests -p "test_t01_*.py" -v
```

測試以合成工作簿涵蓋：完整審核才合格、0 份數與上下限顛倒的可調範圍存成 NULL 並阻擋、單一儲存格數值錯誤只影響該食譜、重複菜只計一次、未審核不補數，以及資料庫匯入時不整批回滾、重複菜排在 primary 前也能匯入、重跑冪等、移除與重新編號的步驟／食材會同步。需要資料庫的測試只在設定 `TEST_DATABASE_URL` 時執行。真實工作簿不在 repo 中，仍需以 dry-run 報告另外留存證據。

### 2026-10-19 修正

- 份數 ≤ 0、可調範圍上下限顛倒：存成 NULL（原文保留），列為阻擋原因；先前會寫入違反 CHECK 的值，使整批匯入回滾。
- 食材數量、重量、營養欄位不是數字或為負數：只標記該列（`invalid_numeric_value:<欄位>`）並阻擋該食譜，不再讓整份工作簿失敗。結構欄位（序號、步驟編號）仍嚴格檢查。
- 重複菜：同名重複只有編號最小的 primary 可計入合格，其餘為 `duplicate_not_primary`；疑似重複在人工確認前只讓編號較小者計入（`suspected_duplicate_not_primary`）。
- 重跑匯入：工作簿中每道食譜的步驟與食材以工作簿為準整批取代，移除的列會刪除，步驟重新編號不再撞唯一限制；結果多回報 `deleted`。工作簿中已不存在的整道食譜目前不會被刪除。
- `duplicate_of_recipe_id` 先全部寫入食譜後再補上，不依賴工作簿列的順序。

### 標準食材字典、來源授權與整道刪除（2026-10-19）

- **FOOD 編號為全系統唯一的食材識別**：工作簿的「標準食材整合」工作表（1,992 筆）匯入 `ingredient_aliases`，食譜沒用到的食材也收錄。T07 過敏／禁吃、T08 已有食材、T09 辨識結果都改用同一套 FOOD 編號；食譜用到的 Food_ID 必須存在於字典且名稱一致，否則匯入失敗。
- **來源授權**：真實工作簿沒有授權欄位，以 `--sources config/recipe_sources.json` 補上 iCook 愛料理的授權、版本與取得時間（工作簿有填時以工作簿為準）。授權確認日為 2026-10-19；取得時間依檔名推定為 2026-10-17，授權文件編號待補。
- **整道刪除**：工作簿已移除的食譜，重新匯入時連同步驟、食材與不再使用的來源紀錄一併刪除；結果的 `deleted` 含隨食譜刪除的子資料。

### 人工審核紀錄與用量規則（2026-10-19）

- **以 Excel 為準，不修改工作簿內容。** 匯入工具只讀取工作簿；同義或異體字的不同 Food_ID（例如「番茄」與「蕃茄」）依工作簿原樣保留，不合併。
- **人工審核**：專案團隊確認全部食譜已審核。工作簿沒有審核欄位，以 `--reviews config/recipe_reviews.json` 套用來源、食譜、烹調方式、步驟與可調範圍的核准；紀錄綁定工作簿 SHA-256，工作簿一改就不再套用，需重新審核。工作簿已填的審核欄位優先。審核人姓名待補。
- **用量以 Excel 的 amount 為準**：
  - 原文有阿拉伯數字，或中文數字（一、兩、半、十二、一顆半）且與 amount 相同 → `source_exact`。
  - 「適量／少許」、概略用量（一大隻、十幾朵、數塊、六到七瓣）、原文沒寫數量 → 以 amount 作參考用量 `reference_amount`。
  - 原文數字與 amount 不同 → 仍採 amount，記 `raw_text_differs_from_amount` 提醒，不阻擋。
  - 只有 amount 也空白時才阻擋。
  - 解析前先去掉 `[分組]`、`(備註)` 與食材名稱，避免把「九層塔」「六份」當成數量。

### 真實資料匯入結果

`recipe_dataset_1017.xlsx`（SHA-256 `6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304`）在乾淨 PostgreSQL 匯入約 6 秒：293 道食譜、1,507 個步驟、2,403 筆食材、1,992 種標準食材、15 種過敏原；重跑結果相同。

**G01／AC05 通過：合格 281 道（目標 150）。** 用量狀態：原文明示 1,739 筆、參考用量 582 筆、範圍 82 筆。未計入合格的 12 道依 Ticket 規則保留，不是資料錯誤：同名重複非 primary 2 道、疑似重複非 primary 6 道（各組仍有一道計入）、步驟中找不到烹調方式 4 道。

## 查詢範例

只應向後續正式流程提供已通過資料：

```sql
SELECT recipe_id, standard_dish_id, recipe_name, original_servings
FROM recipes
WHERE is_eligible IS TRUE
ORDER BY recipe_id;
```

以 `Food_ID` 查詢食譜：

```sql
SELECT DISTINCT r.recipe_id, r.standard_dish_id, r.recipe_name
FROM recipes AS r
JOIN recipe_ingredients AS ri ON ri.recipe_id = r.recipe_id
WHERE r.is_eligible IS TRUE
  AND ri.food_id = 'FOOD001566';
```

查詢待人工處理資料：

```sql
SELECT recipe_id, duplicate_status, review_status, quality_issues
FROM recipes
WHERE is_eligible IS FALSE
ORDER BY recipe_id;
```

## 與 Ticket 11 的介接

- 指定菜與昨日同菜判斷使用 `recipes.standard_dish_id`。
- 菜名輸入先比對 `recipe_name`／`name_aliases`，結果回傳 `standard_dish_id`。
- 食材包含、排除及禁忌使用 `recipe_ingredients.food_id`，不以顯示名稱作最終判斷。
- 食材文字可透過 `ingredient_aliases` 解析為 `Food_ID`；`ambiguous_aliases` 不可自動決定食材。
- T11 查詢必須限制 `recipes.is_eligible IS TRUE`。目前真實資料為 0 筆，不得繞過此 Gate。

## T06 後續整合

T06 已提供 engine、Session 及 Alembic 基礎。本 Ticket補上共用 Base 與 model metadata。正式 PostgreSQL 環境仍需由 T06／部署負責人執行 migration、備份及連線驗證；本匯入工具不建立資料庫，也不執行破壞性操作。

