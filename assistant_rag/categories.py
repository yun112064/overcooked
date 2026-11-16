"""類別回答：問題提到的食材查不到相關資料時，改查它所屬類別的一般資料。

對照表 `food_categories.json`：categories（類別代號 → 名稱與檢索句）、foods（食材名稱 → 類別代號）、
match（食材名稱 → 算作「文章有提到」的詞，例如金瓜＝南瓜；未列出時只比對名稱本身）、
aliases（同一種食物的別名，先取食譜試算表的別名、沒有才用人工列的：本名查不到時依序用別名重查，例如金瓜 → 南瓜）、
refuse（不確定能用類別或相近食物代答的食材，例如皮蛋、蛋豆腐：本身查不到就直接說查不到，不用類別資料回答）。
不確定是不是同一種食物的對照一律不列。
分數過門檻但前幾篇文章都沒提到該食材時，也改走類別回答，避免拿相近但不同的食物文章回答（柴魚 → 鯖魚）。
食材名稱取自 V3 食譜在覆蓋檢查中查不到專文的食材；檢索句是實測過能找到該類別文章的自然問句。
回答時必須明說「沒有該食材本身的資料」，只講類別的一般原則，不可把類別資料說成該食材的特性。
"""
import json
from dataclasses import dataclass
from pathlib import Path

PATH = Path(__file__).with_name("food_categories.json")


@dataclass(frozen=True)
class Fallback:
    food: str
    category: str
    label: str
    query: str
    terms: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    @property
    def refuse(self) -> bool:
        """列在 refuse 的食材沒有類別：本身查不到就拒答。"""
        return not self.category

    def alias_question(self, original: str, alias: str) -> str:
        return original.replace(self.food, alias)

    def alias_notice(self, alias: str) -> str:
        return f"「{self.food}」和「{alias}」是同一種食物，以下以「{alias}」的資料回答：\n"

    def mentioned_in(self, hits) -> bool:
        """前幾篇參考資料的標題或內文是否提到這個食材（或對照表列的同物異名）。"""
        terms = self.terms or (self.food,)
        return any(t in (h.title or "") or t in (h.text or "") for h in hits for t in terms)

    def notice(self) -> str:
        return f"目前沒有「{self.food}」本身的資料，以下是「{self.label}」這一類的一般資訊：\n"

    def question(self, original: str) -> str:
        return (f"使用者問的是「{self.food}」，但參考資料沒有{self.food}本身的資料（開頭已經說明過，不要再重複）。"
                f"請只根據參考資料說明「{self.label}」這一類的一般原則，每句的主詞用「這類食物」或參考資料原本談的食物，"
                f"不要用「{self.food}」當主詞，不要說參考資料的內容是{self.food}的特性，也不要推測{self.food}的成分或功效；"
                f"參考資料裡只適用於特定食物或情境的細節不要套用過來。\n原問題：{original}")


class FoodCategories:
    def __init__(self, data: dict):
        self.categories = data["categories"]
        self.foods = {name: key for name, key in data["foods"].items() if key in self.categories}
        self.match = {name: tuple(terms) for name, terms in data.get("match", {}).items()}
        self.aliases = {name: tuple(a for a in alias if a and a != name) for name, alias in data.get("aliases", {}).items()}
        self.refuse = {name: reason for name, reason in data.get("refuse", {}).items() if name not in self.foods}
        self.version = data.get("version")
        # 長的名稱先比對：「辣豆瓣醬」優先於「豆瓣醬」、「蛋豆腐」優先於「豆腐」
        self._names = sorted([*self.foods, *self.refuse], key=len, reverse=True)

    @classmethod
    def load(cls, path: Path = PATH) -> "FoodCategories":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def lookup(self, question: str) -> Fallback | None:
        for name in self._names:
            if name in question:
                if name in self.refuse:
                    return Fallback(name, "", "", "", (name,))
                key = self.foods[name]
                c = self.categories[key]
                terms = self.match.get(name, (name,))
                aliases = self.aliases.get(name, ())
                terms = (*terms, *(a for a in aliases if a not in terms))
                return Fallback(name, key, c["label"], c["query"], terms, aliases)
        return None
