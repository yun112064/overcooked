# 食譜、食材字典與營養資料匯入

`scripts/import_food_workbooks.py` 一次讀取三份 Excel，在同一個資料庫交易內匯入食譜、食材字典與營養版本。Excel 只讀取，不修改。`done` 的字典須與食譜檔一致，故不重複建立食材。

食譜依清理後的菜名去除完全同名重複，每組採用 `recipe_id` 最小、原本列為主食譜的一筆；只標為「疑似重複」的不同菜名維持原樣。單獨食譜匯入工具也套用相同規則，重跑不會重新加入被略過的版本。報告的 `recipe_quality.import_selection` 記錄保留／略過的 ID、兩張工作表的實際列號及來源網址；`recipe_quality.counts` 保留完整工作簿的原始品質統計。

已存在於資料庫的同名次要版本，會連同其食材明細及步驟一起移除；對應的重複來源封存列也移除。營養表內引用這些食材列的來源快照仍然保留，維持既有營養版本的追溯。

## 資料結構

沿用 `ingredient_aliases.food_id` 作為食材主檔。新增 migration `0d52b9a6c831`：

| 資料表 | 用途 |
|---|---|
| `nutrition_imports` | 三份來源檔名、SHA-256、匯入時間與完整品質報告 |
| `food_nutrition` | 每個食材的各種生熟／烹調狀態及每 100g 七項營養 |
| `nutrition_sources` | TFDA、USDA 原始來源列，以及彙整值引用的食譜食材列 |
| `nutrition_source_links` | 每筆營養紀錄引用的來源，以及是否納入該筆計算 |

`nutrition_id` 才是營養紀錄主鍵；同一個 `food_id` 可以有多筆紀錄。保留原始生熟狀態和烹調文字，包括「未判定」「未註明」及複合狀態，不擅自推定為生或熟。

匯入 ID 綁定三份工作簿的 SHA-256 與匯入格式版本。同樣檔案重跑不新增營養紀錄；不同版本另存，不覆蓋先前的數值或來源。每筆紀錄也保存來源工作表列號與原始欄位。

空白營養值存 `NULL`，原有 0 保持 0。`nutrition_complete` 僅代表七項數值都有值，不代表數值已審核或適合任意料理。食譜衍生營養標記 `recipe_derived_nutrition_unreviewed`，保留原始用量和營養欄位，與 TFDA／USDA 區別。

## 預檢與匯入

先執行預檢；不會連線資料庫：

```bash
python scripts/import_food_workbooks.py \
  --recipes '../recipe_dataset_1017 (1).xlsx' \
  --catalog '../done (1).xlsx' \
  --nutrition '../All_Food_ID_1018 (1).xlsx' \
  --sources config/recipe_sources.json \
  --reviews config/recipe_reviews.json \
  --report .dev/food-workbooks-dry-run.json \
  --dry-run
```

備份資料庫後執行 `alembic upgrade head`，再以相同指令移除 `--dry-run`，並把報告改為 `.dev/food-workbooks-import.json`。資料庫連線讀取環境變數或專案根目錄 `.env` 的 `DATABASE_URL`；環境變數優先，也可指定 `--database-url`。

來源與人工審核設定使用專案既有紀錄。人工審核僅在食譜檔案雜湊吻合時套用，程式不會替新的工作簿補上核准。

不存在於字典的營養 Food_ID、無法追溯的來源 ID、非法數字、重複的同食材／狀態／方式組合會使匯入停止。來源筆數與列出的 ID 數量不一致則保存原值並列入品質報告。

整批資料寫入失敗會回滾。除了本次同名去重明確略過的版本，若匯入將刪除資料庫既有的其他食譜，或改名既有食材主檔，工具會拒絕執行。不同營養版本不會互相刪除；本工具不執行 migration。

## 查詢方式

`find_nutrition_candidates()` 必須指定 `import_id`、`food_id`、原始生熟狀態及烹調方式，採完全符合查詢。找不到時回傳空清單，不會自動改用其他生熟或料理方式。

```python
from app.services.nutrition_import import find_nutrition_candidates

rows = find_nutrition_candidates(
    session,
    import_id=import_id,
    food_id="FOOD001564",
    cooking_state="已熟製",
    cooking_method="烤",
)
```

本次完成資料儲存及明確條件查詢。既有 T04 營養計算仍讀取自身的 JSON 資料，尚未改成使用這批營養表；接入前仍需處理狀態對應、來源採用規則及生重／熟重條件。

## 三份實際檔案的驗證結果

- 完整食譜工作簿：293 道、1,507 筆步驟、2,403 筆食材明細；字典 1,992 筆。
- 同名去重後實際匯入：291 道食譜、291 筆來源、1,494 筆步驟、2,380 筆食材明細。
- 套用既有且雜湊相符的審核紀錄後，保留的食譜中有 281 道合格、10 道仍有品質問題。
- 營養彙整：1,996 筆，涵蓋 1,914 個 Food_ID；65 個 ID 有多種狀態／方式。
- 原始來源 2,587 筆；另保存引用到的食譜食材來源 1,282 筆，來源關聯共 3,848 筆。
- 七項營養完整的紀錄 742 筆；缺值保留空值。
- 字典的 78 個 ID 沒有營養紀錄；81 筆營養名稱與字典不同。原始編號及名稱均保留，完整清單在報告的 `reconciliation`。
- 食譜實際使用的 434 個 Food_ID 都有營養紀錄；「有紀錄」不保證生熟／烹調條件符合或營養完整。
- 3 筆計算來源筆數不一致，列入 `reconciliation.source_count_mismatches`。

同名去重選擇（列號包含第 1 列標題，`source_archive` 與 `recipes` 的列號相同）：

| 食譜 | 保留來源／食譜 | 保留 Excel 列號 | 略過來源／食譜 | 略過 Excel 列號 | 採用來源 |
|---|---|---|---|---|---|
| 三杯雞 | RAW0018／R0018 | 19 | RAW0192／R0192 | 193 | https://icook.tw/recipes/14943 |
| 皮蛋瘦肉粥 | RAW0066／R0066 | 67 | RAW0068／R0068 | 69 | https://icook.tw/recipes/29071 |

## 測試

```bash
PYTHONPATH=backend python -m unittest discover -s tests -p 'test_food_workbooks.py' -v
```

涵蓋多狀態保存、缺值與 0、重跑、新版本歷史、整批回滾、字典差異、來源排除與筆數差異、食譜衍生來源、錯誤來源、非法數字、拒絕刪除其他食譜、精確條件查詢及 migration 升降級。
