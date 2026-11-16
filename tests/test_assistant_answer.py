"""R14 步驟 4：回答規則、串流過濾、門檻、追問與服務端點。假檢索／假 LLM，不連網、不連資料庫。"""
import json

import pytest
from fastapi.testclient import TestClient

from assistant_rag import safety
from assistant_rag.answer import Assistant, InvalidInput, Settings
from assistant_rag.providers import ProviderUnavailable
from assistant_rag.retrieval import Hit, Result
from assistant_rag.server import make_app


def hit(n, score):
    return Hit(n, f"c{n}", f"hy:{n}", score, f"文章{n}", f"https://x/{n}", "Hello醫師", ["小標"], f"內文{n}", "2026-01-01")


class FakeRetriever:
    conn = None
    version = "v"

    def __init__(self, scores=(0.9, 0.8, 0.7)):
        self.scores, self.queries = scores, []

    def search(self, query, method, emb):
        self.queries.append(query)
        return Result(query, method, emb, [hit(i + 1, s) for i, s in enumerate(self.scores)])


class FakeChat:
    def __init__(self, pieces=(), complete="{}", fail=None):
        self.pieces, self.reply, self.fail = list(pieces), complete, fail
        self.streamed, self.completed = [], []

    def complete(self, messages, *, json_mode=False, temperature=0.0):
        self.completed.append(messages)
        return self.reply

    def stream(self, messages, *, temperature=0.0):
        self.streamed.append(messages)
        if self.fail:
            raise ProviderUnavailable(self.fail)
        yield from self.pieces


def bot(chat=None, retriever=None, threshold=0.5):
    return Assistant(retriever or FakeRetriever(), chat or FakeChat(),
                     Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=threshold), converter=lambda s: s)


@pytest.mark.parametrize("text,kind", [("你好", "hello"), ("謝謝你！", "thanks"), ("你是誰？", "who"),
                                       ("嗨～", "hello"), ("掰掰", "bye"), ("你好，吃地瓜有什麼好處", None)])
def test_greeting_rules(text, kind):
    assert safety.greeting(text) == kind


@pytest.mark.parametrize("text,kind", [
    ("我胸口很痛怎麼辦", "emergency"), ("我爸突然嘴歪講話不清楚", "emergency"), ("我不想活了", "emergency"),
    ("高血壓要吃什麼藥", "declined_medical"), ("降血糖藥可以停嗎", "declined_medical"),
    ("我是不是得了糖尿病", "declined_medical"), ("檢查報告尿酸8正常嗎", "declined_medical"),
    ("吃藥膳好嗎", None), ("痛風可以吃豆腐嗎", "declined_medical"), ("維他命D一天要吃多少", None)])
def test_precheck_rules(text, kind):
    result = safety.precheck(text)
    assert (result[0] if result else None) == kind


@pytest.mark.parametrize("sentence,reason", [
    ("每日蛋白質可增至72至87公克[1]。", "dose"), ("建議每天喝一杯牛奶[2]。", "dose"), ("一天可以吃兩顆蛋。", "dose"),
    ("奇亞籽每日20克即可。", "dose"), ("每週吃3次魚[2]。", "dose"), ("建議服用降血壓藥物[1]。", "drug"),
    ("您可能得了糖尿病。", "diagnosis"), ("約20%的普林來自飲食[1]。", None), ("這是一個好方法[3]。", None),
    ("攝取3種顏色的蔬菜[1]。", None), ("少量多次喝水[1]。", None)])
def test_sentence_checks(sentence, reason):
    assert safety.check_sentence(sentence, 5)[1] == reason


def test_invalid_citation_numbers_removed_and_split_keeps_trailing_cites():
    assert safety.check_sentence("地瓜含有維生素A[7][2]。", 5)[0] == "地瓜含有維生素A[2]。"
    assert safety.split_sentences("地瓜好[1]。纖維多。[2]還有") == (["地瓜好[1]。", "纖維多。[2]"], "還有")
    assert safety.split_sentences("地瓜好。") == ([], "地瓜好。")


def events(assistant, message, history=None):
    return list(assistant.events(message, history))


def test_greeting_and_emergency_skip_retrieval_and_llm():
    retriever, chat = FakeRetriever(), FakeChat()
    a = bot(chat, retriever)
    for message, kind in [("你好", "greeting"), ("胸口劇痛", "emergency"), ("要吃什麼藥", "declined_medical")]:
        out = a.answer(message)
        assert out["type"] == kind and out["done"]["type"] == kind and out["references"] == []
    assert retriever.queries == [] and chat.streamed == [] and chat.completed == []
    assert "119" in a.answer("胸口劇痛")["text"]


