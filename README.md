# 今天吃啥：家庭餐食規劃系統

目前整合 T06 系統骨架、T03 營養目標與 T04 食譜營養估算。各模組的程式、資料與驗收狀態請依各自文件核對。

| 目錄 | 用途 | 文件 |
|---|---|---|
| `nutrition_target/` | T03：個人、餐別及家庭合計營養目標，含規則、測試與交接文件 | [T03 README](nutrition_target/README.md) |
| `nutrition_model/` | T04：TFDA 食材與烹調營養估算 | [T04 README](nutrition_model/README.md) |
| `backend/`、`frontend/` | T06：FastAPI／React 系統骨架與正式日誌 | 設定見 `.env.example`、`compose.yaml` |
| `migrations/` | 資料庫遷移 | `alembic.ini` |
| `tests/` | T06 日誌與隱私測試；T03 測試已移入模組 | CI 見 `.github/workflows/t06-ci.yml` |
| `docs/` | 跨模組決議及 T04 驗收／交接記錄 | [決議與整合待辦](docs/T04-T06-merge-pending.md) |

T03 已決議以 Spec 為準，使用版本化規則 `hpa-dri8-mvp-0.3.0` 的目標限制。LLM 烹調係數用途及採用所需的必要營養欄位仍待決議。

**程式碼合併與目錄整理不代表 G03／G04／G07 通過，也不代表主系統 API、容器或營養方案採用驗收完成。** 詳細限制與實際測試記錄見上方文件。

T03 快速驗證，在專案根目錄執行：

```sh
python -m unittest discover -s nutrition_target/tests -t . -v
python -m nutrition_target --rules nutrition_target/rules/hpa-dri8-mvp-0.3.0.json --input nutrition_target/tests/fixtures/member-A.json --operation daily
```
