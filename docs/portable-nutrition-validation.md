# 可攜真實營養發布驗證（2026-11-11）

本輪工作位於 `codex/1111-selected`，以已推送的 `61253f5` 為基底。下列紀錄為提交 PR 前實際執行的驗證。正式資料庫、V1、main 與原始工作簿未修改。

## 真實資料與初始化

- 資料包為 `release_data/nutrition-v1`，17 張白名單來源表：293 道食譜、2477 筆食材列、293 張封面、1996 筆營養觀測、3869 筆來源、2192 筆歷史 mapping。Mapping 筆數不代表全部核准；原 pending/rejected 狀態保持原樣。70 種食材以使用者逐項通過的精確來源清單接續。
- 壓縮資料 SHA256：`f63caca490d30d0fb3a97ee65d8dac65a10ce2b5713dd985afcdf6f90b6ed42e`。
- 政策 SHA256：`c1bac3cfba03d78fb71ebf4c19b402d2eb692897cef058c6d4ad8210aef37674`。
- PR 提交前另核對 Git index 內資料包、政策與五份來源設定的七個 SHA256，全部吻合。`.gitattributes` 保留這些檔案的原始位元組，避免 Windows／Linux 換行轉換破壞驗證；五份設定只有換行儲存方式改變，內容與已驗證數值不變。
- 原 SQL 來源備份 SHA256：`16f9a61c058e25237921a25d0906c2857bdbc40fd111aef74c86d1b59728af74`。先在既有隔離容器的新來源庫還原，再僅匯出白名單；未連線正式庫。原備份未隨交付提供，因交付只需明確白名單。
- 安裝執行正式 Alembic 至 `m1111simorder1`，核對欄位型別／PK／FK、逐表完整內容 digest、列數與政策綁定，建立隨機密碼受限應用角色。來源表無 INSERT／UPDATE／DELETE 權限。

## 實際執行結果

1. 全新 PostgreSQL `mealflow_portable_a_20261111`：安裝成功；由受限應用帳號完成真實資料驗證。
2. 另一名稱 `recipient_release_b_20261111`：直接使用 backend 交付映像內的程式與資料包完成安裝，沒有掛載宿主程式／資料包；只掛載忽略的私密輸出目錄。
3. 兩庫皆為 293 道／2477 列；2473 列預覽與正式營養計算的所有營養欄位、未知值及來源 trace 一致。4 列維持 `nutrition_recipe_derived_only`：石頭火鍋粉、破布子、話梅、鹽麴。沒有以合成值補齊。
4. 白飯 160 g：292.8 kcal、蛋白質 4.96 g（TFDA）；糙米飯 160 g：179.2 kcal、蛋白質 3.712 g（USDA）。950 份與 batch 食譜語意保留。
5. 18 張使用者活動表均為 0，包括 users、sessions、households、jobs、採用、生成結果、確認事件與 Sim orders。資料包沒有帳號／密碼／驗收訂單。
6. 對第二庫再次執行初始化，使用另一個尚不存在的輸出檔，確實以 `Database already exists` 拒絕，未執行遷移／匯入且未產生憑證檔。
7. 最終映像 `overcooked-portable-nutrition:verified` 建置成功；映像內直接執行 `python scripts/verify_portable_nutrition.py --env-file /app/.test-runtime/recipient_release_b_20261111.env --require-empty-runtime` 通過。

Python 回歸：**152 passed, 3 skipped, 40 subtests passed**。使用合成 SQLite／測試替身的是單元及回歸測試；上述兩次 PostgreSQL 安裝及營養計算使用真實資料。回歸範圍包含 portable policy、installer、verifier、營養匯入／整合、generation source、主食、meal calculation 及先前選取的 Sim checkout 測試。`TEST_DATABASE_URL` 未設定，3 個跳過項目不算通過。

## 邊界與重現

這證明新安裝能使用真實營養計算，不代表本輪重跑完整使用者 UI／worker 排程／採用／結帳，也不是另一台乾淨實體電腦驗收。價格、YOLO、T11 和飲食小幫手不在這份營養資料包內；沒有更動它們的既有狀態。

資料庫歷史食譜 hash `dd4f75…` 的原始 Excel 尚未找到；repository 同名檔 hash `6599f5…` 是另一份。保留歷史 source URI 與 SQL 來源證據，不把該 URI 宣稱成現在可取得的原始檔，也不以同名工作簿覆蓋。詳見 [初始化與來源說明](portable-nutrition.md)。

完整資料內容在安裝交易中逐表核對，runtime 另核 schema、版本、來源 metadata、mapping 與食譜 bindings；runtime gate 不宣稱每次請求都重算所有原始觀測內容雜湊。日後變更資料應建立新發布並重新驗證。