def test_below_threshold_refuses_without_llm():
    chat = FakeChat(["不該出現。"])
    out = bot(chat, FakeRetriever((0.3,)), threshold=0.5).answer("今天天氣如何")
    assert out["type"] == "out_of_scope" and out["done"]["top_score"] == 0.3
    assert chat.streamed == [] and "找不到" in out["text"]


def test_answer_streams_filtered_sentences_citations_and_notes():
    chat = FakeChat(["地瓜富含膳食", "纖維[1]。奇亞籽每日", "20克即可[2]。建議服用降血壓藥物[1]。", "地瓜含維生素A[9][3]。",
                     "可以蒸或烤[2]"])
    a = bot(chat)
    evs = events(a, "吃地瓜有什麼好處")
    kinds = [e["event"] for e in evs]
    assert kinds[:2] == ["meta", "references"] and kinds[-1] == "done"
    text = "".join(e["data"]["text"] for e in evs if e["event"] == "delta")
    assert "膳食纖維[1]。" in text and "20克" not in text and "藥物" not in text
    assert "地瓜含維生素A[3]。" in text and "可以蒸或烤[2]" in text  # 最後沒有句號的片段也輸出
    done = evs[-1]["data"]
    assert done["citations"] == [1, 2, 3] and done["removed"] == {"dose": 1, "drug": 1}
    assert safety.QUANTITY_NOTE in text and text.endswith(safety.DISCLAIMER)
    refs = evs[1]["data"]
    assert [r["n"] for r in refs] == [1, 2, 3] and refs[0]["url"] == "https://x/1"
    prompt = chat.streamed[0][1]["content"]
    assert "[1] 文章1｜小標（Hello醫師）\n內文1" in prompt and "\n\n問題：吃地瓜有什麼好處\n" in prompt
    assert prompt.endswith("不要提到這些食物，也不要說明它們不同。）")  # 地瓜／地瓜葉提醒
    assert "不提供任何劑量" in chat.streamed[0][0]["content"]


def test_all_sentences_removed_gets_fallback():
    out = bot(FakeChat(["每天吃2顆蛋[1]。"])).answer("一天可以吃幾顆蛋")
    assert "參考資料中沒有可以直接回答" in out["text"] and safety.QUANTITY_NOTE in out["text"]
    assert out["done"]["citations"] == []


def test_followup_rewritten_with_history_and_rechecked():
    chat = FakeChat(["地瓜葉含鈣[1]。"], complete=json.dumps({"question": "地瓜葉有什麼好處"}, ensure_ascii=False))
    retriever = FakeRetriever()
    a = bot(chat, retriever)
    history = [{"role": "user", "content": "地瓜有什麼好處"}, {"role": "assistant", "content": "富含纖維[1]。"}]
    out = a.answer("那地瓜葉呢", history)
    assert retriever.queries == ["地瓜葉有什麼好處"] and out["type"] == "answer"
    assert "使用者（最後一句）：那地瓜葉呢" in chat.completed[0][1]["content"]
    # 改寫後的問題若是用藥問題，仍然婉拒
    chat2 = FakeChat(complete=json.dumps({"question": "高血壓要吃什麼藥"}, ensure_ascii=False))
    assert bot(chat2).answer("那要吃什麼", history)["type"] == "declined_medical"
    # 沒有前文時不呼叫改寫
    chat3 = FakeChat(["好[1]。"])
    bot(chat3).answer("地瓜有什麼好處")
    assert chat3.completed == []


def test_bad_input_and_llm_failure():
    with pytest.raises(InvalidInput):
        events(bot(), "")
    with pytest.raises(InvalidInput):
        events(bot(), "地" * 501)
    with pytest.raises(InvalidInput):
        events(bot(), "地瓜", [{"role": "system", "content": "x"}])
    out = bot(FakeChat(fail="LLM_FAILED")).answer("吃地瓜有什麼好處")
    assert out["error"] == "LLM_FAILED"


def test_non_rerank_method_requires_explicit_threshold(monkeypatch):
    monkeypatch.delenv("RAG_REFUSAL_THRESHOLD", raising=False)
    with pytest.raises(ValueError):
        Settings(method="hybrid", embedder="embeddinggemma")
    assert Settings(method="multi_query").threshold == 0.5


def client(health=None):
    app = make_app("secret", lambda: bot(FakeChat(["地瓜富含纖維[1]。"])),
                   health or (lambda: {"status": "ok", "checks": {}}))
    return TestClient(app)


