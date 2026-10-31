# T12 改接 feature/data-import（2026-10-31）

來源儲存庫：`https://github.com/yun112064/overcooked`；來源分支 `feature/data-import`；固定提交 `b6ad14f2aee0edc81ac8a1ea31c4297d5f546d55`。本次只在 T12 分支增加接線及必要 schema，未合併整條來源分支、未修改 main 或來源分支，也未執行任何正式資料庫寫入／migration。

從來源提交原樣帶入 `backend/app/models/nutrition.py`（Git blob `b17d7640121349d8a39535498ba84beb12b95022`）及 `migrations/versions/0d52b9a6c831_versioned_food_nutrition.py`（Git blob `6e4dbaf2ae66d645cde9a1531959e52f263c7719`）；models 匯出也與來源分支一致。日後合併時沿用同一個 migration，不另造相同資料表。食譜資料及匯入器的完整功能由 feature/data-import 負責，這次不複製或改寫該分支的食譜審核紀錄。

## 新的資料路徑

`validate_database_menu()` 預設讀取新版資料：

1. 指定 `nutrition_import_id`，由 `nutrition_imports` 確認三份檔案名稱／SHA-256 與 `food-nutrition-import-v1`。
2. 每個實際食材列指定 `ImportedNutritionSelection`；查詢 `food_nutrition` 的 nutrition_id、同一 import_id、Food_ID，並精確核對生熟、烹調方式及前處理。不自動取最新版本或另一種方式，不依名稱猜樣品。
3. 透過 `nutrition_source_links` 選實際納入計算的 TFDA 來源。只有一筆時可唯一判定，多筆時須指定 `source_id`；USDA、食譜倒算及排除的來源不能當成 TFDA。讀取選定來源的原始每100g數值，不採用可能混合來源的 All 彙整值。前處理／生熟／方式也必須與該原始來源一致。
4. 把每一列的來源資料直接放進後端 NutritionPolicy 的 `line_nutrition_records`，按原有精確公式計算。相同 Food_ID 可在兩個食材列使用不同的 TFDA 生熟樣品；不會因全域對照表覆寫其中一種。
5. T04 回傳係數若要接入此路徑，`T04CookingResult` 另外帶相同 `nutrition_import_id`、`nutrition_id`、`nutrition_source_id`，不重用另一批資料的收據。

對外介面沒有改變熱量／蛋白質容差、縮放順序或缺資料阻擋規則。所有匯入紀錄均唯讀。新版資料表或連線故障是 `NUTRITION_DATA_UNAVAILABLE`；沒有相符紀錄、來源多義、來源排除或生熟不明是明確的資料問題。缺少任一必要食材的資料，整餐不可當成已驗證。

## 呼叫範例

```python
from nutrition_planning import ImportedNutritionSelection, validate_database_menu

result = validate_database_menu(
    db_connection,
    selected_recipe_ids,
    participations=participations,
    target_service=target_service,
    policy=backend_policy,
    profiles=reviewed_line_profiles,
    nutrition_import_id=selected_import_id,
    nutrition_selections={
        line_id: ImportedNutritionSelection(
            nutrition_id=selected_nutrition_id,
            cooking_state=exact_state_from_import,
            cooking_method=exact_method_from_import,
            source_id=selected_included_tfda_source_id,
            preparation=exact_preparation_from_import,
        ),
        # 所選菜單的每個食材列均需提供，包含油、調味料和家中已有食材。
    },
)
```

範例變數須由後端實際查詢取得；不是可以直接套用的營養 ID 或核准旗標。呼叫端仍需提供已確認的重量基準、單位與烹調資料。舊版重現才指定 `nutrition_source='legacy_tfda'`；新版缺資料不能用它繞過阻擋。

## 目前可驗證的資料與限制

來源分支文件記載食譜檔 SHA-256 `6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304`、done.xlsx `5611c9f74f475570c8538d6972bf3f8219c4a861dbea075f720f02ec4adccd10`、All_Food `cb0c0f938a26fdd1258cb05b52e3a460d4f146be259048e55d0b7e63e600a959`。本機食譜檔相符，但可找到的 All_Food 為 `b1ccb1b9d9e89c559580571af8018608b442919df4136ed61cf737e928a6dd5f`，不是分支文件記錄的新版本；done.xlsx 及已匯入資料庫目前未取得，Docker 也未啟動。因此本次完成新資料表接線及合成資料回歸，不宣稱正式的 1,996 筆營養紀錄已在本機重新驗算。取得資料庫後，只讀指定匯入版進行實際案例核對即可，不需修改來源分支。

`nutrition_planning/tests/test_imported_nutrition.py` 覆蓋新資料表、缺版次、版本／狀態／方式／前處理不符、來源排除、USDA／食譜來源、TFDA 多來源、缺值與0、同Food不同樣品、舊檔刪除後仍計算，以及 T04 係數的同版本綁定。這些測試使用合成數值與來源標示；它們驗證計算契約，不替代正式資料來源適用性審定。
