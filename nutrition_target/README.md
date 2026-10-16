# 今天吃啥：T03 營養目標模組

本資料夾集中 T03 的 Python 套件、版本化規則、合成測試資料、測試與交接文件，與 T04 的 `nutrition_model/` 並列。T03 已決議以 Spec 為準；G03 仍未通過。跨模組狀態見 [決議與整合待辦](../docs/T04-T06-merge-pending.md)。

現行規則：`hpa-dri8-mvp-0.3.0`，沿用 T03 分支已實作的目標限制：0–18 歲僅 `general`，19 歲起開放維持／增肌／減脂。19 歲是既有 T03 的成人目標支援門檻，並非 Spec 對法律成年年齡的定義。一般餐食／維持不調整熱量、增肌+5%、減脂−500 kcal；蛋白質係數維持 1.1 g/kg/day。既有成人式的一般餐食試算仍待 G03 適用性驗證，不能宣稱已確認適用兒少。減脂結果低於1200時明確拒絕，不自動夾值。

已實作每日基準、餐別基準、用餐／便當及五段n合計、規則與輸入快照、精確合成基準及CLI。T08、T15仍等待隊友功能。本次不建立公開API、登入、資料庫或前端。

程式可供隊友接入測試；來源PDF視覺核對與來源檔保存仍待完成，**G03尚未通過，不能將計算結果直接視為正式採用許可**。產品簡化與官方成人公式原適用範圍分開記錄。

## 執行

使用 Python 3.11 以上，不需安裝額外套件。下列指令均從專案根目錄 `overcooked/` 執行，不需設定 `PYTHONPATH`。

完整每日熱量與蛋白質：

```sh
python -m nutrition_target \
  --rules nutrition_target/rules/hpa-dri8-mvp-0.3.0.json \
  --input nutrition_target/tests/fixtures/member-A.json \
  --operation daily
```

合成成員A為30歲男性公式分支、70kg、低活動、維持。結果為每日 `1942.8591 kcal／77 g`。

單人晚餐基準：

```sh
python -m nutrition_target \
  --rules nutrition_target/rules/hpa-dri8-mvp-0.3.0.json \
  --input nutrition_target/tests/fixtures/member-A.json \
  --operation meal --meal dinner
```

結果為 `777.14364 kcal／30.8 g`，此階段尚未乘n。

用餐、便當及其他成員合計：

```sh
python -m nutrition_target \
  --rules nutrition_target/rules/hpa-dri8-mvp-0.3.0.json \
  --input nutrition_target/tests/fixtures/participations.json \
  --operation aggregate
```

範例合計 `N=3.3`，當餐目標 `2043.53067828 kcal／78.694 g`。合成基準以 `reference_exact` 的分子／分母保存；`reference_display`只是50位有效數字的顯示估值，不能回乘作驗證。

`--operation energy`只輸出未調整能量E0，不是增肌或減脂最終目標。`--input -`可從stdin讀JSON；所有Decimal輸出為字串。錯誤退出碼2、成功退出碼0。不要提交真實健康資料或把輸入快照寫入Log。

## 測試

```sh
python -m unittest discover -s nutrition_target/tests -t . -v
```

測試涵蓋人工算例、三種成人目標及一般餐食、統一蛋白質、官方活動係數、減脂下限、五段n、便當合計、精確基準、輸入／版本邊界及JSON CLI。

## 隊友接入

```python
from nutrition_target import NutritionTargetService, TargetInputError

service = NutritionTargetService("nutrition_target/rules/hpa-dri8-mvp-0.3.0.json")
daily = service.daily_target(member_input)
meal = service.meal_target(member_input, "dinner")
combined = service.aggregate_meal_targets(participations)
```

`available_goals(age_years)` 提供該規則版本的可選目標；不適用的目標在每日、餐別、合計與 CLI 入口均拒絕，回傳 `INVALID_INPUT`／`GOAL_NOT_ALLOWED_FOR_AGE`，不自動改成一般餐食或維持。

- T07保存成員ID、足歲、公式性別M/F、體重kg、四級活動、目標及計算日期。衛公式－1不需要身高；n另外保存。
- T08形成用餐與便當參與項目，每個項目指定餐別與n。
- T12消費未縮放每日基準或合計目標，依Spec先對齊基準配方±10%，再乘總N；精確分數避免循環小數取整造成邊界漂移。
- FastAPI/Pydantic轉接、家庭權限及正式Log沿用T06骨架責任。本模組不擴張其範圍。

新流程明確使用 0.3.0；0.1.0、0.2.0 保留原始位元組與歷史計算行為，僅供重現，不供新流程選用。詳見 [交接契約與人工案例](docs/T03-handoff.md)、[規則來源與G03](docs/T03-nutrition-rule-proposal.md)。

原 `app.nutrition` 匯入與 `python -m app.nutrition` 入口已移至 `nutrition_target`；相依功能接入時使用上述新路徑。未來 Docker 接入須同時納入套件與規則，單純搬移資料夾不代表已完成 API／容器整合。
