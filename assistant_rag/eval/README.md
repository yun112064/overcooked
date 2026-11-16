# 飲食客服題庫（spec R14 AC33／AC34，步驟 5）

| 檔案 | 用途 | 題數 |
|---|---|---|
| `dev.jsonl` | 開發題庫：比較六種檢索方法、選拒答門檻、調整規則 | 103 題＋3 題追問 |
| `holdout.jsonl` | 獨立驗證集：只在最後報告一次，不拿來調任何參數 | 51 題 |

配比：

| 類別 | 開發 | 驗證 | 預期行為 |
|---|---|---|---|
| 飲食（`diet`） | 73 | 36 | `answer`：依文章回答，`relevant_articles` 任一篇進前 k 名算命中 |
| 追問（`followup`） | 3 | 0 | `answer`；`history`＋`standalone`（補完前文後的問題），不計入檢索指標 |
| 醫療越界（`medical`） | 10 | 5 | `declined_medical`（用藥、診斷、解讀數值）、`emergency`（撥 119）、`no_dose`（可回答或婉拒，但不得出現劑量數字） |
| 非飲食健康（`non_diet_health`） | 10 | 4 | `refuse` |
| 無關／個人／亂打（`irrelevant`） | 7 | 4 | `refuse` |
| 寒暄（`greeting`） | 3 | 2 | `greeting` |

每題欄位：`id`、`category`、`question`、`expected_behavior`、`primary_article`（出題依據的文章，`來源:文章編號`）、`relevant_articles`（`[{article_key, title, url}]`，第一篇即 primary）、`review`（`draft`／`approved`／`changed`／`added`／`removed`），追問另有 `history`、`standalone`。題庫只存題目、文章標題與網址，不含文章原文。

## 怎麼做出來的

1. 依來源與子分類分層抽樣文章（亂數種子 20261109），挑出飲食相關、能回答一般使用者問題的文章，各寫一句使用者會問的話；避免照抄標題用詞。驗證集與開發題庫不共用出題文章，也不重複題目。
2. 每題用四種方法（BM25、bge-m3 向量、embeddinggemma 向量、混合＋重排 bge-m3）各取前 5 篇，合併後逐篇判斷是否也能回答該題，能回答的加入 `relevant_articles`（pooling）。只看過這四種方法的候選，其他方法找到但未被看過的相關文章不會被算對，比較時須注意這個偏差。
3. 2026-11-09 補上使用者範例題型（增加蛋白質、增肌、地瓜好處、素食補蛋白質）：D104–D106、H051。
4. 越界、非飲食、無關與寒暄題由規格（R14）列的情境撰寫。

格式與配比由 `tests/test_assistant_questions.py` 檢查。使用者審核後把 `review` 改成 `approved`／`changed`／`removed`。2026-11-10 使用者通過題庫（開發 111、驗證 51），全部標為 `approved`，修改紀錄留在 `note`。

## 評估（步驟 6）

```powershell
python -m assistant_rag.eval.evaluate retrieval                 # 開發題庫 11 組 Hit@1／Hit@5／MRR@5／延遲
python -m assistant_rag.eval.evaluate threshold                 # 只用開發題庫選拒答門檻
python -m assistant_rag.eval.answers run --split dev --threshold 0.5   # 完整回答流程＋預期行為＋規則檢查
python -m assistant_rag.eval.answers ragas --split dev          # RAGAS 定義的忠實度／回答相關性（本機 LLM 當評審）
python -m assistant_rag.eval.evaluate holdout --threshold 0.5   # 驗證集：題庫與排除名單定案後只跑一次
python -m assistant_rag.eval.categories run                   # 類別回答與別名重查：每類抽 2 種食材＋有別名的食材
```

結果寫在 Data Root 的 `eval/`（含回答原文，不進 Git）。2026-11-09 補進 125 篇後重做 pooling，新增 26 組可接受文章。
