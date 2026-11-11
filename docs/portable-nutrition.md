# 可攜式真實食譜與營養資料初始化

`scripts/install_real_nutrition.py` 將經 SHA256 核對的真實資料包裝到**新建的 PostgreSQL 資料庫**，以目前 Alembic migration 建立 schema，再驗證同一份營養政策。資料庫名稱可自行指定，不綁定原本隔離驗收庫名稱。

此指令不更新既有庫；名稱已存在就拒絕。資料包只包含食譜、營養、原始來源與審核證據，不搬帳號、密碼、家庭、訂單、歷史餐食、job 或價格資料。T11／飲食小幫手索引與 YOLO 模型不是這個資料包的內容。

## 同名食譜來源必須區分

本資料包接續 SQL 中已固定的食譜來源，完整 SHA256 為 `dd4f75c1377299fdef858c98d8625cfbdee1e21dd7699f3f78e210048cf0289b`。歷史路徑搜尋尚未找到該原始 xlsx；`source_uri` 保留當時紀錄，並不承諾那個檔案現在仍存在。

儲存庫內同名 `data/recipe_dataset_1103_V3.xlsx` 的 SHA256 是 `6599f53b16a6b4bfc7f8aed4a44fd10758cf46b8d96a744faa36eefe56d43908`，**不能用這個同名檔重新匯入並覆蓋 SQL 發布**。本包使用經雜湊固定的 SQL 資料與逐列來源證據，保留 SQL 中兩個不同食材名稱及 36 處步驟文字編修差異；這些差異不因名稱相同而被自動合併。

## 資料包格式

預設資料夾 `release_data/nutrition-v1` 有三個檔案：

- `data.json.gz`：gzip JSON，格式 `{"tables": {"表名": [列物件, ...]}}`。date、datetime、Decimal 以字串保存，JSON 欄保留原始 object/list 與 NULL。
- `policy.json`：`approved_for_release` 的固定政策，包含來源 hash、recipe／line bindings、三個業務版本與完整核准證據。
- `manifest.json`：格式如下，`tables` 必須列全核心 14 表及實際附帶的審核表。

```json
{
  "schema_version": "portable-nutrition-v1",
  "schema_head": "m1111simorder1",
  "data_file": "data.json.gz",
  "data_sha256": "壓縮檔的SHA256",
  "policy_file": "policy.json",
  "policy_sha256": "政策檔的SHA256",
  "synthetic": false,
  "tables": {"recipe_data_releases": 2}
}
```

上例只示範欄位，不是可安裝清單。核心 14 表為 `recipe_data_releases`、`source_archive`、`ingredient_aliases`、`allergens`、`recipes`、`recipe_steps`、`recipe_ingredients`、`recipe_covers`、`nutrition_imports`、`nutrition_sources`、`food_nutrition`、`nutrition_source_links`、`nutrition_food_mappings`、`nutrition_data_releases`。另允許 `nutrition_product_sources`、`nutrition_source_candidates`、`t04_cooking_receipts`，其他表一律拒絕。

## 安裝

先安裝專案 Python dependencies，準備有 `CREATE DATABASE` 與 schema 建立權限的 PostgreSQL 連線。使用既有安全方式把連線設定到環境變數 `INSTALL_DATABASE_ADMIN_URL`；不要把密碼貼入指令紀錄或提交到 Git。

```powershell
python scripts/install_real_nutrition.py --database mealflow_my_release --output-env .test-runtime/mealflow_my_release.env
```

也可使用 `--bundle <資料夾>`。程式會先驗資料包／政策／設定檔 hash、白名單及每表列數，再檢查 database 不存在，執行 `CREATE DATABASE` 與 `alembic upgrade m1111simorder1`。匯入依反射的 FK 排序，檢查 ORM 與實際 schema 的欄位型別、PK、FK；將日期與 Decimal 字串還原為正確型別，不轉寫 JSON 來源證據。新 migration 自帶的 legacy 食譜 release 只在剛建立的資料庫內清除，確保資料包列數完整一致。

全部匯入在同一交易中，逐表核對 typed canonical SHA256（完整數值、原始來源與 links，非僅列數），最後執行 `reconciled_policy.validate_database`，驗證資料來源、映射內容與全部食譜／食材 bindings。bytea 欄使用 `{"__bytes_base64__": "..."}` 保存並還原。成功後才產生忽略目錄 `.test-runtime` 內的新 `.env`；輸出檔已存在也會拒絕，連線 URL 不印到終端。

API 與 worker 必須共用這份 env 的 `DATABASE_URL`、`NUTRITION_POLICY_MANIFEST`、manifest SHA256、`GENERATION_RECIPE_RELEASE_ID`、`GENERATION_NUTRITION_IMPORT_ID`、`GENERATION_FOOD_MAPPING_VERSION`。政策路徑是此安裝位置的絕對路徑；搬移專案後要重新設定路徑，但檔案內容與 hash 不可自行更改。

安裝後建立全新受限應用角色，預設名稱為新庫名加 `_app`（長名稱會縮短）；可用 `--app-role` 指定另一個新名稱。角色已有就拒絕，不重設現有密碼。新密碼隨機生成，角色沒有 superuser、createdb、createrole、bypassrls，來源表僅能 SELECT，必要操作資料表才給 DML。程式以該角色重新驗證政策與權限，輸出 DATABASE_URL 使用此角色，沒有把 admin 連線當成 API／worker 預設。勿將 `.env` 隨原始碼外送。

使用包含 `release_data` 與 installer 的 backend 映像安裝時，設定工作目錄 `/app`：

```text
docker run --rm --workdir /app --env INSTALL_DATABASE_ADMIN_URL --mount type=bind,src=<本機忽略目錄>,dst=/app/.test-runtime <backend-image> python scripts/install_real_nutrition.py --database <新庫名> --output-env /app/.test-runtime/<新庫名>.env
```

容器須另接到目標 PostgreSQL 所在網路。`--env INSTALL_DATABASE_ADMIN_URL` 從呼叫程序的環境傳入，指令列不放密碼。映像內政策路徑為 `/app/release_data/nutrition-v1/policy.json`；宿主安裝的路徑不同，容器執行 API／worker 時需使用映像內路徑或正確的唯讀掛載，且 SHA256 一致。

輸出的 `.env` 是 dotenv 格式，值含有必要的引號／跳脫，供 Compose 或 dotenv 載入；不要直接當成 `docker run --env-file` 的原始格式，Docker 可能把引號保留成密碼的一部分。安裝後以 verifier 直接安全載入，不用手工 export 密碼：

```powershell
python scripts/verify_portable_nutrition.py --env-file .test-runtime/mealflow_my_release.env --require-empty-runtime
```

容器中驗證也將該檔案掛載，再傳 `--env-file /app/.test-runtime/<新庫名>.env` 給 Python verifier；不要把此旗標誤放到 `docker run`。它會在載入應用設定前解析 dotenv，拒絕來源／版本漂移，檢查空白 runtime 與預覽／正式路徑的營養一致性，並保留缺來源的阻擋列。

若建立新庫後失敗，新庫會保留供診斷，不自動 drop、不重試覆蓋。修復原因後選另一個全新名稱重新安裝。此初始化通過只代表真實營養發布可驗證，不等於爬蟲、圖片、模型服務、價格或完整 UI 驗收通過。
