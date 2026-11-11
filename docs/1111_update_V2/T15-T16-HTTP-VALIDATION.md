# T15／T16 真實 HTTP 準備驗收紀錄

2026-11-11，分支 `1111_update_V2`。這是代理執行的工程檢查，使用者尚未完成畫面驗收。

執行前在後端容器以 READ ONLY 查詢確認 `current_database()` 精確為 `meal_planner_1102_v3_1111_update_v2`。HTTP 僅使用主站 8240 與 Admin 8242，未改正式資料庫或來源表。

## 真實資料與結果

延續本輪 `real-flow.json` 已成功採用的 11/11 晚餐，另建立 11/12 問卷與採用；不是等待兩天，也不是複製歷史資料。兩日使用真食譜 R0036「簡單蒜頭雞(電鍋版)」、既有核准營養來源及真實蒜頭報價；雞腿肉為使用者聲明已有。成員／家庭及問卷是標記為驗收用途的測試資料，沒有合成食譜、營養或價格。兩個新工作皆 `database_menu`，未執行 AI 菜單回退。

| 檢查 | 實際結果 |
| --- | --- |
| 前日排除 | 11/12 未指定時，前日已採用 R0036 出現在 `excluded_dishes`，不在選中菜單。 |
| 指定例外 | 明確指定 R0036 可選入，回傳 catalog-v1 指定例外資訊。 |
| T15 版本資格 | 同問卷 v1/result1 曾成功；預算 1000 改為 1001 後，舊結果 `adoptable=false`，v2/result2 為 true。 |
| 舊結果採用 | 改版後 POST 舊結果回 409 `VERSION_CONFLICT`，未採用。 |
| 正式採用及冪等 | v2 採用 201；同鍵重送 200、created=false、同一 plan ID，當日歷史與 Admin 六個事件完全相同。 |
| 兩日回看 | `/meal-plans` 日期、各日詳情及兩日 `/trend` 都包含 11/11、11/12。 |
| 跨家庭 | 另一驗收家庭 GET 餐單／問卷／job 與 POST 採用皆 404。 |
| 快照重讀 | 兩筆完整 snapshot 的 canonical JSON SHA256 在初次流程前後相同；追加真報價更新後的檢查見下節，仍未驗政策發布／回復。 |

兩日保存的能量均為 659.925 kcal、蛋白質 76.51125 g，趨勢的 cost_total 為比例成本 18.5625 元；不能將該比例成本當整包實付，商品報價仍為各自快照與 Sim 的欄位。

識別與本機證據：

- Day 1 plan：`e443e6cb-7d01-4ff4-8757-babebbdd3a84`。
- Day 2 request：`fca6ed29-cec0-43ff-9c4a-4ab856e030f3`；plan：`e9ccac28-ed91-499a-87c4-ee0cee005e25`。
- 工作 v1：`8aa5cd21-7c9b-4d85-b154-1ca6cf313ba2`；v2：`bf2f97bf-4a11-41e0-baef-3db4d7b34342`，皆 succeeded。
- 完整 JSON：`.test-runtime/1111_update_V2/t16-acceptance-resumed.json`，`completed=true`。記錄 snapshot hash、版本、價格來源、前日上下文、Admin 事件與日期趨勢；不含登入密碼。
- 第一段 `.test-runtime/1111_update_V2/t16-acceptance-run1.json` 為部分紀錄。為配合另一代理本機模型測試，停止 HTTP 執行程式，但沒有取消已送出的工作；兩個工作都完成後，使用 `--resume` 接續既有結果，沒有重送生成。

執行工具為 `deploy/1111_update_V2/t16_acceptance.py`；已跑 CLI／語法檢查及實際 HTTP，非僅腳本存在。一般命令：

```text
python deploy/1111_update_V2/t16_acceptance.py --flow .test-runtime/1111_update_V2/real-flow.json --output .test-runtime/1111_update_V2/t16-acceptance-run1.json
python deploy/1111_update_V2/t16_acceptance.py --flow .test-runtime/1111_update_V2/real-flow.json --resume .test-runtime/1111_update_V2/t16-acceptance-run1.json --output .test-runtime/1111_update_V2/t16-acceptance-resumed.json
```

`--resume` 僅供兩個結果版本均已成功、尚未採用第二天的中斷流程，不是任意階段重跑工具。`--recheck-snapshots` 可在另行發布／回復後重讀先前 hash，但必須另附真正發布／回復事件，不能僅重讀就宣稱完成來源更新測試。

## 追加：真實價格更新後快照保持不變

父代理在兩筆採用完成後執行真爬蟲。此處另以 V2 `REPEATABLE READ READ ONLY` 交易核對實際資料列，不以工作狀態代替寫入證據：

| 證據 | 實際值（時間為 UTC） |
| --- | --- |
| 兩筆採用 confirmed | 11/11 08:31:24.033766、13:56:21.689552 |
| 舊報價 | snapshot **7**；uniprosperity；07:47:23.919930；product `2203101800101`；99 元 |
| 全聯本輪 | run `v2-after-adoption-garlic-20261111`；snapshot **9**；14:06:36.163500；`unusable_package`，包裝數量／單位無法解析，沒有虛構價格 |
| 萬家福 fallback | run 加後綴 `:uniprosperity`；snapshot **10**；14:06:37.100776；`success`，實際新增報價 |
| 新報價身份 | FOOD000039；release `recipe-1103-v3-dd4f75c13772`；商品 `2203101800101`「大蒜(蒜苗)180g」；180 g／99 元；來源為萬家福查詢 URL |

斷言已通過：新成功報價 ID 不同於舊報價，且其 queried_at 晚於兩筆採用 confirmed。這次新觀測的商品、金額仍相同，並非已測價格數值改變。

隨後實際執行 `t16_acceptance.py --recheck-snapshots .../t16-acceptance-resumed.json --output .../t16-after-price-refresh.json`，兩筆完整快照 SHA256 均與更新前一致，歷史內報價仍是 snapshot 7：

- 11/11：`d8d121cfcc603145b73d37eb04a6990f2e57aaa641940de8f186954fd61e03e8`。
- 11/12：`7a787b584d1163dc9ea0964336feb6c5b9a54e71ed098708622dffb672257bac`。
- 唯讀 SQL 證據：`.test-runtime/1111_update_V2/t16-price-refresh-readonly.json`；HTTP 重讀：`.test-runtime/1111_update_V2/t16-after-price-refresh.json`。

因此已驗「真實新報價寫入後，既有採用快照不被最新觀測覆寫」。沒有發布或回復食譜、營養、來源政策或管理價格政策；這些不能由本結果推定通過。

## 仍需驗證

- 本輪沒有發布／回復任何來源政策或管理價格政策；此類變更後舊快照不變未驗。新價格觀測寫入的較窄範圍已通過上述追加檢查。
- 未測前日不同餐別兩筆不同 plan 同菜去重、同日其他餐別、生成後新增前日採用造成衝突、併發交易與故障回滾。
- T15 缺價接受例外／重試、已知超預算、必要營養缺失及知識故障重試未由這兩筆真實結果涵蓋；不能宣稱 T15 全部通過。
- Admin 同帳號不能自行審核／發布自己的提案仍是既有保護；本輪只讀事件，沒有以角色放寬自審限制。一般管理發布／回復另待獨立審核者驗收。
- 此輪沒有再次做第二天 Sim 結帳；第一天已有獨立 Sim 證據。使用者仍須自行操作主站、Sim、Admin 與畫面歷史確認。