def test_server_auth_sse_and_input_errors():
    c = client()
    assert c.post("/v1/chat", json={"message": "地瓜"}).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    r = c.post("/v1/chat", json={"message": "吃地瓜有什麼好處"}, headers=headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in r.text.split("\n\n") if b]
    assert blocks[0].startswith("event: meta") and blocks[-1].startswith("event: done")
    assert json.loads(blocks[1].split("data: ", 1)[1])[0]["title"] == "文章1"
    assert c.post("/v1/chat", json={"message": ""}, headers=headers).status_code == 422
    assert c.post("/v1/chat", json={"message": "x", "history": "bad"}, headers=headers).status_code == 422


def test_server_health_and_unavailable_assistant():
    headers = {"Authorization": "Bearer secret"}
    assert client().get("/v1/health", headers=headers).json()["status"] == "ok"
    down = client(lambda: {"status": "unavailable", "checks": {"llm": "LLM_UNAVAILABLE"}})
    assert down.get("/v1/health", headers=headers).status_code == 503

    def broken():
        raise RuntimeError("db down")
    app = TestClient(make_app("secret", broken))
    assert app.post("/v1/chat", json={"message": "地瓜"}, headers=headers).status_code == 503
    with pytest.raises(ValueError):
        make_app("", broken)


def test_bullets_on_own_lines_and_simplified_only_conversion():
    from assistant_rag.textconv import to_traditional
    assert to_traditional("膳食纤维与蛋白质") == "膳食纖維與蛋白質"
    assert to_traditional("干擾、里面、便秘、后來") == "干擾、里面、便秘、后來"  # 繁體文字不被改壞
    a = Assistant(FakeRetriever(), FakeChat(["・地瓜含纤维[1]。", "・地瓜含鈣[2]。"]),
                  Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5))
    text = a.answer("地瓜好處")["text"]
    assert text.startswith("・地瓜含纖維[1]。\n・地瓜含鈣[2]。")


from assistant_rag.categories import FoodCategories

CATS = FoodCategories({"categories": {"sauce": {"label": "醬料與沾醬", "query": "醬料和沾醬怎麼吃比較健康"}},
                       "foods": {"豆瓣醬": "sauce", "辣豆瓣醬": "sauce", "沒類別": "none"}})


class ScriptedRetriever(FakeRetriever):
    """依查詢回傳不同分數：類別檢索句一個分數，其餘一個分數。"""

    def __init__(self, first, category, text=None):
        super().__init__()
        self.first, self.category, self.text = first, category, text

    def search(self, query, method, emb):
        self.queries.append(query)
        score = self.category if query == "醬料和沾醬怎麼吃比較健康" else self.first
        h = hit(1, score)
        if self.text and query != "醬料和沾醬怎麼吃比較健康":
            h = Hit(1, "c1", "hy:1", score, "文章1", "https://x/1", "Hello醫師", [], self.text, None)
        return Result(query, method, emb, [h])


def test_category_lookup_prefers_longest_name_and_skips_unknown_category():
    assert CATS.lookup("辣豆瓣醬吃多了會怎樣").food == "辣豆瓣醬"
    assert CATS.lookup("豆瓣醬健康嗎").label == "醬料與沾醬"
    assert CATS.lookup("沒類別可以吃嗎") is None and CATS.lookup("今天天氣如何") is None


def test_category_answer_when_food_itself_below_threshold():
    chat = FakeChat(["醬料通常含鈉，建議適量沾取[1]。"])
    retriever = ScriptedRetriever(first=0.2, category=0.9)
    a = Assistant(retriever, chat, Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=CATS)
    out = a.answer("辣豆瓣醬吃多了會怎樣")
    assert retriever.queries == ["辣豆瓣醬吃多了會怎樣", "醬料和沾醬怎麼吃比較健康"]
    assert out["type"] == "category_answer" and out["done"]["fallback"]["food"] == "辣豆瓣醬"
    assert out["text"].startswith("目前沒有「辣豆瓣醬」本身的資料，以下是「醬料與沾醬」這一類的一般資訊")
    prompt = chat.streamed[0][1]["content"]
    assert "不要用「辣豆瓣醬」當主詞" in prompt and "不要說參考資料的內容是辣豆瓣醬的特性" in prompt and "原問題：辣豆瓣醬吃多了會怎樣" in prompt
    assert out["done"]["top_score"] == 0.2 and out["done"]["citations"] == [1]


