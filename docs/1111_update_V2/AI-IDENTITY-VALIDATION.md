# V2 AI 食材身分修正與驗證

日期：2026-11-11。分支：1111_update_V2。僅限隔離業務庫 meal_planner_1102_v3_1111_update_v2。

## 問題與證據

- 舊驗收案例指定蒜香清蒸雞胸肉，但現有食材使用 FOOD001578（雞腿肉）；雞胸肉實際為 FOOD001564。這是可合法存在的問卷情境，但不適合作為基本成功案例。
- 原外部 schema 要模型提供名稱及可空的 FOOD ID；合法食材清單只有名稱。提示詞同時要求部分食材填 ID、禁止輸出資料庫編號。
- 原工作 f23a2f8e-9d39-4bb2-807d-00b594c0cb33 回報 INGREDIENT_ID_MISMATCH。原始輸出未保存，所以不能斷言當次是哪一列錯配。使用真字典可重現雞胸肉＋雞腿 ID 被拒絕。

## 修正

- 外部模型只輸出本輪合法標準名稱及用量，後端解析 FOOD ID；不覆寫真實來源資料。
- 名稱多義或明標歧義時阻擋，不再以使用頻率、ID 排序或模型提供的 ID 猜測。
- 舊內部格式仍可讀；名稱／ID 矛盾仍拒絕。
- 每次失敗的有限身分診斷保存在 Data Root 的 generation-diagnostics/{job_id}/{attempt}.json。包含錯誤、候選 ID、已知標準名稱及字典／食譜／營養／政策版本；不保存任意模型文字、完整 prompt、密碼或金鑰。
- 重試只接續同工作上一嘗試、且版本綁定相符的診斷；提示資料不能放寬後端限制。原 T10 預留、結算及兩次上限維持。
- 驗收腳本分 matching 與 different-stock；結果必須含雞胸 ID，不能用雞腿替代。

## 本輪實測及限制

- v7 prompt / v3 schema 首次部署：matching 工作 66694de4-aa54-4f22-8862-de5a45e5243d 第二次成功，可採用；第一回「鹽」歧義診斷正確保存，第二次已修正。費用 0.0998＋0.0981 TWD。
- 同版 different-stock 工作 82f776dd-aa1c-4753-bb4f-2dfef8991123 兩回皆因「鹽」歧義被拒絕；費用 0.0971＋0.1021 TWD。未假報通過。
- 因此追加本輪合法名稱 schema enum，並將重試建議限制為本輪合法名稱。後續部署實測另記。
- 單元測試使用合成字典；HTTP 驗收使用真實食材、營養來源及真實 OpenAI，測試帳號／家庭是驗收資料。診斷與報告留在私有 Data Root／Git-ignored .test-runtime；沒有推送或改正式庫。
- 知識解說在首筆成功案例仍 unavailable；不代表 T11 或 T01–T16 全部驗收完成。

## 最終部署：prompt v9 / schema v5

- v8/v4 的 different-stock 工作 782db66a-d6a9-424b-940f-982ed65969ae 不再有身分錯誤，但被 INVALID_QUANTITY 擋下；兩回費用 0.1669＋0.1683 TWD。另補外部數值限制：有限數、0 < grams_per_n ≤ 2000，不用的食材省略。內部舊格式仍交由既有驗證拒絕錯誤份量。
- 最終 deterministic identity／diagnostics／nutrition-source tests：**41 passed**。先前涵蓋管理與知識相容性的回歸 **49 passed、11 skipped**（需專用 PostgreSQL），不把略過列為通過。
- different-stock：工作 ae7015e8-266e-4a36-8d0f-9b9b623d7906，**一次成功**；輸出雞胸 FOOD001564，沒有雞腿 FOOD001578，adoptable=true。費用 0.1653 TWD；報告 ai-flow-20261111-214652.json。
- matching：工作 ac1202f3-358b-487a-8ce4-8cfafccfcab9，**一次成功**；雞胸身分驗證通過，adoptable=true。費用 0.1608 TWD；報告 ai-flow-20261111-214757.json。
- 最終兩筆 T10 合計 0.3261 TWD；本次修正階段含前述失敗／中間版本測試共 1.0584 TWD，全部已結算。
- 兩筆僅驗證到生成結果及採用資格，沒有自動採用；原始流程的生成→採用→Sim→Admin HTTP 驗收另有紀錄。知識解說仍 unavailable，沒有把它列為本次完成。
