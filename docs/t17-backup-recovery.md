# T17 備份與還原操作

部署入口為 `scripts/prod-up.ps1`，每日備份入口為 `scripts/backup-daily.ps1`。業務、T11 知識與飲食小幫手資料庫各自備份，預設保留七日，最新一份永遠保留。

## 分工與部署資料

依 2026-11-13 澄清，使用者也會在自己的電腦測試可由 Git 或公開下載資源重建的環境；只有依賴另一位同學既有資料、特定硬體或正式部署環境的項目才交給該同學。Windows、GPU、外網與備份還原不一律排除，依使用者電腦的能力逐項執行。各執行者填寫自己環境的設定。操作起點見 [可自行重建的本機驗證](t17-local-validation.md)，分工與證據見 [驗收交接清單](t17-machine-handoff.md)。

| 項目 | 需要的資訊／決定 | 預設 |
| --- | --- | --- |
| 獨立備份位置 | `BACKUP_MIRROR_ROOT` 的完整路徑，位於另一顆實體磁碟或獨立儲存／同步位置 | 未設定；尚不符合獨立副本要求 |
| Windows 排程 | 確認每日備份時間、保留天數、是否登入後自動啟動；排程帳號須可使用 Docker Desktop 與備份磁碟 | 每日 03:30、七日、登入兩分鐘後啟動；錯過備份於可執行時補跑 |
| 實機驗收 | 目標電腦的 Windows／Docker Desktop 版本、GPU 型號及顯存、Ollama 模型清單與本機模型路徑，以及可測試的時段 | 此雲端環境沒有驗證真實 GPU／外網／Windows 排程 |
| 秘密保管 | 指定 `.env.prod` 與資料庫服務帳號密碼的安全保管方式，供換機還原使用 | 不寫入 Git、不放在 manifest、不需要貼到聊天中 |

一般修正不需要提供密碼或 API 金鑰；各執行者只需回報設定是否存在、檔案路徑與去除秘密的結果。AC24 依既有決議排除；快速通道網址仍會變動。embeddinggemma 的題庫重新評估與五人容量須記錄實際語料及硬體條件。

## 新備份的權限與一致性

- 保留資料表、欄位、schema、sequence 的原有 ACL；資料庫名稱變更時，將 CONNECT／CREATE／TEMPORARY 授權套用到新資料庫。
- manifest 的 `access.required_roles` 列出還原需要的 PostgreSQL 角色名稱，不保存密碼、不建立或重設登入帳號、不提高既有角色權限。
- 筆數、Alembic 版本、pg_dump 與知識來源 JSONL 使用同一個 exported PostgreSQL snapshot。備份期間可繼續註冊或寫入，新寫入不會混入該次資料庫快照。
- Data Root、來源／規則等檔案仍是檔案層備份，沒有 PostgreSQL 的交易快照保護；進行涉及檔案改寫的完整災難還原演練時，應先停止相關寫入程序，確保檔案與資料庫的對應。

## 在同一個 PostgreSQL 容器還原

先更新程式並重新產生一次備份。還原只允許不存在或沒有 public 資料表的目標庫；使用新庫與新目錄，保留舊庫。

```powershell
python -m ops.backup cloud --env-file .env.prod
python -m ops.restore <備份目錄> --target-database <新資料庫名稱> --data-target <新資料目錄>
```

同一容器中的角色通常已存在，ACL 會保留。檢查回報通過後，先以 `.env.prod` 原來的受限 app 帳號連到新庫，確認 schema gate、登入／家庭／餐食讀寫及必要的 column／sequence 權限，再切換 DATABASE_URL 的資料庫名稱及 Data Root。不要改用 postgres 執行應用程式。

## 換機或新 PostgreSQL 容器

pg_dump 不包含 cluster 的登入角色或密碼。如果目標缺少必要角色，還原在建立目標庫之前以 `MISSING_ROLES` 停止，列出缺少的名稱；既有帳號不受影響。

由維運者在目標容器本機建立對應的受限服務帳號，並在本機設定密碼。例如先進入 psql，再依錯誤列出的實際角色名稱逐一建立：

```powershell
docker exec -it meal-prod-db psql -U postgres -d postgres
```

```sql
CREATE ROLE <原服務帳號> LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
```

在 psql 使用 `\password <原服務帳號>` 互動設定密碼，避免把密碼寫入 SQL 歷史或貼到聊天。原安裝器的 app、crawler、management worker 帳號應分別建立；如果使用自行設定的群組角色／角色繼承，亦須依原本的身分管理政策重建。目標既有角色直接沿用，不重設其密碼。

完成後重跑還原。將新帳號密碼保存在本機秘密設定；`.env.prod` 更新為還原後資料庫的連線。核對該帳號仍為受限角色，並完成應用程式驗證後才切換正式服務。

## 修正前產生的舊備份

舊版使用 `--no-privileges`，資料庫內容可以還原，但原本的 ACL 無法從該 dump 找回。程式保留讀取舊備份的能力；業務庫驗證會回報「舊備份未保存應用程式權限」，以非成功狀態提醒不要直接切換。

優先在原庫仍可用時，以更新後程式重新備份。若只能使用舊備份，需依備份對應程式版本的受限權限政策補齊授權（參照該版本 `scripts/install_real_nutrition.py` 的 app／service grants），再以 app 身分實測；不要授予全表寫入或 superuser 來繞過限制。