def test_category_also_below_threshold_refuses_and_known_food_not_rerouted():
    chat = FakeChat(["不該出現。"])
    retriever = ScriptedRetriever(first=0.2, category=0.3)
    a = Assistant(retriever, chat, Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=CATS)
    assert a.answer("豆瓣醬健康嗎")["type"] == "out_of_scope" and chat.streamed == []
    # 本身就查得到（過門檻且文章有提到）時不改查類別
    ok = ScriptedRetriever(first=0.8, category=0.9, text="豆瓣醬的鈉含量偏高。")
    b = Assistant(ok, FakeChat(["可以[1]。"]), Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=CATS)
    assert b.answer("豆瓣醬健康嗎")["type"] == "answer" and ok.queries == ["豆瓣醬健康嗎"]


REFUSE_CATS = FoodCategories({"categories": {"egg": {"label": "蛋類", "query": "醬料和沾醬怎麼吃比較健康"}},
                              "foods": {"雞蛋": "egg"}, "refuse": {"皮蛋": "加工蛋品", "雞蛋": "已有類別，忽略"}})


@pytest.mark.parametrize("first,text", [(0.2, None), (0.9, "生蛋要煮熟再吃。")])
def test_refuse_food_not_answered_with_category_or_similar_food(first, text):
    """不確定能代答的食材：分數不夠或前幾篇沒提到它，就拒答，不改查類別、不拿相近食物回答。"""
    assert REFUSE_CATS.lookup("皮蛋有什麼營養").refuse and not REFUSE_CATS.lookup("雞蛋有什麼營養").refuse
    chat = FakeChat(["不該出現。"])
    retriever = ScriptedRetriever(first=first, category=0.9, text=text)
    a = Assistant(retriever, chat, Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=REFUSE_CATS)
    out = a.answer("皮蛋有什麼營養")
    assert out["type"] == "out_of_scope" and chat.streamed == [] and retriever.queries == ["皮蛋有什麼營養"]
    assert out["text"] == safety.no_food_reply("皮蛋") and "「皮蛋」本身的資料" in out["text"]


def test_refuse_food_answered_when_articles_mention_it():
    retriever = ScriptedRetriever(first=0.8, category=0.9, text="皮蛋是鹼性加工蛋品。")
    a = Assistant(retriever, FakeChat(["皮蛋是加工蛋品[1]。"]), Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=REFUSE_CATS)
    assert a.answer("皮蛋有什麼營養")["type"] == "answer"


def test_repository_food_categories_well_formed():
    cats = FoodCategories.load()
    assert len(cats.foods) >= 200 and set(cats.foods.values()) <= set(cats.categories)
    assert all(c["label"] and c["query"] for c in cats.categories.values())
    assert cats.lookup("蠔油可以常吃嗎").category == "sauce"
    assert cats.lookup("梅花肉有什麼營養").category == "pork"
    # 不確定的對照刪掉：蛋豆腐不是豆腐、皮蛋不用一般蛋類資料代答
    for food in ("皮蛋", "泡香菇的水", "油花生", "蛋豆腐"):
        assert cats.lookup(f"{food}有什麼營養").refuse
    assert cats.match["鳳梨罐頭"] == ("鳳梨罐頭",) and "小茴香" not in cats.aliases


@pytest.mark.parametrize("text", ["我最近一直頭暈，是不是貧血", "我的血糖空腹130，這樣算糖尿病嗎", "我是不是得了胃潰瘍",
                                  "我是不是有點貧血", "這是不是胃炎", "我小孩一直拉肚子是腸胃炎嗎", "我媽最近常頭暈是貧血嗎"])
def test_diagnosis_questions_declined(text):
    assert safety.precheck(text)[0] == "declined_medical"


@pytest.mark.parametrize("text", ["吃太多糖會不會得糖尿病", "是不是吃太鹹容易高血壓", "吃辣是不是會胃潰瘍",
                                  "是不是要少吃糖才不會糖尿病", "每天吃2顆蛋算多嗎", "貧血可以吃什麼"])
def test_risk_and_diet_questions_not_treated_as_diagnosis(text):
    result = safety.precheck(text)
    assert result is None or result[1] != safety.DIAGNOSIS_REPLY


@pytest.mark.parametrize("text", ["脂肪肝要多吃什麼食物", "糖尿病患者嘴饞可以吃什麼零食", "痛風可以吃海鮮嗎",
                                  "什麼飲食可以幫助預防巴金森氏症", "癌症病人可以吃什麼點心", "吃苦瓜可以降血糖嗎",
                                  "吃素的人為什麼也會高血壓", "手術後要吃什麼補傷口"])
def test_disease_diet_questions_declined(text):
    assert safety.precheck(text) == ("declined_medical", safety.DISEASE_REPLY)


