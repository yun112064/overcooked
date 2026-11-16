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

# 名稱包含另一個食物、但是不同的食物（不同部位或加工品，營養不同）：問「地瓜」時不能把「地瓜葉」寫進回答。
# 2026-11-10 使用者回報：「地瓜、地瓜葉是不一樣的」。只列確定不同的組合，不確定的不列。
DISTINCT_FOODS: dict[str, tuple[str, ...]] = {
    "地瓜": ("地瓜葉", "地瓜粉"),
    "番薯": ("番薯葉", "番薯粉"),
    "南瓜": ("南瓜子", "南瓜籽"),
    "玉米": ("玉米筍", "玉米鬚", "玉米粉", "玉米澱粉"),
    "黃豆": ("黃豆芽",),
    "綠豆": ("綠豆芽",),
    "西瓜": ("西瓜子", "西瓜籽"),
    "葡萄": ("葡萄籽",),
    "芹菜": ("芹菜葉",),
}


def distinct_foods(question: str) -> dict[str, tuple[str, ...]]:
    """問題問到的食物 → 不能混進回答的相近食物（問題本身也提到的不算，例如問「地瓜和地瓜葉哪個好」）。"""
    out = {}
    for base, others in DISTINCT_FOODS.items():
        rest = question
        for other in others:
            rest = rest.replace(other, "")
        excluded = tuple(o for o in others if o not in question)
        if base in rest and excluded:
            out[base] = excluded
    return out


def title_only_about_other_food(title: str, distinct: dict[str, tuple[str, ...]]) -> bool:
    """文章標題談的是相近的另一種食物（〈地瓜葉6大功效〉）：問地瓜時不給模型看，避免拿地瓜葉的資料說地瓜。
    標題同時談兩者（〈地瓜、地瓜葉好處〉）的保留，再由逐句過濾處理。"""
    for base, others in distinct.items():
        hit = [o for o in others if o in title]
        if not hit:
            continue
        rest = title
        for other in sorted(hit, key=len, reverse=True):
            rest = rest.replace(other, "")
        if base not in rest:
            return True
    return False


def about_other_food(sentence: str, distinct: dict[str, tuple[str, ...]]) -> bool:
    """句子提到問題沒問的相近食物（問地瓜卻寫到地瓜葉）。連「地瓜和地瓜葉都…」也刪，避免把兩者混在一起。"""
    return any(other in sentence for others in distinct.values() for other in others)


class FoodNames:
    """文章標題在講別的食物時不給模型看（問地瓜葉卻拿到〈吃茼蒿5大營養好處〉〈超級食物地瓜好處多〉）。

    2026-11-15 使用者同意：DISTINCT_FOODS 只擋「問地瓜時混進地瓜葉」，反方向與完全不同的食物（茼蒿）另以這條處理。
    - 食物名稱：專案既有的 TFDA 食材標準名與別名、食材 ID 主檔（同 segment.FOOD_FILES）；同一列的名稱互為同物異名
      （地瓜＝甘藷＝番薯，地瓜葉＝甘藷葉）。營養素詞（胡蘿蔔素、β胡蘿蔔素…）先遮掉，不當成食物。
    - 問題點名的食物：名稱長的優先、不重疊（問「地瓜葉」就是地瓜葉，不會再當成地瓜）。
    - 標題＋小標提到點名食物（或其同物異名）本身 → 保留；以它開頭、問題沒提到的更長名稱（地瓜葉之於地瓜）不算提到。
    - 標題＋小標沒提到點名食物、但提到其他食物 → 不給模型；標題沒有任何食物的一般文章照常保留。
    """
    MASK = "\0"

    def __init__(self, groups, nutrients=()):
        self.synonyms: dict[str, set[str]] = {}
        for group in groups:
            names = {n for n in map(_food_name, group) if n}
            for n in names:
                self.synonyms.setdefault(n, set()).update(names)
        self.names = sorted(self.synonyms, key=lambda n: (-len(n), n))
        self.nutrients = sorted({n for n in nutrients if n and n not in self.synonyms}, key=len, reverse=True)

    @classmethod
    def load(cls) -> "FoodNames":
        from assistant_rag.config import PROJECT_ROOT
        from assistant_rag.segment import FOOD_FILES, NUTRITION_TERMS
        groups = []
        for rel in FOOD_FILES:
            for row in json.loads((PROJECT_ROOT / rel).read_text(encoding="utf-8")):
                groups.append([row.get("standard_name"), row.get("canonical_name"), *(row.get("aliases") or [])])
        return cls(groups, NUTRITION_TERMS)

    def _mask(self, text: str) -> str:
        for term in self.nutrients:
            text = text.replace(term, self.MASK)
        return text

    def asked(self, text: str) -> list[str]:
        """文字點名的食物（長的優先、位置不重疊）。"""
        text = self._mask(text)
        taken = [False] * len(text)
        found = []
        for name in self.names:
            start, hit = text.find(name), False
            while start != -1:
                span = range(start, start + len(name))
                if not any(taken[i] for i in span):
                    for i in span:
                        taken[i] = True
                    hit = True
                start = text.find(name, start + 1)
            if hit:
                found.append(name)
        return found

    def mentions(self, food: str, text: str, question: str) -> bool:
        """text 提到 food 或其同物異名本身（「地瓜葉」不算提到「地瓜」）。"""
        text = self._mask(text)
        for name in sorted(self.synonyms.get(food, {food}), key=len, reverse=True):
            plain = text
            for longer in self.names:  # 長的在前
                if longer != name and longer.startswith(name) and longer not in question:
                    plain = plain.replace(longer, self.MASK)
            if name in plain:
                return True
        return False

    def title_about_other_food(self, topic: str, foods: list[str], question: str) -> bool:
        if any(self.mentions(food, topic, question) for food in foods):
            return False
        return bool(self.asked(topic))


def _food_name(name) -> str | None:
    import re
    if not isinstance(name, str):
        return None
    name = re.sub(r"[(（].*?[)）]", "", name).strip()
    return name if re.fullmatch(r"[一-鿿]{2,8}", name) else None


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
