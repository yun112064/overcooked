# T03 MVP 開發交接

日期：2026-10-05。現行規則版本：`hpa-dri8-mvp-0.2.0`。計算程式已交付；來源驗收待完成，G03 未通過。

## 已確認與未確認

- 已確認：MVP 全部採成人標準，兒少公式及條件延後；國健署衛公式－1及官方活動係數；所有年齡和目標的蛋白質統一1.1 g/kg/day；增肌熱量+5%、減脂−500 kcal，低於1200回報不適用。不新增BMI、訓練或高齡門檻。
- 尚待查證：官方 PDF 表格視覺核對、來源檔快照，以及正式 G03 適用性結論。產品使用成人式到所有年齡的 MVP 指示與原文件成人 19 歲起適用範圍分別保存。

## 輸入契約

`energy_baseline(member)`、`daily_target(member)` 與 `meal_target(member, meal)` 接受下列成員mapping；不含n，未知欄位會拒絕：

| 欄位 | 型別／要求 |
|---|---|
| member_id | 非空字串；不要使用姓名代替 ID |
| age_years | 非負整數足歲；不接受 bool、小數或字串年齡 |
| equation_sex | M 或 F，為來源公式分支；不從姓名推測 |
| weight_kg | 正有限 Decimal、十進位字串或整數；Python float 不接受 |
| activity_category | sedentary、low_active、active、highly_active |
| calculation_date | YYYY-MM-DD；是足歲資料的計算日期，隨輸入保存 |
| goal | general、maintain、muscle_gain、fat_loss |

JSON CLI 使用 `parse_float=Decimal`，所以 JSON 十進位數字可接受；HTTP 轉接應同樣用 Decimal，避免先轉 Python float 再傳入。

`validate_portion(n)` 只接受 0.7、0.85、1、1.15、1.3。這是**單人成員**的驗證，不可用來驗證總 N；例如兩個 1.3n 合計 2.6 合法。每日能量函式不接受 n，避免重複縮放。

## 計算與可追溯性

```text
RMR_per_kg = 28.2437 + 2.4275×S − 0.02277×A − 0.1234×W
REE = RMR_per_kg×W
E0 = REE×PAL
PAL = 低1.3／稍低1.5／適度1.7／高1.9
```

Decimal 計算使用局部 context，至少 50 位有效數字，並隨輸入有效位數擴大；中間運算若需不精確取整便拒絕，不能默默取整。沒有自行設定醫學上的最大年齡或體重；非正有限數值、推算結果非正數及無法精確計算的輸入明確拒絕。官方體位支援範圍尚未驗證完整，不能稱只要通過這些形式檢查就是醫學上適用。

輸出包含規則版次、規則 JSON SHA-256、來源版次與印刷頁碼、原公式適用範圍、產品 MVP 範圍、PAL 及輸入快照。**規則檔雜湊不是官方來源 PDF 雜湊**。本版本依原始規則檔位元組固定雜湊，即使只改 JSON 排版也必須正式發行新版本或恢復原檔；新版本須有程式註冊與測試，不能用 `latest` 偷換歷史規則。

`energy_baseline`只提供E0，仍標記 `daily_target_available=false`。`daily_target`提供未乘n的 `daily_reference`，並標記 `daily_target_available=true`：

| 目標 | 每日熱量D_E | 每日蛋白質D_P |
|---|---|---|
| general／maintain | E0 | W×1.1 |
| muscle_gain | E0×1.05 | W×1.1 |
| fat_loss | E0−500，低於1200拒絕 | W×1.1 |

`meal_target`另提供 `meal_reference = daily_reference×餐別比例`，仍不乘n。所有輸出保留 `approved_for_adoption=false`，因G03來源驗收尚未完成；不得混淆「可以算出目標」與「餐食可正式採用」。

### 用餐與便當合計

`aggregate_meal_targets(participations)`接受非空list，每項必須含 `participation_id`（唯一）、`kind`（dining或bento）、`member`（上表mapping）、`meal`（breakfast/lunch/dinner）、`n`（五段之一）。同一成員晚餐加午餐便當用兩個不同ID；不能以member_id去重。全部項目用同一計算日期，同一成員的身體與目標快照須一致。

```text
B_E(j) = D_E(j)×r(j)；B_P(j) = D_P(j)×r(j)
N = Σn(j)
E_target = ΣB_E(j)×n(j)；P_target = ΣB_P(j)×n(j)
E_reference = E_target/N；P_reference = P_target/N
```

輸出 `target`及 `total_n` 為精確Decimal字串；`reference_exact`為約分後分子／分母字串，精確保存循環小數。`reference_display`是50位有效數字的顯示估值，**不可拿它回乘作驗證**。T12可用Fraction比較基準配方，或直接檢查 `0.9×E_target ≤ 基準配方熱量×N ≤ 1.1×E_target`；蛋白質同理。先通過基準驗證、再縮放配方的順序不變。

每項也保存每日基準、餐別基準、n及縮放後目標，便於還原。T03不生成食譜，也不代替T12計算實際食譜營養。