@pytest.mark.parametrize("text", ["吃隔夜菜會致癌嗎", "烤肉會致癌嗎？要怎麼吃比較安全", "吃味精會過敏嗎", "吃地瓜有什麼好處",
                                  "蝦子的膽固醇高嗎", "吃白飯會讓血糖上升嗎", "失眠可以吃什麼改善", "便秘要多吃什麼"])
def test_food_safety_and_general_questions_not_declined(text):
    assert safety.precheck(text) is None


def test_long_frequency_sentence_removed():
    assert safety.check_sentence("每天進食次數不超過 5 次，並減少含糖果的習慣 [1]。", 1)[1] == "dose"


@pytest.mark.parametrize("sentence", ["菠菜被認為是含有最多葉黃素的冠軍，每 100 克約含 11.3 毫克 [1]。",
                                      "每 100 公克黃色玉米可提供約 32.2 毫克的葉黃素及玉米黃素 [1]。",
                                      "若正在服用藥物或患有特定疾病，更需留意攝取量並諮詢專業人士 [1]。"])
def test_nutrient_content_and_consult_sentences_kept(sentence):
    assert safety.check_sentence(sentence, 1)[1] is None


@pytest.mark.parametrize("sentence,reason", [
    ("菠菜每100克含11.3毫克葉黃素，建議每天吃一碗。", "dose"),
    ("每100克黑芝麻約含1400毫克鈣，一次最多吃兩匙。", "dose"),
    ("每週攝取兩次深海魚以獲得 Omega-3。", "dose"),
    ("成人一天不宜攝取超過 4 克的薑根。", "dose"),
    ("1 天僅需 1 顆魚油便能達到建議攝取量。", "dose"),
    ("正在服用抗凝血藥物者，請諮詢醫師是否可以一起吃。", "drug"),
    ("服用降血壓藥物時要避免葡萄柚，請諮詢醫師。", "drug"),
])
def test_intake_amounts_and_drug_instructions_still_removed(sentence, reason):
    assert safety.check_sentence(sentence, 1)[1] == reason


def test_prohibition_on_medicine_counts_as_drug_instruction():
    assert safety.check_sentence("建議尋求醫師諮詢，切勿自行服用他人藥物。", 1)[1] == "drug"


@pytest.mark.parametrize("sentence", [
    "・胃潰瘍患者建議將鳳梨挪到飯後食用，以避免酵素對胃部造成額外刺激 [2]。",
    "糖尿病的人吃水果要注意 [1]。", "腎友應避免楊桃 [1]。", "血糖偏高的人可以多吃蔬菜 [1]。",
    "痛風族群要少喝含糖飲料 [2]。", "有高血壓問題的人應少吃醃漬物 [1]。", "化療期間可以吃清淡的食物 [1]。"])
def test_disease_population_sentences_removed(sentence):
    assert safety.check_sentence(sentence, 3)[1] == "disease"


@pytest.mark.parametrize("sentence", [
    "腸胃較敏感的人若過量食用，可能會出現胃部不適 [1]。", "鳳梨含有維生素C，有助於維持免疫功能 [1]。",
    "孕婦可以適量食用 [1]。", "吃太多糖可能增加罹患糖尿病的風險 [1]。", "高溫油炸會產生致癌物 [2]。"])
def test_general_sentences_not_treated_as_disease_population(sentence):
    assert safety.check_sentence(sentence, 3)[1] is None


def test_disease_sentence_removed_and_note_added():
    from assistant_rag.retrieval import Hit
    hit = Hit(1, "c:0", "hn:1", 0.9, "木瓜", "https://example.test/1", "健康醫療網", [], "木瓜含有木瓜酵素。", None)

    class Ret:
        conn, version = None, "v"

        def search(self, *a, **k):
            return type("R", (), {"hits": [hit]})()

    class Chat:
        name = "fake"

        def stream(self, messages, temperature=None):
            yield "木瓜含有木瓜酵素，可幫助消化 [1]。腎病患者不宜食用木瓜 [1]。"

    from assistant_rag.answer import Assistant
    out = Assistant(Ret(), Chat(), converter=lambda s: s, categories=False).answer("吃木瓜有什麼好處")
    assert "腎病患者" not in out["text"] and safety.DISEASE_NOTE in out["text"]
    assert out["done"]["removed"] == {"disease": 1}


