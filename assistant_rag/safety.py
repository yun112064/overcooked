"""回答規則（spec R14）：寒暄、急症、醫療越界的事前判斷，以及回答句子的事後檢查。

這些規則固定在程式裡，使用者輸入或參考文章內容都不能改變。
- 寒暄：整句只有招呼／道謝／問身分時直接回覆，不檢索。
- 急症：胸痛、中風徵兆、呼吸困難、自殺念頭等 → 提醒撥 119（自殺另附 1925 安心專線），不檢索。
- 用藥、診斷 → 婉拒並建議就醫；疾病相關的飲食問題（患者怎麼吃、控制或預防特定疾病）→ 婉拒（2026-11-09 使用者決定先不處理）；
  食物安全（會不會致癌、食物過敏、食物中毒）不在此列；份量／劑量問題 → 仍檢索，但回答只講一般原則（數字句由事後檢查移除）。
- 事後檢查：移除含劑量／攝取數量、指示用藥、替使用者下診斷或寫給疾病族群（患者、某病的人）的句子，以及沒有引用編號的句子；引用編號只保留本次參考資料範圍內的。
"""
import re

GREETING_REPLY = {
    "hello": "你好！我是飲食小幫手，可以回答飲食與營養相關的問題，例如「吃地瓜有什麼好處」。回答內容整理自健康醫療網與 Hello醫師的文章。",
    "thanks": "不客氣！還有其他飲食問題都可以再問我。",
    "who": "我是飲食小幫手，會根據健康醫療網與 Hello醫師的飲食文章回答問題並附上出處。我不能診斷、開藥或提供劑量建議，身體不適請就醫。",
    "bye": "再見！祝你吃得健康。",
}
GREETINGS = {
    "hello": r"(你好|您好|妳好|嗨|哈囉|哈啰|hi|hello|hey|早安|午安|晚安|早|安安)",
    "thanks": r"(謝謝|謝啦|感謝|感恩|多謝|thanks|thank you|thx|3q)(你|您|妳)?",
    "who": r"(你|妳|您)(是誰|是什麼|叫什麼|會做什麼|能做什麼|可以做什麼)",
    "bye": r"(再見|掰掰|拜拜|bye|886)",
}
FILLER = r"[\s,.!?~，。！？～、…:：;；)(（）\"'「」『』喔哦啊呀耶唷嗎呢啦囉吧欸ㄟ]*"

EMERGENCY = re.compile(
    r"胸(口|部)?.{0,3}(痛|悶)|心(臟)?絞痛|呼吸困難|喘不過氣|無法呼吸|中風|嘴歪|半邊(身體)?(無力|麻)|口齒不清|"
    r"昏倒|昏迷|意識不清|抽搐|吐血|大量出血|血便不止|過敏性休克|喉嚨(腫|緊)|嘴唇腫|全身起疹.*喘|"
    r"中毒|誤食|喝了(漂白水|農藥|清潔劑)|劇烈(腹痛|頭痛)")
SELF_HARM = re.compile(r"自殺|不想活|想死|輕生|結束(自己的)?生命|傷害自己")
DRUG = re.compile(
    r"(吃|服用|用|開|推薦|建議|換|停|加)[^，。？?！!]{0,6}(什麼|哪種|哪個|哪些)?[^，。？?！!]{0,4}(藥(?!膳|材|燉|草|酒)|藥物|處方|成藥|抗生素|胰島素|類固醇|止痛藥|安眠藥)|"
    r"(藥|藥物)[^，。？?！!]{0,6}(吃多少|怎麼吃|要吃|可以停|副作用|一起吃|交互作用)|"
    r"(降血壓藥|降血糖藥|降血脂藥|降尿酸藥|止痛藥|抗生素|胰島素|安眠藥|類固醇|普拿疼|阿斯匹靈)")
