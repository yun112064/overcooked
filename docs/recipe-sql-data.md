# 食譜資料庫安裝與版本規則

正式環境、開發環境、測試與查價流程一律從 PostgreSQL 讀取食譜資料，不讀取 Excel。

## 唯一正式版本

- 版本清單：`data/recipe_data_manifest.json`
- SQL 資料：`data/recipe_data_1027.sql.gz`
- 目前 release：`recipe-1027-e61bbd81`
- 原始資料 SHA-256：`e61bbd814682fd9d95e5a3cd705728d0a4c062dcdc211aaa4af62db732d2e1ca`

manifest 同時記錄 SQL 檔雜湊與各資料表筆數。安裝前會驗證雜湊；安裝後會核對筆數、外鍵、release 一致性及唯一啟用版本。任何一項不符都會停止啟動，不會讓後端使用半套資料。

## 全新安裝

```bash
alembic -c alembic.ini upgrade head
python scripts/bootstrap_recipe_data.py
```

PowerShell 開發流程直接執行 `scripts/start-dev.ps1`；Docker Compose 啟動後端時也會先完成相同步驟。重複執行安全，已安裝且核對正確時會回傳 `already_installed`。

## 資料流

```text
版本化 SQL seed
  -> recipe_data_releases（決定唯一 active release）
  -> recipes
     -> recipe_steps
     -> recipe_ingredients -> ingredient_aliases
  -> T02 查價、T07–T20 功能
```

功能查詢必須透過 `recipe_release_id` 連到目前 active release。不得以檔名、Excel 路徑或自行建立的另一份食譜表決定資料版本。

## 更新下一版資料

新資料不可直接覆蓋現有 SQL 檔。必須產生新的 release ID、SQL seed 與 manifest，先在全新 PostgreSQL 驗證 migration、筆數與功能回歸，再由獨立 PR 審查。舊 release 留在版本紀錄中，以便追蹤與回退。

目前 1027 版本的步驟人工審核狀態仍為 `pending`；這是資料狀態，不可在匯入時自動宣告為已審核。