def test_off_topic_article_above_threshold_reroutes_to_category():
    # 過門檻但文章沒提到豆瓣醬（例如只談醬油）→ 類別回答，原因標 not_mentioned
    chat = FakeChat(["醬料通常含鈉[1]。"])
    retriever = ScriptedRetriever(first=0.9, category=0.9, text="醬油的鈉含量偏高。")
    a = Assistant(retriever, chat, Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=CATS)
    out = a.answer("豆瓣醬健康嗎")
    assert out["type"] == "category_answer" and out["done"]["fallback"]["reason"] == "not_mentioned"
    assert retriever.queries == ["豆瓣醬健康嗎", "醬料和沾醬怎麼吃比較健康"]
    # 類別也低於門檻 → 拒答，不拿無關文章回答
    low = ScriptedRetriever(first=0.9, category=0.2, text="醬油的鈉含量偏高。")
    b = Assistant(low, FakeChat(["不該出現。"]), Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=CATS)
    assert b.answer("豆瓣醬健康嗎")["type"] == "out_of_scope"


def test_match_terms_count_synonyms_as_mentioned():
    cats = FoodCategories({"categories": {"veg": {"label": "蔬菜", "query": "多吃蔬菜有什麼好處"}},
                           "foods": {"金瓜": "veg"}, "match": {"金瓜": ["金瓜", "南瓜"]}})
    food = cats.lookup("金瓜有什麼營養")
    assert food.terms == ("金瓜", "南瓜")
    assert food.mentioned_in([Hit(1, "c", "a", 0.9, "南瓜營養好處多", "u", "s", [], "", None)])
    assert not food.mentioned_in([Hit(1, "c", "a", 0.9, "冬瓜消水腫", "u", "s", [], "冬瓜", None)])
    real = FoodCategories.load()
    assert real.lookup("烏醋可以常吃嗎").terms == ("醋",) and real.lookup("柴魚有什麼營養").terms == ("柴魚",)


class AliasRetriever(FakeRetriever):
    """本名查不到；別名「南瓜」查得到且文章有提到南瓜。"""

    def search(self, query, method, emb):
        self.queries.append(query)
        if "南瓜" in query:
            h = Hit(1, "c1", "hy:1", 0.9, "南瓜營養好處多", "https://x/1", "Hello醫師", [], "南瓜富含β-胡蘿蔔素。", None)
        elif query == "多吃蔬菜有什麼好處":
            h = Hit(1, "c2", "hy:2", 0.9, "蔬菜", "https://x/2", "Hello醫師", [], "蔬菜", None)
        else:
            h = Hit(1, "c3", "hy:3", 0.2, "冬瓜", "https://x/3", "Hello醫師", [], "冬瓜", None)
        return Result(query, method, emb, [h])


ALIAS_CATS = FoodCategories({"categories": {"veg": {"label": "蔬菜", "query": "多吃蔬菜有什麼好處"}},
                             "foods": {"金瓜": "veg", "冬菇": "veg"}, "aliases": {"金瓜": ["南瓜"]}})


def test_alias_requery_answers_directly_with_notice():
    chat = FakeChat(["南瓜富含β-胡蘿蔔素[1]。"])
    retriever = AliasRetriever()
    a = Assistant(retriever, chat, Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=ALIAS_CATS)
    out = a.answer("金瓜有什麼好處")
    assert retriever.queries == ["金瓜有什麼好處", "南瓜有什麼好處"]
    assert out["type"] == "answer" and out["text"].startswith("「金瓜」和「南瓜」是同一種食物")
    assert "原問題" not in chat.streamed[0][1]["content"] and "南瓜有什麼好處" in chat.streamed[0][1]["content"]


def test_food_without_alias_goes_straight_to_category():
    retriever = AliasRetriever()
    a = Assistant(retriever, FakeChat(["蔬菜好[1]。"]), Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=ALIAS_CATS)
    assert a.answer("冬菇有什麼好處")["type"] == "category_answer"
    assert retriever.queries == ["冬菇有什麼好處", "多吃蔬菜有什麼好處"]


def test_repository_aliases_prefer_recipe_workbook():
    cats = FoodCategories.load()
    assert cats.lookup("粉絲熱量高嗎").aliases == ("冬粉", "乾冬粉")   # 食譜試算表
    assert cats.lookup("金瓜有什麼好處").aliases == ("南瓜",)          # 人工補充
    assert cats.lookup("豬油蔥健康嗎").aliases == ()                   # 只差異體字，不重查


@pytest.mark.parametrize("sentence", ["市售薄鹽醬油鉀含量較高，不適合心臟衰竭或腎功能不佳者使用 [1]。",
                                      "肝功能不好的人要少喝酒 [1]。"])