DISEASE = r"(病|症|癌|炎|潰瘍|貧血|結石|痛風|三高|高血壓|高血脂|糖尿病|脂肪肝|腫瘤|感染|過敏)"
DIAGNOSIS = re.compile(
    r"(我|我媽|我爸|家人|小孩|他|她)[^，。？?！!]{0,12}(是不是|是否|會不會)[^，。？?！!]{0,8}(得了|罹患|有)[^，。？?！!]{0,10}" + DISEASE + "|"
    # 「我小孩一直拉肚子是腸胃炎嗎」：描述身邊的人的狀況並問是不是某種病（2026-11-10 驗證集 H039 後補）
    r"(我|我媽|我爸|家人|小孩|孩子|寶寶|他|她)[^，。？?！!]{0,12}(是|算)[^，。？?！!吃喝會容易]{0,4}" + DISEASE + r"[^，。？?！!]{0,2}(嗎|吗|[?？])|"
    # 「是不是貧血」「是否得了胃潰瘍」：問自己現在的狀況；「會不會得…」是風險問題，不在此列
    r"(是不是|是否|算不算|算是)(得了|罹患|患了|有)?(點|一點)?[^，。？?！!會吃喝要容易太]{0,3}" + DISEASE + "|"
    # 「空腹 130 算糖尿病嗎」「這樣算高血壓嗎」：解讀數值
    r"\d+(\.\d+)?[^？?！!]{0,12}(算|是)(不是)?[^，。？?！!]{0,6}(" + DISEASE[1:-1] + r"|正常|偏高|太高|過高|偏低)|"
    r"(這樣|這)(算|是)(不是)?[^，。？?！!]{0,4}" + DISEASE + "|"
    r"(幫我|幫忙)(診斷|看看是什麼病|判斷是什麼病)|(這是|是)什麼病|(得了|罹患)什麼(病|症)|"
    r"(檢查|報告|數值|指數)[^，。？?！!]{0,10}(正常嗎|代表什麼|是不是有病|嚴重嗎)")
# 疾病相關飲食問題（2026-11-09 使用者決定先不回答）。「致癌」「過敏」「食物中毒」屬食物安全，不列入。
DISEASE_TOPIC = re.compile(
    r"糖尿病|糖友|妊娠糖|降血糖|控(制)?血糖|控糖|高血糖|低血糖|血糖(值)?(偏|過|太)高|血糖高的人|"
    r"高血壓|低血壓|降血壓|血壓(偏|過|太)高|血壓高的人|高血脂|降血脂|血脂(偏|過|太)高|血脂高的人|降膽固醇|高膽固醇|(血中)?膽固醇(偏|過|太)高|膽固醇高的人|膽固醇超標|三高|"
    r"痛風|尿酸|脂肪肝|肝病|肝炎|肝硬化|腎臟病|腎病|腎友|洗腎|透析|心臟病|心血管疾病|中風|"
    r"(?<!致)癌|腫瘤|化療|貧血|骨質疏鬆|骨鬆|甲狀腺|甲亢|潰瘍|胃食道逆流|胃酸逆流|腸躁|巴金森|帕金森|失智|阿茲海默|"
    r"慢性病|患者|病人|病友|病患|結石|關節炎|代謝症候群|憂鬱症|氣喘|肌少症|吞嚥困難|術後|手術後|開刀後|開完刀|火燒心|"
    r"新冠|確診|流感|肺炎|乾癬|異位性皮膚炎|ADHD|過動症", re.I)
QUANTITY_QUESTION = re.compile(r"(吃|喝|攝取|補充)?(多少|幾(顆|杯|碗|份|片|根|克|公克|毫克|匙|次))|劑量|上限|建議量|攝取量")

EMERGENCY_REPLY = ("你描述的情況可能需要緊急處理，請立即撥打 119 或儘快到急診就醫。"
                   "我只能提供一般飲食資訊，無法判斷身體狀況。")
SELF_HARM_REPLY = ("聽起來你現在很不好受。如果有立即危險，請撥打 119；"
                   "也可以撥打衛福部安心專線 1925（24 小時、免付費）跟專人聊聊。")
DRUG_REPLY = ("用藥（藥名、怎麼吃、要不要停藥、劑量）需要醫師或藥師依你的狀況判斷，我無法提供建議，請諮詢醫師或藥師。"
              "如果想了解一般食物的營養，可以換個方式問我，例如「吃地瓜有什麼好處」。")
DIAGNOSIS_REPLY = ("我無法判斷身體狀況或疾病，若有不適或檢查數值異常，請諮詢醫師。"
                   "如果想了解一般食物的營養，可以直接問我，例如「吃地瓜有什麼好處」。")
DISEASE_REPLY = ("目前小幫手先不回答和疾病有關的飲食問題（例如糖尿病、痛風、癌症患者怎麼吃，或怎麼預防特定疾病），"
                 "這類情況需要依個人病情調整，請諮詢醫師或營養師。一般食物的營養與好處，可以直接問我。")
OUT_OF_SCOPE_REPLY = ("目前的參考資料中找不到能回答這個問題的內容。我只能回答飲食與營養相關的問題，"
                      "例如食物的營養與好處、特定族群的飲食原則；也看不到個人病歷或即時資訊。")


