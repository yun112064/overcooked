"""BM25 斷詞（jieba）＋食材／營養詞典。

- 食材詞：專案既有的 TFDA 食材標準名與別名（nutrition_model/data/ingredient_master.json）、
  食材 ID 主檔（ingredient_id_master.json），只讀不改；去掉括號註記，只收 2–8 字的中文詞。
- 營養與飲食詞：下方 NUTRITION_TERMS（jieba 內建詞典以簡體為主，這些繁體詞需補上）。
- 查詢與文件用同一套斷詞；英文、數字轉小寫；去掉標點、停用詞與問句用語（吃什麼、有什麼…）；
  「吃豆腐」「喝咖啡」這類動詞＋食物的詞拆成食物本身；異體字（祕／秘、麽／麼）先統一。
"""
import hashlib
import json
import re
import threading

from assistant_rag.config import PROJECT_ROOT

TOKENIZER_VERSION = "assistant-jieba-v3"
FOOD_FILES = ("nutrition_model/data/ingredient_master.json", "nutrition_model/data/ingredient_id_master.json")
NUTRITION_TERMS = """
蛋白質 膳食纖維 纖維質 碳水化合物 醣類 澱粉 脂肪 飽和脂肪 反式脂肪 不飽和脂肪 熱量 卡路里 維生素 維他命
維生素A 維生素B 維生素B12 維生素C 維生素D 維生素E 維生素K 葉酸 菸鹼酸 礦物質 鈣質 鐵質 鎂 鋅 鉀 鈉 硒 碘 磷
胡蘿蔔素 β胡蘿蔔素 茄紅素 花青素 葉黃素 多酚 兒茶素 類黃酮 抗氧化 益生菌 益生元 寡糖 Omega-3 DHA EPA 普林 尿酸
膽固醇 血糖 血壓 血脂 三酸甘油酯 升糖指數 低GI 高GI 代謝 腸道 腸胃 便秘 腹瀉 消化 飽足感 減重 減肥 瘦身 增肌 肌肉
骨質 骨質疏鬆 免疫力 發炎 貧血 痛風 糖尿病 高血壓 心血管 腎臟病 脂肪肝 孕婦 哺乳 銀髮族 長輩 幼兒 兒童 青少年 更年期
素食 全素 蛋奶素 生酮 低醣 地中海飲食 得舒飲食 間歇性斷食 斷食 原型食物 加工食品 全穀 全穀雜糧 根莖類 豆魚蛋肉類
乳品類 蔬菜類 水果類 油脂與堅果種子類 堅果 營養師 營養素 攝取 好處 功效 副作用 禁忌 地瓜葉 秋葵 奇亞籽
""".split()
STOPWORDS = set("""
的 了 和 與 及 或 是 在 有 也 就 都 而 但 若 如 為 於 對 等 讓 把 被 從 到 以 可 可以 可能 會 要 能 這 那 這些 那些 其 之 很
更 最 多 少 還 又 吃 喝 想 要吃 有什麼 吃什麼 喝什麼 哪些 什麼樣 適合 建議 並 且 因此 所以 因為 如果 例如 像是 其中 一些 一個 一種 什麼 怎麼 如何 嗎 呢 吧 啊 喔 我 你 他 她 我們 你們 大家
""".split())
TOKEN = re.compile(r"[\w一-鿿]+")
# jieba 會把「吃豆腐」「喝咖啡」「多吃蔬菜」黏成一詞；拆掉動詞只留食物
# 異體字統一（只影響 BM25 斷詞，不改原文）
VARIANTS = str.maketrans({"祕": "秘", "麽": "麼", "着": "著"})
EAT_PREFIX = re.compile(r"^(?:多|少|常|要|可以)?(?:吃|喝)(.{2,})$")
_LOCK = threading.Lock()
_TOKENIZER = None


def food_terms(root=PROJECT_ROOT) -> list[str]:
    names: set[str] = set()
    for rel in FOOD_FILES:
        for row in json.loads((root / rel).read_text(encoding="utf-8")):
            for name in [row.get("standard_name"), row.get("canonical_name"), *(row.get("aliases") or [])]:
                if isinstance(name, str):
                    name = re.sub(r"[(（].*?[)）]", "", name).strip()
                    if re.fullmatch(r"[一-鿿]{2,8}", name):
                        names.add(name)
    return sorted(names)


def dictionary(root=PROJECT_ROOT) -> list[str]:
    return sorted(set(food_terms(root)) | set(NUTRITION_TERMS))


def dictionary_hash(words: list[str]) -> str:
    return hashlib.sha256("\n".join(words).encode("utf-8")).hexdigest()[:16]


class Tokenizer:
    def __init__(self, words: list[str] | None = None):
        import jieba  # 延後載入：只用到分段時不需要

        self.words = words if words is not None else dictionary()
        self.version = f"{TOKENIZER_VERSION}+dict-{dictionary_hash(self.words)}"
        self._jieba = jieba.Tokenizer()
        self._jieba.initialize()
        for w in self.words:
            self._jieba.add_word(w, freq=50000)

    def tokens(self, text: str) -> list[str]:
        out = []
        for piece in self._jieba.lcut(text.translate(VARIANTS), HMM=True):
            piece = piece.strip().lower()
            if (m := EAT_PREFIX.match(piece)):
                piece = m.group(1)
            if piece and TOKEN.fullmatch(piece) and piece not in STOPWORDS:
                out.append(piece)
        return out


def default_tokenizer() -> Tokenizer:
    global _TOKENIZER
    with _LOCK:
        if _TOKENIZER is None:
            _TOKENIZER = Tokenizer()
        return _TOKENIZER