def test_organ_function_population_removed(sentence):
    assert safety.check_sentence(sentence, 3)[1] == "disease"


def test_uncited_sentences_removed_but_reference_scope_kept():
    assert safety.check_sentence("・若對雞蛋過敏，食用時應特別留意成分標示。", 3)[1] == "uncited"
    assert safety.check_sentence("豆腐是好的蛋白質來源 [9]。", 3)[1] == "uncited"   # 編號超出範圍，清掉後沒有引用
    assert safety.check_sentence("參考資料沒有提到具體的份量。", 3)[1] is None


# ---- 名稱相近但不同的食物（2026-11-10 使用者回報：問地瓜不應該出現地瓜葉） ----

def test_distinct_foods_detection():
    from assistant_rag.categories import about_other_food, distinct_foods
    assert distinct_foods("吃地瓜有什麼好處") == {"地瓜": ("地瓜葉", "地瓜粉")}
    # 問題本身就問地瓜葉，或兩者都問：地瓜葉不算「混進來」
    assert distinct_foods("地瓜葉有什麼營養") == {}
    assert distinct_foods("地瓜和地瓜葉哪個好") == {"地瓜": ("地瓜粉",)}
    d = distinct_foods("吃地瓜有什麼好處")
    assert about_other_food("・深綠色的地瓜葉則提供高鈣與維生素K [2]。", d)
    # 兩者混在同一句也刪（實測模型會寫「地瓜葉雖非地瓜本體，但…」）
    assert about_other_food("・地瓜葉雖非地瓜本體，但地瓜本身也含有鐵 [4]。", d)
    assert not about_other_food("・地瓜富含膳食纖維 [1]。", d)


def test_sentences_about_other_food_removed_and_model_told():
    chat = FakeChat(["・地瓜富含膳食纖維 [1]。", "・深綠色的地瓜葉則提供高鈣 [2]。", "・地瓜含有鉀 [3]。"])
    ev = events(bot(chat), "吃地瓜有什麼好處")
    text = "".join(e["data"]["text"] for e in ev if e["event"] == "delta")
    assert "地瓜葉" not in text and "膳食纖維" in text and "鉀" in text
    done = ev[-1]["data"]
    assert done["removed"] == {"other_food": 1} and done["citations"] == [1, 3]
    prompt = chat.streamed[0][-1]["content"]
    assert "「地瓜葉」「地瓜粉」不是「地瓜」" in prompt


def test_other_food_kept_when_question_asks_about_it():
    chat = FakeChat(["・地瓜葉提供高鈣 [1]。"])
    text = "".join(e["data"]["text"] for e in events(bot(chat), "地瓜葉有什麼營養") if e["event"] == "delta")
    assert "地瓜葉提供高鈣" in text


def test_articles_titled_about_other_food_not_given_to_model():
    from dataclasses import replace as dc_replace

    class TitledRetriever(FakeRetriever):
        def search(self, query, method, emb):
            result = super().search(query, method, emb)
            titles = ["超級食物地瓜好處多", "地瓜葉6大功效", "地瓜、地瓜葉怎麼吃"]
            return dc_replace(result, hits=[dc_replace(h, title=t) for h, t in zip(result.hits, titles)])

    chat = FakeChat(["・地瓜富含纖維 [1]。"])
    ev = events(bot(chat, TitledRetriever()), "吃地瓜有什麼好處")
    refs = next(e["data"] for e in ev if e["event"] == "references")
    # 〈地瓜葉6大功效〉移除；兩者都談的標題保留；編號重新排
    assert [(r["n"], r["title"]) for r in refs] == [(1, "超級食物地瓜好處多"), (2, "地瓜、地瓜葉怎麼吃")]
    assert "地瓜葉6大功效" not in chat.streamed[0][-1]["content"]


def test_low_scoring_articles_not_given_to_model():
    # 2026-11-12：第 1 名過門檻才回答；其餘每篇要 ≥ article_min_score（預設 0.3）才給模型與列為參考資料
    chat = FakeChat(["・地瓜富含纖維 [1]。"])
    ev = events(bot(chat, FakeRetriever((0.95, 0.6, 0.31, 0.29, 0.1))), "吃地瓜有什麼好處")
    refs = next(e["data"] for e in ev if e["event"] == "references")
    assert [r["n"] for r in refs] == [1, 2, 3]
    prompt = chat.streamed[0][-1]["content"]
    assert "[3] 文章3" in prompt and "文章4" not in prompt and "文章5" not in prompt


