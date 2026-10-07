# T04 食譜營養估算模組

## 這個模組解決什麼問題？

使用者輸入食材名稱與重量後，系統會從公開營養資料辨識食材，計算以下六項營養：

- 熱量
- 碳水化合物
- 蛋白質
- 脂肪
- 鈉
- 膳食纖維

也可以把多個食材加總成一道食譜，並保留資料來源與缺值狀態。

## 對組員的重點規則

1. 使用者只輸入「紅蘿蔔」時，系統使用 TFDA 的胡蘿蔔平均值；若輸入冷凍或特定品種，則優先使用明確的品名資料。
2. `success` 代表食材是否成功辨識與計算；`nutrition_complete` 代表六項營養是否全部有資料，兩者不同。
3. TFDA 沒有提供的營養值保留為 `null`，不能自行當成 0。
4. 熟食營養與 Cooking Yield 必須是相同食材型態；沒有完全相符資料時，改用 TFDA 生食估算並標示原因。
5. 若食譜有食材完全查不到，`total_nutrition` 不會顯示部分結果冒充完整總營養；部分加總只放在 `partial_nutrition_totals`。

## 執行測試

在 `nutrition_model` 資料夾執行：

```sh
python -m unittest test_t04_completion -v
```

也可以執行整組展示腳本：

```sh
python test_nutrition.py
python test_nutrition_flow.py
python test_recipe_nutrition.py
python test_usda_cooked_nutrition.py
python test_cooking_yield.py
python test_cooked_nutrition.py
```

## 驗收案例

輸入「雞胸肉 150g＋紅蘿蔔 80g」時：

- 兩個食材都應成功辨識
- 紅蘿蔔應對到 TFDA `E02001` 胡蘿蔔平均值
- 總熱量應為 `206.7 kcal`
- 雞胸肉膳食纖維因 TFDA 缺值維持 `null`
- 若沒有相符 Cooking Yield，結果應標示為生食估算

## 資料範圍

此分支提交整理後的 JSON 與程式，不提交約 106MB 的原始下載資料、虛擬環境或暫存檔。原始資料含公開資料下載檔，若要重新匯入，請由團隊另行保存並依匯入腳本處理。