def no_food_reply(food: str) -> str:
    """問題提到的食材查不到本身的資料，也不能用類別或相近食物代答時的回覆（不改說成「只能回答飲食問題」）。"""
    return f"目前的參考資料中沒有「{food}」本身的資料，我無法回答，以免拿其他食物的資料張冠李戴。可以問其他食材，或換個方式問。"


DISCLAIMER = "以上內容整理自參考文章，僅供一般飲食參考，不能取代醫師或營養師的個別建議。"
QUANTITY_NOTE = "實際適合的份量會因年齡、身體狀況與用藥而不同，建議諮詢營養師或醫師。"
DISEASE_NOTE = "參考資料中針對特定疾病患者的內容已略去；若有慢性病或正在治療，請先諮詢醫師或營養師。"

# ---------- 事後檢查 ----------
NUM = r"(\d+(\.\d+)?|[一二兩三四五六七八九十百千半幾數]+)"
UNIT = (r"(公克|克|毫克|微克|mg|μg|mcg|g|IU|國際單位|大卡|千卡|kcal|卡路里|毫升|ml|mL|cc|c\.c\.|公升|"
        r"顆|粒|錠|膠囊|杯|碗|份|匙|湯匙|茶匙|小匙|大匙|片|根|條|串|瓣|個|拳頭|掌心|盎司)")
DOSE = re.compile(NUM + r"\s*(至|到|~|～|-|－)?\s*(" + NUM + r")?\s*" + UNIT + r"(?![a-zA-Z])", re.I)
# 「每天進食次數不超過 5 次」：中間可夾較長的說明（2026-11-10 驗證集 H043 後由 6 字放寬為 10 字）
FREQUENCY = re.compile(r"(每|一)(天|日|週|周|星期|個月|餐)[^，。]{0,10}" + NUM + r"\s*(次|回)")
RATE = re.compile(r"(每(天|日|週|周|餐|次|公斤)|一(天|日|週|餐)|一天|一週|體重每)")
DRUG_ADVICE = re.compile(r"(服用|吃|使用|補充|搭配|停用|停止)[^，。]{0,8}(藥|藥物|錠|膠囊|處方|胰島素|抗生素|類固醇)")
# 只勸使用者諮詢專業人員、沒有指示怎麼用藥的句子，不算用藥建議（2026-11-09 使用者同意）
CONSULT = re.compile(r"(諮詢|請教|詢問|告知|尋求)[^，。]{0,6}(醫師|醫生|藥師|專業人士|專業人員|營養師)")
DRUG_INSTRUCTION = re.compile(r"停用|停止|停藥|減藥|加藥|換藥|一起吃|一起服用|併用|合併|劑量|避免|不要|不可|不能|切勿|勿|別")
# 食物成分含量（「每 100 克約含 11.3 毫克葉黃素」）不是攝取劑量；句中出現攝取用語時仍視為劑量（2026-11-09 使用者同意）
NUTRIENT_CONTENT = re.compile(r"每\s*(100|一百)\s*(公克|克|g|毫升|ml|mL)[^，。；]{0,12}?(約含|含有|含|提供|約有)", re.I)
INTAKE_WORDS = re.compile(r"每(天|日|週|周|星期|餐|次)|一(天|日|週|周|餐)|建議|不超過|不宜|上限|攝取量|"
                          r"(最多|至少)(只能|可以|要|吃|喝|攝取|補充|\d|[一二兩三四五六七八九十半])|"
                          r"應(該)?(吃|喝|攝取|補充)|可以(吃|喝)|只要吃|就夠|為限|即可")
# 寫給疾病族群的句子（「胃潰瘍患者建議…」「糖尿病的人要…」）：一般食物題的回答也刪掉（2026-11-10 使用者決定）
DISEASE_NAMES = (r"糖尿病|高血壓|低血壓|高血脂|三高|痛風|高尿酸|腎臟病|腎病|慢性腎|心臟病|心血管疾病|脂肪肝|肝病|肝炎|肝硬化|"
                 r"癌症|胃潰瘍|十二指腸潰瘍|潰瘍|胃食道逆流|胃酸逆流|腸躁症|貧血|骨質疏鬆|甲狀腺(亢進|低下|疾病)?|慢性病|"
                 r"結石|關節炎|氣喘|失智|巴金森|帕金森|中風|肌少症|代謝症候群|妊娠糖尿病|心臟衰竭|心衰竭|腎衰竭|腎功能不全")