def test_article_min_score_configurable(monkeypatch):
    monkeypatch.setenv("RAG_ARTICLE_MIN_SCORE", "0")
    chat = FakeChat(["・地瓜富含纖維 [1]。"])
    a = Assistant(FakeRetriever((0.95, 0.6, 0.1)), chat,
                  Settings(method="hybrid_rerank", embedder="bge-m3", threshold=0.5), converter=lambda s: s)
    refs = next(e["data"] for e in events(a, "吃地瓜有什麼好處") if e["event"] == "references")
    assert len(refs) == 3


# ---------- 標題在講別的食物的文章不給模型（2026-11-15：問地瓜葉卻拿到茼蒿、地瓜的文章） ----------
from assistant_rag.categories import FoodNames  # noqa: E402

NAMES = FoodNames([["地瓜", "甘藷", "番薯"], ["地瓜葉", "甘藷葉"], ["茼蒿"], ["胡蘿蔔"], ["番茄"]],
                  nutrients=["胡蘿蔔素", "β胡蘿蔔素", "地瓜葉"])


def titled(n, title, heading=(), score=0.9):
    return Hit(n, f"c{n}", f"hy:{n}", score, title, f"https://x/{n}", "Hello醫師", list(heading), "內文", None)


def test_food_names_asked_and_mentions():
    assert NAMES.asked("地瓜葉的好處") == ["地瓜葉"]
    assert NAMES.asked("β胡蘿蔔素預防肺癌") == []  # 營養素不是胡蘿蔔
    assert NAMES.mentions("地瓜", "甘藷的營養", "地瓜的好處")  # 同物異名
    assert not NAMES.mentions("地瓜", "地瓜葉6大功效", "地瓜的好處")  # 地瓜葉不算提到地瓜
    assert NAMES.mentions("地瓜", "地瓜葉和地瓜", "地瓜和地瓜葉哪個好")


@pytest.mark.parametrize("topic,other", [
    ("地瓜葉6大功效 功效2. 地瓜葉高鈣", False),
    ("高膳食纖維食物排行 高纖維食物5：地瓜葉", False),
    ("超級食物地瓜好處多 地瓜好處2", True),          # 問地瓜葉時，標題只講地瓜
    ("吃茼蒿5大營養好處 5. β-胡蘿蔔素預防肺癌", True),  # 茼蒿；胡蘿蔔素不算胡蘿蔔
    ("糖尿病飲食原則", False),                        # 標題沒有任何食物：照常保留
])
def test_title_about_other_food(topic, other):
    assert NAMES.title_about_other_food(topic, ["地瓜葉"], "地瓜葉的好處") is other


class ListRetriever(FakeRetriever):
    def __init__(self, hits):
        super().__init__()
        self.hits = hits

    def search(self, query, method, emb):
        self.queries.append(query)
        return Result(query, method, emb, list(self.hits))


def test_answer_drops_articles_whose_title_is_about_another_food():
    chat = FakeChat(["地瓜葉高鈣[1]。"])
    hits = [titled(1, "地瓜葉6大功效", ["功效2. 地瓜葉高鈣"], 0.99), titled(2, "超級食物地瓜好處多", [], 0.97),
            titled(3, "吃茼蒿5大營養好處", ["5. β-胡蘿蔔素預防肺癌"], 0.42), titled(4, "糖尿病飲食原則", [], 0.35)]
    a = Assistant(ListRetriever(hits), chat, Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=None, food_names=NAMES)
    out = a.answer("地瓜葉的好處")
    assert [r["title"] for r in out["references"]] == ["地瓜葉6大功效", "糖尿病飲食原則"]
    prompt = chat.streamed[0][-1]["content"]
    assert "茼蒿" not in prompt and "超級食物地瓜" not in prompt


def test_all_articles_about_other_food_are_kept_rather_than_answering_empty():
    chat = FakeChat(["可以[1]。"])
    a = Assistant(ListRetriever([titled(1, "吃茼蒿5大營養好處")]), chat,
                  Settings(method="hybrid_rerank", embedder="embeddinggemma", threshold=0.5),
                  converter=lambda s: s, categories=None, food_names=NAMES)
    assert [r["title"] for r in a.answer("地瓜葉的好處")["references"]] == ["吃茼蒿5大營養好處"]


def test_default_food_names_load_tfda_synonyms():
    names = FoodNames.load()
    assert {"地瓜葉", "甘藷葉"} <= names.synonyms["地瓜葉"] and "地瓜葉" not in names.synonyms["地瓜"]
    assert names.title_about_other_food("吃茼蒿5大健康好處 5. β-胡蘿蔔素預防肺癌", ["地瓜葉"], "地瓜葉的好處")
