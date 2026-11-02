# 統一資料庫結構與資料整合規格

本次已統一**程式中的 schema**，尚未統一或寫入實際資料。雲端主資料庫與團隊電腦知識資料庫依原架構保持不同服務責任；「統一一個版本」指共同發布識別與來源契約，不把知識向量硬搬進家庭資料庫。

## 結構定版

- 基底沿用 main Alembic 鏈，選入 V1 後續結構修訂與 merge revisions；唯讀解析結果是單一 head：`c1e7a4b9d260`。
- 雲端 29 張 ORM 表的 PostgreSQL DDL 可編譯，逐欄位／FK 清冊見 [schema-inventory.json](schema-inventory.json)。T02 三張以 migration-only SQL 管理的表另計，不會誤稱只有 29 張實體表。
- 知識庫由 `knowledge_rag/schema.sql` 管理：knowledge_records、source_articles、knowledge_indexes、knowledge_embeddings；不加入家庭 DB 的 Alembic，未執行建表。
- T13 舊分支的另一套 `recipes` 建表及 `retail_*` 表不合入，成本改共用 T02 snapshot。
- 新 migration 檔包含歷史資料回填（例如 legacy release）。只保存結構演進程式，**本次沒有執行它們**，後續需先審查再跑。

## 資料責任

| 資料群組 | 統一後位置 | 主鍵／版本原則 |
|---|---|---|
| 食譜發布 | recipe_data_releases ＋ source_archive／recipes／recipe_steps／recipe_ingredients | recipe_release_id、原始來源 hash、標準菜色 ID、逐列來源；不以名稱直接拼接不同發布 |
| 共用食材／過敏 | ingredient_aliases／allergens | Food_ID 保持原識別；別名與營養替代對照分開 |
| 數值營養 | nutrition_imports／food_nutrition／nutrition_sources／nutrition_source_links | import_id 綁定 recipes／catalog／nutrition 三份 hash；同 Food_ID 可有不同生熟／方式／前處理觀測 |
| 營養映射 | nutrition_food_mappings ＋ mapping_version | 綁 recipe_release_id 與 nutrition_import_id；人工審核狀態與來源保留，不把 pending 當 approved |
| 烹調與估算 | conversion_factor_estimates、tfda_food_mappings、nutrient_fill_estimates、t04_cooking_receipts | 官方觀測、人工映射、LLM 估算分層；收據綁相同 import／nutrition／source／食譜列 |
| 零售價格 | price_collection_runs／price_quote_snapshots／retailer_product_mappings | 食材與商品映射、零售商、包裝／單位、觀察／效期、幣別、地區；T13 不另保存一套價格 |
| 帳號／家庭／草稿／工作 | app/db/models.py | 家庭範圍、UUID、revision、冪等鍵；不是來源資料發布的一部分 |
| 結果／已採用快照 | generation_results／adopted_meal_plans 等 | 固定當時食譜、營養、價格、知識、目標規則版本，來源更新不回寫舊餐 |
| 知識 | 獨立 pgvector DB | source_version、review、index_version、embedding_model／維度；引用與原文證據保留 |

## 下一階段的單一發布識別

正式資料版本應是一份 manifest，明確綁定：

```text
release_id
  recipe_release_id + recipe_source_sha256
  nutrition_import_id + 三份來源檔名／sha256
  food_mapping_version
  target_rule_version + rule_file_sha256
  cooking_policy_version + receipts／estimates 版本
  price_source_policy + snapshot cutoff／效期規則
  knowledge_source_version + published_index_version + embedding_model
```

本次沒有填入假 ID 或自動沿用候選 v2 的歷史核准。`GENERATION_RECIPE_RELEASE_ID`、`GENERATION_NUTRITION_IMPORT_ID`、`GENERATION_FOOD_MAPPING_VERSION` 待選版；T12 獨立預覽缺少 nutrition_import_id 時回報 `NUTRITION_VERSION_REQUIRED`，不自動取最新批次。`T12_PREVIEW_CONVERSION` 預設 `t04`；factor_one 只保留可選架構。

## 資料整合順序（本次未執行）

1. 確認 SOURCES 清冊、原始檔 hash／授權／審核、正式來源版本與生熟計算政策。
2. 備份既有環境並建立獨立 staging PostgreSQL／knowledge DB；先檢視 migrations 中的結構與資料回填。
3. 用指定主版建立一批食譜與一批營養來源，保留原始來源、排除原因、未知值；不可混用 1017／1027／1102 食譜列。
4. 對映 Food_ID、樣品、生熟、烹調方式、前處理及重量基準；不明與多義列維持待審。
5. 以同一批食譜驗 T11 查詢／昨日去重、T12 全食材與 ±10%／n 倍、T13 包裝與比例成本；官方值與估算／補值分開核對。
6. 知識資料獨立審核、建索引與發布，引用綁定相同 Food_ID；價格依有效條件更新，舊價不得當作現價。
7. 通過來源與端到端驗證後產生不可變 manifest，才設定 runtime 版本並啟動服務。舊資料保留可回復，不覆寫歷史餐食快照。

## 架構驗證方式

`scripts/audit_branch_architecture.py` 僅讀 Git tree；`scripts/verify_v3_source_freeze.py` 僅比對已追蹤原檔。Alembic head／ORM DDL 檢查沒有連資料庫。PostgreSQL 升降版、pgvector、容器與正式來源驗證尚未執行，不能由 SQLite 結果替代。