DISEASE_POPULATION = re.compile(
    r"患者|病人|病友|病患|糖友|腎友|癌友|洗腎|透析|化療|術後|手術後|罹患[^，。]{0,10}(的人|者)|"
    r"(" + DISEASE_NAMES + r")[^，。、]{0,3}(的人|者|族群|人士)|"
    r"(血糖|血壓|血脂|膽固醇|尿酸)(值)?(偏|過|太)?高的人|有(" + DISEASE_NAMES + r")(問題)?的人|"
    # 「腎功能不佳者」「肝功能不好的人」（2026-11-10 類別回答評估補上）
    r"(腎|肝|心|心臟|甲狀腺)(臟)?功能[^，。、]{0,4}(不佳|不好|異常|較差|差|低下|不全)[^，。、]{0,2}(者|的人|族群|患者)")
YOU_DIAGNOSIS = re.compile(r"(你|您)(可能|應該|大概|很可能|恐怕)?(已經)?(是|得了|罹患|患有|有)[^，。]{0,8}(病|症|癌)")
CITE = re.compile(r"\[(\d{1,2})\]")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def greeting(text: str) -> str | None:
    """整句只有寒暄（可夾語助詞與標點）時回傳類別。"""
    t = normalize(text).lower()
    for kind, pattern in GREETINGS.items():
        if re.fullmatch(FILLER + pattern + FILLER + r"((我是|我叫).{0,6})?" + FILLER, t):
            return kind
    if re.fullmatch(FILLER + GREETINGS["hello"] + FILLER + GREETINGS["who"] + FILLER, t):
        return "who"
    return None


def precheck(text: str) -> tuple[str, str] | None:
    """事前判斷：回傳（類型, 固定回覆）或 None（可以檢索）。"""
    t = normalize(text)
    if SELF_HARM.search(t):
        return "emergency", SELF_HARM_REPLY
    if EMERGENCY.search(t):
        return "emergency", EMERGENCY_REPLY
    if DRUG.search(t):
        return "declined_medical", DRUG_REPLY
    if DIAGNOSIS.search(t):
        return "declined_medical", DIAGNOSIS_REPLY
    if DISEASE_TOPIC.search(t):
        return "declined_medical", DISEASE_REPLY
    return None


def asks_quantity(text: str) -> bool:
    return bool(QUANTITY_QUESTION.search(normalize(text)))


def has_dose(sentence: str) -> bool:
    """劑量／攝取數量：數字＋單位（例如 20 克、每天 2 杯、一天一顆）、頻率（每週 3 次）。比例（20%）、倍數不算。
    食物成分含量（每 100 克含多少）且句中沒有攝取用語時不算。"""
    if FREQUENCY.search(sentence):
        return True
    if NUTRIENT_CONTENT.search(sentence) and not INTAKE_WORDS.search(sentence):
        return False
    for m in DOSE.finditer(sentence):
        number = m.group(1)
        if re.fullmatch(r"[一兩]", number) and m.group(7) in ("個", "種", "份", "片", "條", "根") and not RATE.search(sentence):
            continue  # 「一個原則」「一份研究」這類非份量用法
        return True
    return False


def check_sentence(sentence: str, n_refs: int) -> tuple[str | None, str | None]:
    """回傳（處理後句子或 None, 移除原因）。"""
    if has_dose(sentence):
        return None, "dose"
    if DRUG_ADVICE.search(sentence) and not (CONSULT.search(sentence) and not DRUG_INSTRUCTION.search(sentence)):
        return None, "drug"
    if YOU_DIAGNOSIS.search(sentence):
        return None, "diagnosis"
    if DISEASE_POPULATION.search(sentence):
        return None, "disease"
    cleaned = CITE.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= n_refs else "", sentence)
    # 沒有引用編號的句子是模型自己加的，刪除；說明參考資料範圍的句子（「參考資料沒有提到…」）保留
    if cleaned.strip() and not CITE.search(cleaned) and "參考資料" not in cleaned:
        return None, "uncited"
    return cleaned, None


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """把串流緩衝切成完整句子；回傳（完整句子, 剩下未完成的部分）。引用編號跟在句號後也算同一句。"""
    out, start = [], 0
    for m in re.finditer(r"[。！？!?](\s*\[\d{1,2}\])*|\n", buffer):
        end = m.end()
        if m.group(0) != "\n" and end == len(buffer):
            break  # 句號在緩衝最後，可能還有 [n] 沒進來
        piece = buffer[start:end]
        if piece.strip():
            out.append(piece)
        start = end
    return out, buffer[start:]


def citations(text: str) -> list[int]:
    return sorted({int(n) for n in CITE.findall(text)})