## 錯誤轉接

`TargetInputError.code` 沿用 Spec 的 `INVALID_INPUT`；`as_dict()` 包含可讀訊息及 `details.reason/field`。下列 reason 是本模組細分理由，並非擴增 Spec 的頂層錯誤碼：

- 缺輸入／格式：`REQUIRED_TARGET_INPUT_MISSING`、`INVALID_NUMBER`、`INVALID_AGE`、`INVALID_WEIGHT`、`INVALID_DATE`、`INVALID_ACTIVITY`、`INVALID_GOAL`、`UNSUPPORTED_EQUATION_SEX`、`UNKNOWN_FIELD`、`INVALID_MEMBER_ID`、`INVALID_MEMBER`。
- 規則：`UNKNOWN_RULE_VERSION`、`RULE_CONFIG_VERSION_CONFLICT`、`GOAL_RULE_PENDING_CONFIRMATION`、`PROTEIN_RULE_PENDING_CONFIRMATION`。
- 無法計算：`UNSUPPORTED_TARGET_SCOPE`、`UNSUPPORTED_NUMERIC_PRECISION`、`INVALID_PORTION`、`DAILY_ENERGY_BELOW_MINIMUM`、`INVALID_MEAL`。
- 合計輸入：`INVALID_PARTICIPATIONS`、`INVALID_PARTICIPATION`、`DUPLICATE_PARTICIPATION`、`INVALID_PARTICIPATION_KIND`、`MEMBER_SNAPSHOT_CONFLICT`、`CALCULATION_DATE_CONFLICT`。

API 層再決定 HTTP 狀態及家庭權限；不得直接對任意成員 ID 提供無權限的公開服務。不要在捕捉錯誤時記錄完整 input snapshot 到 Log。

## 人工結果與驗證

| 合成案例 | E0 kcal/day |
|---|---:|
| M／30／70 kg／低 | 1942.8591 |
| 同成員／稍低 | 2241.7605 |
| 同成員／適度 | 2540.6619 |
| 同成員／高 | 2839.5633 |
| F／30／55 kg／稍低 | 1713.822 |
| M／19／65 kg／低 | 1877.384665 |
| F／75／50 kg／低 | 1323.78675 |
| M／30／70.125 kg／低 | 1944.9223096875 |
| M／8／28 kg／低，以MVP成人式試算 | 984.031776 |

最後一列僅驗證使用者指定的產品分支，不是官方兒少營養建議。含小數案例與此例另外以 Fraction 有理數算術核對。

### 每日、餐別及合計人工案例

| 合成案例 | D_E kcal/day | D_P g/day |
|---|---:|---:|
| A：M／30／70kg／低／維持 | 1942.8591 | 77 |
| C：M／30／70kg／適度／增肌 | 2667.694995 | 77 |
| D：M／45／90kg／低／減脂 | 1669.24435 | 99 |
| I：F／75／50kg／低／維持 | 1323.78675 | 55 |
| E：M／8／28kg／低／一般，MVP成人式 | 984.031776 | 30.8 |
| M／30／55kg／低／減脂 | 1158.87865（拒絕） | 不回傳完整目標 |
| M／30／60kg／低／減脂 | 1261.5598 | 66 |

A午餐基準 `582.85773／23.1`，晚餐基準 `777.14364／30.8`；晚餐n1.3為 `1010.286732／40.04`。A晚餐n1.3＋A午餐便當n1.3＋E晚餐n0.7：

```text
N = 3.3
E_target = 1942.8591×0.7×1.3 + 984.031776×0.4×0.7 = 2043.53067828
P_target = 77×0.7×1.3 + 30.8×0.4×0.7 = 78.694
E_reference = 17029422319 / 27500000
P_reference = 3577 / 150
```

其他邊界：0／18／19／69／70／71／75歲不切換蛋白質係數；0.7／0.85／1／1.15／1.3合法、0.9等非法；合計N2.6合法。缺必要欄位、非有限值、布林或無法推算正熱量明確拒絕。兩次相同participation_id拒絕，同member_id的晚餐與便當可同時存在。

23個unittest通過，涵蓋以上案例與CLI十進位JSON解析、歷史版本待確認行為、輸入快照不污染規則來源。人工案例另以Fraction算術核對。CLI四種操作及規範文件連結均已檢查；未執行HTTP／資料庫／前端測試，這些留給相依Ticket接入。

## G03與後續

使用者已定案所有目前MVP計算參數，無待回覆的公式選型問題。程式、規則版本、人工案例與接入文件已交付。尚需完成來源PDF視覺核對與來源檔保存；所有年齡統一成人式仍是產品簡化，不能宣稱官方已驗證該外推。因此G03整體未通過、Ticket尚不標整體驗收完成。

待隊友接入T07／T08／T12；T08、T15不在本次開發範圍。來源驗收完成及MVP後加入兒少／高齡等條件時發新版本，保留0.1.0與0.2.0歷史快照。
