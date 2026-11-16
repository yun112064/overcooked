"""Build preview/art.html: a self-contained review page for the layered room art.

Usage (from the repository root):  python frontend-3d/preview/build_art.py

Each room is a folder under src/assets/rooms/<room>/ holding z0.svg..z4.svg (realistic style).
Layers are embedded as data URIs so the page works from file:// without a server.
The mascot (src/assets/mascot/mascot.svg, illustrated style) is inlined so CSS can animate it.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOMS = ROOT / "src" / "assets" / "rooms"
MASCOT = ROOT / "src" / "assets" / "mascot" / "mascot.svg"
OUT = ROOT / "preview" / "art.html"

LAYER_NAMES = {"z0": "z0 遠景", "z1": "z1 建物／家具", "z2": "z2 物件", "z3": "z3 前景", "z4": "z4 浮動"}
PARALLAX = {"z0": 0.15, "z1": 0.4, "z2": 0.7, "z3": 1.0, "z4": 1.0}


def data_uri(path: Path) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def door_overlay() -> str:
    card = data_uri(ROOMS / "door" / "nameplate-card.svg")
    return f"""
<div class="ov card" style="left:50%;top:35.5%;width:17.5%;background-image:url({card})">
  <div class="card-in">
    <b class="brand">🍅 今天吃啥</b>
    <label>帳號<span class="inp">demo@family</span></label>
    <label>密碼<span class="inp">••••••••</span></label>
    <span class="btn">開門</span>
    <small>沒有帳號？<i>翻面註冊</i></small>
  </div>
</div>"""


def genkan_overlay() -> str:
    plate = data_uri(ROOMS / "genkan" / "door-plate.svg")
    return f"""
<div class="ov wall-title" style="left:36.5%;top:5.2%">歡迎回家，要進哪個家？</div>
<div class="ov plate" style="left:22.5%;top:17.7%;width:8.25%;background-image:url({plate})">
  <div class="plate-in"><b>林家廚房</b><span>4 位成員</span><em>上次進這家</em></div></div>
<div class="ov plate" style="left:32.5%;top:17.7%;width:8.25%;background-image:url({plate})">
  <div class="plate-in"><b>外婆家</b><span>2 位成員</span></div></div>
<div class="ov plate empty" style="left:42.5%;top:17.7%;width:8.25%">
  <svg viewBox="0 0 160 220"><path d="M10 104 L80 50 L150 104 V198 q0 8 -8 8 H18 q-8 0 -8 -8Z" fill="rgba(255,250,240,.16)" stroke="#FFF6E0" stroke-width="3" stroke-dasharray="9 7"/></svg>
  <div class="plate-in"><b>＋</b><span>掛新門牌</span></div></div>"""


def living_overlay() -> str:
    pot = data_uri(ROOT / "src" / "assets" / "pot" / "pot-side.svg")
    food = data_uri(ROOMS / "living" / "plate-food.svg")
    bars = "".join(
        f'<i class="{c}" style="height:{h}%"></i>'
        for h, c in ((62, ""), (78, ""), (55, "unk"), (86, ""), (70, ""), (80, ""), (92, "now"))
    )
    return f"""
<img class="ov pk" style="--k:.15;left:84.4%;top:39.6%;width:5.4%" alt="" src="{pot}">
<div class="ov pk steam" style="--k:.15;left:84.4%;top:33%;width:4%"><i></i><i></i></div>
<div class="ov pk tv" style="--k:.4;left:50%;top:39.9%;width:17.7%">
  <div class="tv-top"><span>近 7 天熱量達成</span><span class="seg"><b>早</b><b>午</b><b class="on">晚</b></span></div>
  <div class="tv-bars">{bars}</div>
  <div class="tv-foot"><span>斜紋＝計算不完整</span><span>今天 96%</span></div>
</div>
<img class="ov pk" style="--k:.7;left:50%;top:75.2%;width:11.6%" alt="" src="{food}">
<div class="ov pk meal" style="--k:.7;left:50%;top:69.4%">
  <b>今晚吃這些</b><span class="ok">已採用</span><span class="ok">熱量 96%</span><span class="ok">蛋白質 104%</span><span class="unk">纖維 未知</span>
</div>
<div class="ov pk tag" style="--k:.15;left:84.4%;top:12.6%">廚房 · 規劃</div>
<div class="ov pk tag" style="--k:.15;left:50%;top:30.6%">全家福 · 家庭</div>
<div class="ov pk tag" style="--k:.4;left:50%;top:58.6%">電視 · 切換餐別</div>"""


def kitchen_overlay() -> str:
    pots = ROOT / "src" / "assets" / "pot"
    ticket = data_uri(ROOMS / "kitchen" / "ticket.svg")

    def tk(x: float, title: str, sub: str, badge: str, cls: str) -> str:
        return (
            f'<div class="ov pk ticket" style="--k:.15;left:{x / 16:.2f}%;top:35.2%;width:5.75%;background-image:url({ticket})">'
            f'<b>{title}</b><span>{sub}</span><em class="{cls}">{badge}</em></div>'
        )

    def note(x: float, y: float, w: float, rot: float, cls: str, body: str) -> str:
        return (
            f'<div class="ov pk note {cls}" style="--k:.4;--r:{rot}deg;left:{x / 16:.2f}%;top:{y / 10:.1f}%;width:{w / 16:.2f}%">'
            f'<i class="mag"></i>{body}</div>'
        )

    return f"""
{tk(180, "11/15 午", "4 人・$600", "生成中", "warn")}
{tk(320, "11/16 早", "尚未選成員", "草稿", "unk")}
{tk(460, "11/13 晚", "3 人・$500", "待決定", "hot")}
<div class="ov pk steam" style="--k:.7;left:47%;top:35.5%;width:5.5%"><i></i><i></i></div>
<img class="ov pk" style="--k:.7;left:47%;top:46.9%;width:8.2%" alt="" src="{data_uri(pots / 'pot-side-lidded.svg')}">
<img class="ov pk" style="--k:.7;left:56.75%;top:46.9%;width:8.2%" alt="" src="{data_uri(pots / 'pot-side.svg')}">
{note(1350, 300, 104, -3, "y", "<b>11/14 晚餐</b>五穀飯<br>清蒸鱸魚<br>蒜炒花椰菜")}
{note(1462, 326, 88, 4, "g", "<b>本週採購</b><span class='big'>$1,840</span>")}
{note(1372, 450, 150, -1, "w", "<b>11 月</b><span class='cal'>2 3 5 7 8 10<br>11 12 13 <u>14</u></span>")}
{note(1466, 600, 88, 3, "p", "<b>11/13 晚</b>待決定")}
{note(1348, 610, 100, -4, "y", "<b>11/12 晚</b><span class='unkt'>計算不完整</span>")}
<div class="ov pk tag" style="--k:.7;left:47%;top:41.2%">生成中 · 看進度</div>
<div class="ov pk tag" style="--k:.7;left:56.75%;top:63.4%">空鍋 · 新的一餐</div>
<div class="ov pk tag" style="--k:.7;left:16.9%;top:64.8%">食譜書 · 食譜庫</div>
<div class="ov pk tag" style="--k:.15;left:20.6%;top:51.6%">單軌 · 接續問卷</div>
<div class="ov pk tag" style="--k:.4;left:87.5%;top:73.6%">便利貼 · 紀錄</div>"""


POT_DIR = ROOT / "src" / "assets" / "pot"
POT_STATES = [("0", "空鍋"), ("1", "步驟1 菜"), ("2", "步驟2 菇"), ("3", "步驟3 肉"), ("4", "步驟4 湯"), ("5", "步驟5 全滿"),
              ("lidded", "蓋鍋・生成中"), ("ready", "完成"), ("failed", "失敗")]

FORM = """
<div class="fm">
  <div class="fm-h"><b>新的一餐</b><span>步驟 3 / 5</span></div>
  <div class="fm-bar"><i class="d"></i><i class="d"></i><i class="c"></i><i></i><i></i></div>
  <p class="fm-done">✓ 1 日期餐別、預算<span>11/16 晚餐・$600</span></p>
  <p class="fm-done">✓ 2 成員<span>爸爸、媽媽、小米</span></p>
  <div class="fm-cur"><b>3 家裡現在有什麼？</b>
    <div class="fm-row"><div class="cam"><i></i><em>拍冰箱或檯面</em></div>
      <div class="chips"><i class="ok">雞蛋 ✓</i><i class="ok">番茄 ✓</i><i class="maybe">青江菜？</i><i class="add">＋ 手動</i></div></div>
    <small>辨識結果要逐項確認；也可以略過這一步</small></div>
  <p class="fm-next">4 主食偏好與不吃的食材</p>
  <p class="fm-next">5 確認・營養預覽</p>
  <div class="fm-btn"><span>上一步</span><span class="pri">下一步</span></div>
</div>"""


def passboard_overlay() -> str:
    pot = data_uri(POT_DIR / "pot-top-3.svg")
    return f"""
<div class="ov pk paper" style="--k:.4;left:25%;top:14.4%;width:28.4%">{FORM}</div>
<img class="ov pk potimg" style="--k:.7;left:68.1%;top:12.6%;width:33%" alt="" src="{pot}">
<div class="ov pk steam potsteam" style="--k:.7;left:68.1%;top:6%;width:16%"><i></i><i></i></div>
<div class="ov pk tag" style="--k:.7;left:68.1%;top:63.6%">鍋 · 每填完一步多一樣</div>"""


RECIPES = [("咖哩雞", "主食・4 人", True), ("鹽酥蝦", "海鮮", False), ("麻婆豆腐", "豆類", False), ("蘿蔔排骨湯", "湯品", True),
           ("炒地瓜葉", "蔬菜", False), ("滷肉", "肉類", False), ("三杯小卷", "海鮮", False), ("地瓜飯", "主食", False),
           ("番茄炒蛋", "蛋類", False), ("番茄雞湯麵", "主食・湯", False)]


def book_overlay() -> str:
    rows = "".join(
        f'<li class="{"sel" if sel else ""}"><b>{name}</b><span>{cat}</span><em>{"✓" if sel else ""}</em></li>'
        for name, cat, sel in RECIPES
    )
    tabs = "".join(
        f'<div class="ov pk tablabel{" on" if i == 0 else ""}" style="--k:.4;left:90.6%;top:{15 + i * 9.6:.1f}%">{t}</div>'
        for i, t in enumerate(["全部", "主食", "肉類", "海鮮", "蔬菜", "湯品"])
    )
    return f"""
<div class="ov pk page" style="--k:.4;left:31.3%;top:14.6%;width:33.4%">
  <div class="pg-h"><span>第 3 頁・全部 48 道</span><span class="search">搜尋：在這裡寫字</span></div>
  <div class="lp-filter"><i class="on">全部</i><i>主食</i><i>肉類</i><i>海鮮</i><i>蔬菜</i><i>湯品</i></div>
  <ol class="rlist">{rows}</ol>
  <div class="pg-f"><span>‹ 上一頁</span><span>3 / 5</span><span>下一頁 ›</span></div>
</div>
<div class="ov pk page" style="--k:.4;left:68.7%;top:14.6%;width:33.4%">
  <h3>咖哩雞</h3><p class="meta">4 人份・搭配白飯・約 35 分鐘</p>
  <h4>食材</h4><p>雞腿 600 g、馬鈴薯 2 顆、紅蘿蔔 1 條、洋蔥 1 顆、咖哩塊 4 塊</p>
  <h4>每份營養（T04 估算）</h4>
  <p>熱量 520 kcal・蛋白質 32 g・膳食纖維 <u class="unkt">未知</u></p>
  <div class="pg-btn"><span class="pri">✓ 已夾進托盤</span><span>拍冰箱</span></div>
</div>
{tabs}
<div class="ov pk tray" style="--k:.7;left:70%;top:82%;width:29.6%">
  <span class="card">咖哩雞</span><span class="card">蘿蔔排骨湯</span><span class="card empty">＋</span><span class="go">送去出菜口</span>
</div>
<div class="ov pk tag" style="--k:.4;left:14.6%;top:6.2%">闔上書 · 回廚房</div>"""


TABLEWARE = ROOT / "src" / "assets" / "tableware"


def dining_overlay() -> str:
    import math

    cx, cy = 800.0, 500.0

    def img(name: str, x: float, y: float, w: float, h: float, rot: float = 0, k: float = 0.7, cls: str = "") -> str:
        r = f"--r:{rot}deg;" if rot else ""
        return (f'<img class="ov pk rot {cls}" style="--k:{k};{r}left:{x / 16:.2f}%;top:{(y - h / 2) / 10:.2f}%;width:{w / 16:.2f}%" '
                f'alt="" src="{data_uri(TABLEWARE / name)}">')

    out = []
    # chairs first (they sit under everything else), then settings, bowls, dishes
    members = [("爸爸", "102%", "95%", "ok", "通過", 180, (800, 190)), ("媽媽", "108%", "112%", "ok", "通過", -90, (1004, 392)),
               ("小米", "116%", "98%", "warn", "熱量超出", 0, (800, 812)), ("阿嬤", "未知", "未知", "unk", "計算不完整", 90, (596, 392))]
    for name, kcal, prot, cls, badge, rot, label in members:
        a = math.radians(rot)
        down = (-math.sin(a), math.cos(a))            # unit vector from table centre towards this seat
        sx, sy = cx + down[0] * 222, cy + down[1] * 222
        chx, chy = cx + down[0] * 396, cy + down[1] * 396
        out.append(img("chair-top.svg", chx, chy, 170, 153, rot, 0.4))
        out.append(img("place-setting.svg", sx, sy, 170, 108, rot))
        bx = sx + (-24.7) * math.cos(a) - (-1.5) * math.sin(a)
        by = sy + (-24.7) * math.sin(a) + (-1.5) * math.cos(a)
        bowl = "rice-bowl-unknown.svg" if cls == "unk" else "rice-bowl.svg"
        out.append(img(bowl, bx, by, 84, 84, 0, 0.7, "unkring" if cls == "unk" else ""))
        nums = '<u class="unkt">未知</u>' if cls == "unk" else f"熱量 {kcal}・蛋白質 {prot}"
        out.append(f'<div class="ov pk seat" style="--k:.7;left:{label[0] / 16:.2f}%;top:{label[1] / 10:.1f}%">'
                   f'<b>{name}</b><em class="{cls}">{badge}</em><span>{nums}</span></div>')
    dishes = [("dish-staple.svg", "五穀飯", 140, 140, -135), ("dish-seafood.svg", "清蒸鱸魚", 176, 135, -45),
              ("dish-vegetable.svg", "蒜炒花椰菜", 140, 140, 135), ("dish-soup.svg", "蘿蔔排骨湯", 166, 138, 45)]
    for name, label, w, h, ang in dishes:
        a = math.radians(ang)
        x, y = cx + math.cos(a) * 100, cy + math.sin(a) * 100
        out.append(img(name, x, y, w, h))
        out.append(f'<div class="ov pk dlabel" style="--k:.7;left:{x / 16:.2f}%;top:{(y + h / 2 - 16) / 10:.1f}%">{label}</div>')
    receipt = data_uri(TABLEWARE / "receipt.svg")
    tray = data_uri(TABLEWARE / "tip-tray.svg")
    tray_empty = data_uri(TABLEWARE / "tip-tray-empty.svg")
    out.append(f"""
<div class="ov pk rcpt" style="--k:.7;--r:14deg;left:58.9%;top:57.6%;width:7%;background-image:url({receipt})">
  <b>超市收據</b><p><span>要買的整包</span><i>$486</i></p><p class="sub"><span>這餐吃掉</span><i>$312</i></p>
  <small>鱸魚 價格 <u class="unkt">未知</u></small><em>預算內</em></div>
<div class="ov pk tent" style="--k:.7;left:40.4%;top:27.6%"><b>家庭合計</b>熱量 101%・蛋白質 99%</div>
<img class="ov pk rot know-has" style="--k:.7;--r:-12deg;left:39.4%;top:60.4%;width:7.4%" alt="" src="{tray}">
<img class="ov pk rot know-empty" style="--k:.7;--r:-12deg;left:39.4%;top:60.4%;width:7.4%" alt="" src="{tray_empty}">
<div class="ov pk dlabel know-has" style="--k:.7;left:39.4%;top:71%">營養小知識 3 則</div>
<div class="ov pk dlabel know-none know-down" style="--k:.7;left:39.4%;top:71%">小知識暫時拿不到</div>
<div class="ov pk dlabel know-none know-nf" style="--k:.7;left:39.4%;top:71%">這桌沒有小知識</div>
<div class="ov pk dlabel know-none know-retry" style="--k:.7;left:39.4%;top:71%">小知識重試中…</div>
<div class="ov side l"><h4>個人營養（點飯碗展開）</h4>
  <table><tr><th>成員</th><th>熱量</th><th>蛋白質</th><th></th></tr>
  <tr><td>爸爸</td><td>102%</td><td>95%</td><td><em class="ok">通過</em></td></tr>
  <tr><td>媽媽</td><td>108%</td><td>112%</td><td><em class="ok">通過</em></td></tr>
  <tr><td>小米</td><td>116%</td><td>98%</td><td><em class="warn">熱量超出</em></td></tr>
  <tr><td>阿嬤</td><td><u class="unkt">未知</u></td><td><u class="unkt">未知</u></td><td><em class="unk">計算不完整</em></td></tr></table>
  <p>熱量 ≤110%、蛋白質 ≥90% 才算通過。缺資料時不畫飯，也不當成 0。</p></div>
<div class="ov side r"><h4>收據明細（點收據展開）</h4>
  <table><tr><th>食材</th><th>要買</th><th>這餐用</th></tr>
  <tr><td>五穀米 1 kg</td><td>$120</td><td>$36</td></tr>
  <tr><td>青花菜 2 朵</td><td>$98</td><td>$98</td></tr>
  <tr><td>排骨 600 g</td><td>$198</td><td>$132</td></tr>
  <tr><td>白蘿蔔 1 條</td><td>$70</td><td>$46</td></tr>
  <tr><td>鱸魚 1 條</td><td><u class="unkt">未知</u></td><td><u class="unkt">未知</u></td></tr>
  <tr class="sum"><td>合計</td><td>$486</td><td>$312</td></tr></table>
  <p>「要買的整包」和「這餐吃掉」是兩個數字，不相加。預算 $600：<em class="ok">預算內</em>（有 1 項價格未知）</p></div>
<div class="ov side k"><h4>營養小知識（點小卡盤展開）</h4>
  <div class="know-has">
    <div class="kcard"><b>番茄</b><p>番茄加熱後茄紅素更容易被吸收，搭配少量油脂一起煮效果更好。</p>
      <small>來源：健康醫療網・營養飲食　<i>原文</i></small></div>
    <div class="kcard dim"><b>青花菜</b><p>青花菜富含維生素 C，汆燙時間短一點能保留較多。</p><small>來源：健康醫療網・營養飲食　<i>原文</i></small></div>
    <div class="kpager"><span>‹</span><span>1 / 3</span><span>›</span></div>
    <p>每張卡是這桌其中一樣食材的小知識，內容與來源都來自後端，來源固定顯示在卡片上。</p></div>
  <div class="know-none know-down"><p class="kmsg">營養知識服務暫時無法使用。<br>菜單、份量和能不能採用都不受影響。</p>
    <span class="kbtn">只重試小知識</span><p>重試不會重新生成菜單，結果會存成新版本。額度不足時這裡會改寫「本月外部服務額度不足」。</p></div>
  <div class="know-none know-nf"><p class="kmsg">這桌的食材目前查不到對應的小知識。</p><p>不顯示重試按鈕，也不用其他內容充數。</p></div>
  <div class="know-none know-retry"><p class="kmsg">正在重新產生小知識…</p><span class="kbtn off">重試中…</span><p>完成後自動換成新版本；失敗會顯示原因。</p></div>
</div>
<div class="ov actbar-d"><span class="pri">✓ 採用這桌</span><span>換一桌</span><span>重試查價</span></div>""")
    return "".join(out)


def fridge_overlay() -> str:
    days = ""
    marked = {2, 3, 5, 7, 8, 10, 11, 12, 13, 14}
    for i in range(6):
        days += "<i></i>"
    for dnum in range(1, 31):
        cls = ("has " if dnum in marked else "") + ("sel" if dnum == 14 else "")
        days += f'<i class="{cls.strip()}">{dnum}</i>'
    bars = "".join(f'<i style="height:{h}%"{" class=now" if k == 3 else ""}><b>{v}</b></i>'
                   for k, (h, v) in enumerate([(58, "$1,320"), (74, "$1,690"), (66, "$1,510"), (81, "$1,840")]))
    return f"""
<div class="ov pk magtab on" style="--k:.4;left:31.25%;top:7%">已採用</div>
<div class="ov pk magtab" style="--k:.4;left:68.75%;top:7%">營養與採購</div>
<div class="ov pk fnote w" style="--k:.4;--r:-1.2deg;left:31.25%;top:15%;width:27%"><i class="mag"></i>
  <b>2026 年 11 月</b><div class="fcal"><u>日</u><u>一</u><u>二</u><u>三</u><u>四</u><u>五</u><u>六</u>{days}</div>
  <small>有餐盤貼紙的日子＝那天有採用的餐單</small></div>
<div class="ov pk fnote y" style="--k:.4;--r:2deg;left:31.25%;top:63%;width:22%"><i class="mag g"></i>
  <b>11/14 晚餐・已採用</b><p>五穀飯<br>清蒸鱸魚<br>蒜炒花椰菜<br>蘿蔔排骨湯</p>
  <div class="fbtn"><span>看這桌（唯讀）</span><span class="pri">一鍵購入</span></div></div>
<div class="ov pk fnote w" style="--k:.4;--r:1.4deg;left:68.75%;top:15%;width:27%"><i class="mag g"></i>
  <b>每週採購金額</b><div class="fbars">{bars}</div><small>10/24　　10/31　　11/07　　本週</small></div>
<div class="ov pk fnote g" style="--k:.4;--r:-1.6deg;left:68.75%;top:57%;width:27%"><i class="mag"></i>
  <b>生成紀錄</b>
  <p class="frow"><span>11/15 午餐・第 2 版</span><em class="hot">待決定</em></p>
  <p class="frow"><span>11/14 晚餐・第 1 版</span><em class="ok">已採用</em></p>
  <p class="frow"><span>11/13 晚餐・第 1 版</span><em class="hot">待決定</em></p>
  <p class="frow"><span>11/12 晚餐・第 3 版</span><em class="unk">計算不完整</em></p>
  <small>點任一列 → 回餐桌看那一版（已採用的是唯讀）</small></div>
<div class="ov pk tag" style="--k:.4;left:16.4%;top:80%">把手 · 拉開拍冰箱</div>"""


FAMILY = ROOT / "src" / "assets" / "family"


def portrait_overlay() -> str:
    people = [("crayon-man.svg", "爸爸", "45 歲・中度活動", 590, 104), ("crayon-woman.svg", "媽媽", "43 歲・輕度活動", 706, 100),
              ("crayon-kid.svg", "小米", "9 歲・中度活動", 812, 78), ("crayon-elder.svg", "阿嬤", '78 歲・<u class="pencil">活動量未填</u>', 916, 100),
              ("crayon-add.svg", "畫上新成員", "", 1024, 84)]
    out = []
    for name, label, sub, x, w in people:
        h = w * 200 / 120
        top = 606 - h
        cls = " addp" if "add" in name else ""
        out.append(f'<img class="ov pk{cls}" style="--k:.4;left:{x / 16:.2f}%;top:{top / 10:.1f}%;width:{w / 16:.2f}%" alt="" src="{data_uri(FAMILY / name)}">')
        out.append(f'<div class="ov pk cname{cls}" style="--k:.4;left:{x / 16:.2f}%;top:61.4%"><b>{label}</b><span>{sub}</span></div>')
    out.append("""
<div class="ov pk plaque" style="--k:.4;left:50%;top:74.5%">林家廚房</div>
<div class="ov side l"><h4>成員（點畫裡的人展開）</h4>
  <p class="who"><b>爸爸</b>45 歲・男・中度活動</p>
  <table><tr><th>每日目標</th><th></th></tr>
  <tr><td>熱量</td><td>2,100 kcal</td></tr><tr><td>蛋白質</td><td>70 g</td></tr>
  <tr><td>不吃的食材</td><td>香菜</td></tr></table>
  <div class="fbtn"><span>編輯</span><span>擦掉（刪除）</span></div>
  <p>人物會從畫裡走出來放大；編輯用一張蠟筆便條。沒填的欄位用鉛筆底線標出來，不替使用者猜。</p></div>
<div class="ov side r"><h4>設定（畫框下的名牌）</h4>
  <p class="who"><b>景深效果</b></p><div class="seg3"><span class="on">沉浸</span><span>靜態景深</span><span>關閉</span></div>
  <table><tr><td>目前的家</td><td>林家廚房 ▾</td></tr>
  <tr><td>辨識服務</td><td><em class="ok">正常</em></td></tr><tr><td>知識服務</td><td><em class="ok">正常</em></td></tr></table>
  <div class="fbtn"><span>換一個家</span><span>登出</span></div></div>""")
    return "".join(out)


# Per-room review metadata: title, notes, phone focus (fraction of the 16:10 world width
# that sits at the centre of a portrait viewport) and the HTML overlay drawn on top.
META = {
    "door": {
        "title": "大門（登入）",
        "route": "/login",
        "focus": 0.5,
        "overlay": door_overlay,
        "notes": [
            "畫風：寫實（材質紋理、方向性光源、投影、玻璃反光、暗角）。手寫 SVG 的半寫實程度，不是照片或 3D 算圖",
            "z0 黃昏漸層天空、逆光雲、偏紫模糊的遠景（大氣透視）、樹籬",
            "z1 橫向木板外牆、白色封簷板、磚煙囪、石砌牆基、有褶窗簾與玻璃反光的側窗、木紋紅門與凹板、黃銅門把、壁燈與光暈、混凝土門階、草地與石板路",
            "z2 椰纖踏墊、陶盆番茄、鍍鋅澆水壺、虎尾蘭、鐵信箱、橘貓，各自有落地陰影",
            "z3 失焦枝葉、散景光斑、暗角",
            "吉祥物維持插畫風（平塗、無材質），在畫面底部來回跑；點「聊天室」標籤看懸浮視窗打開時的樣子（吉祥物會停在視窗旁）",
        ],
    },
    "genkan": {
        "title": "玄關（選擇家庭）",
        "route": "/households",
        "focus": 0.375,
        "stops": [("門牌", 0.375), ("內門", 0.78)],
        "dock": [("林家廚房", "上次進這家", 0), ("外婆家", "", 0), ("＋ 掛新門牌", "", 0)],
        "overlay": genkan_overlay,
        "notes": [
            "光源：吊燈為主光，檯燈與客廳透進來的光為輔；兩側與四角壓暗",
            "z0 粉刷牆與光暈、牆上小畫、白色腰牆板、架高木地板、段差、落塵區石磚、通往客廳的內門（門洞裡是失焦的客廳：黃昏的窗、綠沙發、立燈）、吊燈",
            "z1 掛門牌的胡桃木軌與四個銅鉤、百葉門鞋櫃、傘桶與兩把傘、內門上的番茄紋暖簾",
            "z2 檯面：黃金葛、鑰匙盤（含番茄鑰匙圈）、信件、檯燈；落塵區：帆布鞋、皮鞋、小雨靴；架高地板：室內拖鞋",
            "z3 前景：剛推開的大門內側（失焦）、腳邊踏墊、暗角",
            "門牌是獨立元件（door-plate.svg）：小屋形楓木牌，一張＝一個家庭，名稱與成員數由 HTML 疊上；虛線輪廓＝建立新家庭；第四個鉤子留空給之後的家庭",
            "手機兩個停靠點：門牌（預設）／內門；選好家庭後鏡頭自動向右平移到內門再推進客廳",
        ],
    },
    "living": {
        "title": "客廳（今日）",
        "route": "/today",
        "focus": 0.5,
        "app": True,
        "stops": [("沙發與窗", 0.2), ("電視牆", 0.5), ("廚房門口", 0.824)],
        "dock": [("全家福", "家庭", 1), ("電視", "切換餐別", 1), ("廚房門口", "規劃", 2)],
        "overlay": living_overlay,
        "notes": [
            "光源：天花板主光打在電視牆、立燈照沙發、廚房門口灑出暖光、窗外是黃昏的冷光",
            "z0 牆、黃昏的窗與亞麻窗簾、橡木框的蠟筆全家福、通往廚房的門口（磁磚牆、鼠尾草綠櫥櫃、抽油煙機、流理台上的瓦斯爐）、木地板",
            "z1 立燈、綠色布沙發（芥末黃與米白抱枕、披在扶手上的番茄紅毯子）、胡桃木電視櫃與電視、小花瓶、編籃裡的琴葉榕",
            "z2 編織地毯、胡桃木橢圓茶几、白瓷大盤、筷子與筷架、冒著熱氣的茶、遙控器",
            "z3 前景：失焦的龜背芋葉、扶手椅一角、暗角",
            "構圖調整：電視放在正中、全家福掛在電視上方、茶几在正前方，這樣手機直式一屏就能看到三個重點；手機三個停靠點：沙發與窗／電視牆（預設）／廚房門口，物件列列出三個可點物件",
            "介面疊層：門口瓦斯爐上的鍋（pot-side.svg，會依鍋狀態機換圖）、電視螢幕內容、餐盤上的菜（示意佔位）與三顆徽章、三個可點物件的標籤",
            "茶几與餐盤只是擺飾、不可點；全家福畫框裡的蠟筆畫是刻意保留的非寫實畫法",
        ],
    },
    "kitchen": {
        "title": "廚房（規劃）",
        "route": "/plan",
        "focus": 0.52,
        "app": True,
        "stops": [("出菜口與食譜書", 0.2), ("爐台", 0.52), ("冰箱", 0.824)],
        "dock": [("單軌", "3 張單", 0), ("食譜書", "食譜庫", 0), ("左鍋", "生成中", 1), ("空鍋", "新的一餐", 1), ("便利貼", "紀錄", 2)],
        "overlay": kitchen_overlay,
        "notes": [
            "光源：抽油煙機下的燈照爐台、天花板主光、出菜口透進餐廳的暖光",
            "z0 亮面磁磚牆、左上的出菜口（裡面是失焦的餐廳：吊燈、餐桌）、出菜口下的鋁製單軌、抽油煙機、掛桿上的湯勺鍋鏟打蛋器濾網、香料架、石磚地",
            "z1 石紋流理台、鼠尾草綠下櫃（與客廳門口看到的一致）、奶油色復古冰箱",
            "z2 雙口瓦斯爐、闔上的綠色精裝食譜書（書口四色分類標籤、紅緞帶）、砧板上的青江菜香菇番茄與菜刀、廚具筒、橄欖油與胡椒罐",
            "z3 前景：失焦的吊燈、中島邊緣與一碗番茄、暗角",
            "介面疊層：單軌上的三張單（ticket.svg）、左爐口蓋著冒蒸氣的鍋（生成中）、右爐口的空鍋（新的一餐）、冰箱上的便利貼、五個可點物件的標籤",
            "鍋的側視狀態已畫五張：空鍋、有料、蓋鍋、開蓋發亮、失敗（鍋蓋半開）。俯視圖會跟「單板＋鍋」那一景一起畫",
            "手機三個停靠點：出菜口與食譜書／爐台（預設）／冰箱；下方物件列列出五個可點物件，點了鏡頭自己轉過去",
        ],
    },
    "recipe-book": {
        "title": "翻開的食譜書（食譜庫）",
        "route": "/pick",
        "focus": 0.3125,
        "app": True,
        "phone_ty": "-7%",
        "stops": [("清單", 0.3125), ("細節與托盤", 0.6875)],
        "dock": [("清單", "第 3 頁", 0), ("細節", "咖哩雞", 1), ("托盤", "2 道", 1), ("闔上書", "回廚房", 0)],
        "overlay": book_overlay,
        "notes": [
            "視角：從正上方俯視流理台上攤開的書（和「單板＋鍋」是同一張檯面）",
            "z0 石紋流理台；z1 綠色布面精裝書攤開、左右書頁與書溝陰影、翻頁角、右緣六個分類書籤、垂下的紅緞帶；z2 壓在右頁下緣的胡桃木托盤、鉛筆、羅勒、一杯茶；z3 暗角",
            "介面疊層：左頁條列清單一頁 10 道（已選的整列反綠）、右頁是點到那道的細節、書籤上的分類文字、托盤裡已選的食譜與「送去出菜口」",
            "手機一次一頁：清單／細節與托盤兩個停靠點；書籤在手機上看不到，所以清單頁首多一排分類按鈕",
        ],
    },
    "dining": {
        "title": "餐桌（餐單結果）",
        "route": "/plan/:id/result/:ver",
        "focus": 0.5,
        "phone_ty": "-7%",
        "stops": [("餐桌", 0.5)],
        "dock": [("五穀飯", "", 0), ("清蒸鱸魚", "", 0), ("蒜炒花椰菜", "", 0), ("蘿蔔排骨湯", "", 0), ("爸爸", "通過", 0), ("媽媽", "通過", 0),
                 ("小米", "熱量超出", 0), ("阿嬤", "未知", 0), ("收據", "$486", 0), ("小知識", "3 則", 0)],
        "knowctl": True,
        "actions": [("✓ 採用這桌", True), ("換一桌", False)],
        "overlay": dining_overlay,
        "notes": [
            "視角：從正上方俯視餐桌（和食譜書、單板同一套俯視畫法）。圓桌放在畫面正中，手機一屏就看得到整桌",
            "z0 木地板、圓形編織地毯、角落盆栽、吊燈的光；z1 胡桃木圓桌與中央的轉盤；z2 轉盤正中的小花瓶；z3 暖光與暗角",
            "依資料擺放的元件（src/assets/tableware/）：菜依類別用不同器皿——主食＝木飯桶、海鮮＝橢圓盤、蔬菜＝圓盤、肉類＝圓盤、湯＝雙耳湯鍋、其他＝小碟；有幾道菜就擺幾個在轉盤上",
            "每位成員一張椅子、一份餐墊（筷子、湯匙）和一個飯碗；飯碗＝個人營養判定。阿嬤資料不足，所以是空碗加虛線，不畫飯",
            "收據壓在桌邊：「要買的整包」與「這餐吃掉」兩行分開；價格未知的食材列在上面；底下蓋預算章",
            "桌機：左右兩側的空間用來放展開的面板（個人營養、收據明細），不遮住餐桌。手機：這兩個面板改成從下方滑出的抽屜（審圖頁沒做）",
            "營養小知識：桌上的胡桃木小卡盤放著一疊知識卡，每張＝這桌一樣食材的小知識，來源固定印在卡上。用上方「小知識狀態」切換四種情況：有小知識／暫時拿不到（可只重試小知識）／查無資料／重試中",
            "手機：單一停靠點；物件列列出四道菜、四位成員、收據、小知識；最底下是「採用這桌／換一桌」動作列，這一景不顯示底部分頁",
        ],
    },
    "fridge": {
        "title": "冰箱門（紀錄）",
        "route": "/records",
        "focus": 0.3125,
        "app": True,
        "stops": [("已採用", 0.3125), ("營養與採購", 0.6875)],
        "dock": [("月曆", "11 月", 0), ("11/14 晚餐", "已採用", 0), ("一鍵購入", "", 0), ("拉開冰箱", "拍照", 0), ("採購金額", "本週 $1,840", 1), ("生成紀錄", "4 筆", 1)],
        "overlay": fridge_overlay,
        "notes": [
            "視角：正面近看冰箱門。門面刻意留白，左半是「已採用」、右半是「營養與採購」；桌機兩半同時看得到，手機一半就是一個停靠點",
            "z0 冰箱兩側露出的磁磚牆；z1 奶油色冰箱門、上方冷凍門的下緣、鍍鉻把手、門面反光；z2 固定的裝飾磁鐵（番茄、小魚、字母、夾子）；z3 暗角",
            "介面疊層：兩顆磁鐵分頁、月曆（有餐盤貼紙的日子＝有採用的餐單）、當日餐單便利貼（可看唯讀的那一桌、一鍵購入）、每週採購金額、生成紀錄",
            "把手是可點物件：拉開冰箱＝捷徑到問卷第 3 步的拍照辨識",
        ],
    },
    "portrait": {
        "title": "全家福（家庭）",
        "route": "/family",
        "focus": 0.5,
        "app": True,
        "stops": [("全家福", 0.5)],
        "dock": [("爸爸", "", 0), ("媽媽", "", 0), ("小米", "", 0), ("阿嬤", "有未填", 0), ("＋ 新成員", "", 0), ("設定", "", 0)],
        "overlay": portrait_overlay,
        "notes": [
            "視角：回頭近看客廳牆上的畫。畫框裡的圖畫紙剛好是手機一屏的寬度",
            "z0 牆與畫燈的光；z1 黃銅畫燈、橡木畫框、襯紙、圖畫紙和蠟筆畫的背景（天空、太陽、雲、小房子、草地、花）、畫框下的黃銅名牌；z2 矮櫃檯面上的盆栽、一盒蠟筆、相框與書；z3 失焦的窗簾邊與暗角",
            "畫裡的人物＝家庭成員（src/assets/family/，男、女、小孩、長輩四種蠟筆人物），依資料排在草地上；虛線人形＝畫上新成員",
            "場景是寫實的，畫裡面是蠟筆畫——這是除了吉祥物以外唯一不寫實的地方",
            "桌機：左側是點到的成員資料，右側是設定（景深效果、換家、服務狀態、登出）。手機：這兩個面板改成下方抽屜（審圖頁沒做），物件列列出每位成員、新成員、設定",
        ],
    },
    "passboard": {
        "title": "單板＋鍋（問卷與生成）",
        "route": "/plan/:id",
        "focus": 0.681,
        "sheet": True,
        "phone_ty": "-11%",
        "potctl": True,
        "overlay": passboard_overlay,
        "notes": [
            "視角：俯視流理台。把「單板」畫成夾著點菜單的木製寫字板，從單軌取下的那張單就夾在上面；右邊是瓦斯爐的左爐口和鍋的俯視",
            "z0 石紋流理台；z1 木製單板與金屬夾、點菜單（紙）、鉛筆、瓦斯爐（鑄鐵爐架、旋鈕）；z2 爐前一排備料碟（菜、菇、肉、配菜）與高湯壺；z3 爐燈的光與暗角",
            "鍋的俯視九張：空鍋、步驟 1–5（菜→菇→肉→湯→配菜補滿並亮起）、蓋鍋、完成、失敗。用上方「鍋的狀態」按鈕切換看",
            "介面疊層：紙上的問卷（示意停在第 3 步「現有食材辨識」：相機框＋逐項確認的食材）",
            "手機：鍋在上半、問卷是下半的紙張抽屜，一步一屏；這一景沒有物件列，吉祥物也先收起來",
        ],
    },
}


def hud() -> str:
    mascot = MASCOT.read_text(encoding="utf-8")
    return f"""
<div class="hud">
  <div class="chat" role="dialog" aria-label="飲食小幫手">
    <header><span class="dot">🍅</span><b>飲食小幫手</b><span class="sub">只根據檢索到的文章回答</span><i>✕</i></header>
    <div class="msgs">
      <p class="me">晚餐想多吃點蔬菜，有什麼建議？</p>
      <p class="bot">可以把一半的盤子留給深綠色蔬菜，少油快炒或汆燙能保留較多水溶性維生素。
        <span class="src">📄 蔬菜怎麼煮最營養？<em>健康醫療網・營養飲食</em></span></p>
    </div>
    <footer><span class="inp">問問飲食問題…</span><span class="send">送出</span></footer>
  </div>
  <div class="runner"><div class="flip">{mascot}</div></div>
</div>"""


def phone_ui(meta: dict) -> tuple[str, str]:
    """Return (attributes for the phone viewport, extra HUD html): stop dots, edge hints, hotspot dock, tab bar."""
    if meta.get("sheet"):
        return "", f'<div class="pui"><div class="sheet"><i class="grip"></i>{FORM}</div></div>'
    stops = meta.get("stops")
    if not stops:
        return "", ""
    cur = min(range(len(stops)), key=lambda i: abs(stops[i][1] - meta["focus"]))
    data = json.dumps([[n, f] for n, f in stops], ensure_ascii=False).replace('"', "&quot;")
    dots = "".join("<i></i>" for _ in stops)
    chips = ""
    for name, sub, stop in meta.get("dock", []):
        small = f"<small>{sub}</small>" if sub else ""
        chips += f'<button type="button" data-stop="{stop}">{name}{small}</button>'
    tabs = ""
    if meta.get("app"):
        on = {"/today": 0, "/plan": 1, "/pick": 2, "/records": 3, "/family": 4}.get(meta["route"], 0)
        names = ["今日", "規劃", "食譜庫", "紀錄", "家庭"]
        spans = "".join(f'<span class="{"on" if i == on else ""}">{n}</span>' for i, n in enumerate(names))
        tabs = f'<div class="tabsbar">{spans}</div>'
    if meta.get("actions"):
        spans = "".join(f'<span class="{"pri" if pri else ""}">{label}</span>' for label, pri in meta["actions"])
        tabs = f'<div class="actbar">{spans}</div>'
    html = f"""
<div class="pui">
  <div class="dots">{dots}</div>
  <button type="button" class="edge l" data-dir="-1"></button><button type="button" class="edge r" data-dir="1"></button>
  <div class="dock">{chips}</div>
  {tabs}
</div>"""
    return f' data-stops="{data}" data-cur="{cur}"', html


def room_section(room: str) -> str:
    meta = META[room]
    layers = sorted(p for p in (ROOMS / room).glob("z[0-4].svg"))
    toggles, imgs = [], []
    for p in layers:
        z = p.stem
        toggles.append(
            f'<input type="checkbox" id="{room}-{z}" class="tg tg-{z}" checked>'
            f'<label for="{room}-{z}">{LAYER_NAMES[z]}</label>'
        )
        imgs.append(f'<img class="lyr l-{z}" style="--k:{PARALLAX[z]}" alt="" src="{data_uri(p)}">')
    toggles.append(f'<input type="checkbox" id="{room}-ui" class="tg tg-ui" checked><label for="{room}-ui">介面疊層</label>')
    toggles.append(f'<input type="checkbox" id="{room}-mascot" class="tg tg-mascot" checked><label for="{room}-mascot">吉祥物</label>')
    toggles.append(f'<input type="checkbox" id="{room}-chat" class="tg tg-chat"><label for="{room}-chat">聊天室</label>')
    world = "".join(imgs) + meta["overlay"]()
    notes = "".join(f"<li>{n}</li>" for n in meta["notes"])
    focus = f"{meta['focus'] * 100:.0f}%"
    pattr, pui = phone_ui(meta)
    pcls = (" has-dock" + (" app" if meta.get("app") or meta.get("actions") else "")) if pui else ""
    pcap = "手機 9:16：左右滑動或點箭頭換停靠點；下方物件列列出這個房間所有可點的東西" if pui else f"手機 9:16（同一張圖，鏡頭對準 {focus}）"
    pty = f";--ty:{meta['phone_ty']}" if meta.get("phone_ty") else ""
    if meta.get("sheet"):
        pcls, pcap = " sheeted", "手機 9:16：鍋在上半，問卷是下半的紙張抽屜，一步一屏"
    potctl = ""
    if meta.get("potctl"):
        btns = "".join(f'<button type="button" data-pot="{k}"{" class=on" if k == "3" else ""}>{label}</button>' for k, label in POT_STATES)
        potctl = f'<div class="potctl"><span>鍋的狀態</span>{btns}</div>'
        room_attr = ' data-pot="3"'
    else:
        room_attr = ""
    if meta.get("knowctl"):
        states = [("has", "有小知識"), ("down", "暫時拿不到"), ("nf", "查無資料"), ("retry", "重試中")]
        btns = "".join(f'<button type="button" data-know="{k}"{" class=on" if k == "has" else ""}>{label}</button>' for k, label in states)
        potctl += f'<div class="potctl knowctl"><span>小知識狀態</span>{btns}</div>'
        room_attr += ' data-know="has"'
    return f"""
<section class="room"{room_attr}>
  <h2>{meta['title']} <code>{meta['route']}</code></h2>
  {''.join(toggles)}
  {potctl}
  <div class="views">
    <figure><div class="vp desk"><div class="world" style="--fx:50%">{world}</div>{hud()}</div><figcaption>桌機 16:10（完整構圖）</figcaption></figure>
    <figure><div class="vp phone{pcls}"{pattr}><div class="world" style="--fx:{focus}{pty}">{world}</div>{hud()}{pui}</div><figcaption>{pcap}</figcaption></figure>
  </div>
  <ul class="notes">{notes}</ul>
</section>"""


CSS = """
:root{--ink:#3A2A20;--ink2:#6E5A4C;--tomato:#D9472B}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Noto Sans TC',system-ui,sans-serif;background:#EADBC4;color:var(--ink);padding:24px 16px 60px}
h1{font-size:24px;margin-bottom:6px}
.lead{color:var(--ink2);font-size:14px;line-height:1.7;max-width:900px;margin-bottom:22px}
.room{background:#fff;border-radius:22px;padding:20px;max-width:1280px;margin:0 auto 28px;box-shadow:0 8px 0 #F3E0C3,0 22px 34px rgba(90,50,20,.16)}
.room h2{font-size:19px;margin-bottom:12px}
.room h2 code{font-size:13px;color:var(--tomato);margin-left:6px}
.tg{position:absolute;opacity:0}
.tg+label{display:inline-block;margin:0 8px 12px 0;padding:6px 12px;border-radius:999px;font-size:13px;font-weight:600;background:#FBEFDD;color:var(--ink2);cursor:pointer;user-select:none}
.tg:checked+label{background:#FCE3DA;color:var(--tomato)}
.tg:focus-visible+label{outline:3px solid var(--tomato);outline-offset:2px}
.views{display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap}
figure{margin:0}
figure:first-child{flex:1 1 640px;min-width:0}
figcaption{font-size:12px;color:var(--ink2);margin-top:8px;text-align:center}

/* viewport and world */
.vp{position:relative;overflow:hidden;border-radius:16px;background:#2A2238;--px:0;--py:0;container-type:inline-size}
.vp.desk{aspect-ratio:16/10;width:100%;--mw:6.5cqw;--cw:27cqw;--fs:1.02cqw}
.vp.phone{aspect-ratio:9/16;height:min(560px,80vh);border-radius:28px;box-shadow:0 0 0 8px #1E1820;--mw:17cqw;--cw:90cqw;--fs:3.3cqw}
.world{position:absolute;top:0;height:100%;aspect-ratio:16/10;left:50%;transform:translate(calc(var(--fx) * -1),var(--ty,0));container-type:inline-size;transition:transform .5s cubic-bezier(.2,.8,.2,1)}
.vp.phone.has-dock{touch-action:pan-y;cursor:grab}

/* phone: stop dots, edge hints, hotspot dock, tab bar */
.pui{position:absolute;inset:0;pointer-events:none;color:var(--ink)}
.dots{position:absolute;top:3.4cqw;left:50%;transform:translateX(-50%);display:flex;gap:1.4cqw;padding:1.2cqw 1.8cqw;border-radius:99em;background:rgba(30,20,35,.4)}
.dots i{width:1.8cqw;height:1.8cqw;border-radius:99em;background:rgba(255,255,255,.5);transition:all .3s}
.dots i.on{width:5.4cqw;background:#fff}
.edge{position:absolute;top:42%;pointer-events:auto;border:0;font:inherit;font-size:2.7cqw;font-weight:700;line-height:1.3;color:#fff;background:rgba(30,20,35,.6);padding:1.8cqw 2cqw;max-width:24cqw;cursor:pointer}
.edge.l{left:0;border-radius:0 2.4cqw 2.4cqw 0;text-align:left}.edge.r{right:0;border-radius:2.4cqw 0 0 2.4cqw;text-align:right}
.edge:empty{display:none}
.dock{position:absolute;left:0;right:0;bottom:3cqw;display:flex;gap:1.6cqw;padding:1cqw 3cqw;overflow-x:auto;pointer-events:auto;scrollbar-width:none}
.dock::-webkit-scrollbar{display:none}
.vp.app .dock{bottom:18cqw}
.dock button{flex:none;font:inherit;font-size:3cqw;font-weight:700;border:0;border-radius:99em;padding:1.6cqw 2.8cqw;background:rgba(255,253,247,.95);color:var(--ink);box-shadow:0 1cqw 2.4cqw rgba(15,8,25,.4);cursor:pointer;opacity:.82}
.dock button small{font-weight:600;font-size:.86em;color:var(--ink2);margin-left:.5em}
.dock button.cur{opacity:1;box-shadow:0 0 0 .6cqw #E8603F,0 1cqw 2.4cqw rgba(15,8,25,.4)}
.dock button.cur small{color:var(--tomato)}
.tabsbar{position:absolute;left:3cqw;right:3cqw;bottom:2.6cqw;height:13.5cqw;border-radius:5cqw;background:#FFFDF7;display:flex;justify-content:space-around;align-items:center;box-shadow:0 1.6cqw 4cqw rgba(15,8,25,.45)}
.tabsbar span{font-size:2.9cqw;font-weight:700;color:var(--ink2)}.tabsbar span.on{color:var(--tomato)}
.vp.has-dock .runner{bottom:13cqw}.vp.app .runner{bottom:28cqw}
.vp.has-dock .chat{bottom:calc(13cqw + var(--mw) * 1.25)}.vp.app .chat{bottom:calc(28cqw + var(--mw) * 1.25)}
.tg-ui:not(:checked)~.views .pui{visibility:hidden}
.lyr{position:absolute;inset:-3%;width:106%;height:106%;object-fit:cover;transition:transform .25s ease-out;
  transform:translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw))}
.tg-z0:not(:checked)~.views .l-z0,.tg-z1:not(:checked)~.views .l-z1,.tg-z2:not(:checked)~.views .l-z2,
.tg-z3:not(:checked)~.views .l-z3,.tg-z4:not(:checked)~.views .l-z4,.tg-ui:not(:checked)~.views .ov,
.tg-mascot:not(:checked)~.views .runner{visibility:hidden}

/* login card overlay (realistic) */
.ov{position:absolute;transform:translateX(-50%);transition:transform .25s ease-out}
.card{aspect-ratio:320/440;background-size:100% 100%;font-size:1cqw;
  transform:translateX(-50%) translate(calc(var(--px) * -.9cqw),calc(var(--py) * -.6cqw)) rotate(calc(var(--px) * 1.5deg));transform-origin:50% 4%}
.card-in{position:absolute;left:12%;right:12%;top:25%;bottom:6%;display:flex;flex-direction:column;gap:.55em;color:var(--ink)}
.card-in .brand{font-size:1.25em;color:#8C2413;text-align:center;margin-bottom:.2em}
.card-in label{font-size:.7em;font-weight:600;color:var(--ink2);display:flex;flex-direction:column;gap:.3em}
.card-in .inp{display:block;font-size:1.2em;font-weight:400;color:var(--ink);background:#FBF7EE;border:.12em solid #CDBFA3;border-radius:.3em;padding:.45em .7em}
.card-in .btn{margin-top:.3em;text-align:center;font-weight:700;color:#fff;background:#A93019;border-radius:.35em;padding:.6em;box-shadow:0 .2em .5em rgba(40,10,5,.45)}
.card-in small{font-size:.66em;text-align:center;color:var(--ink2)}
.card-in small i{font-style:normal;font-weight:700;color:#8C2413}

/* genkan: household plates hanging from the rail (moves with z1) */
.plate,.wall-title{transform:translateX(-50%) translate(calc(var(--px) * -.48cqw),calc(var(--py) * -.32cqw))}
.plate{aspect-ratio:160/220;background-size:100% 100%;font-size:.86cqw;transform-origin:50% 4%}
.plate svg{position:absolute;inset:0;width:100%;height:100%}
.plate-in{position:absolute;left:10%;right:10%;top:50%;bottom:8%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.25em;text-align:center}
.plate-in b{font-size:1.1em;color:#4A321E;text-shadow:0 .06em 0 rgba(255,246,224,.7)}
.plate-in span{font-size:.78em;color:#6B4A2C}
.plate-in em{font-style:normal;font-size:.66em;font-weight:700;color:#FFF6E0;background:#A93019;border-radius:.3em;padding:.15em .5em;box-shadow:0 .1em .3em rgba(40,10,5,.4)}
.plate.empty .plate-in b{font-size:1.8em;color:#FFF6E0;text-shadow:0 .05em .2em rgba(20,10,20,.6)}
.plate.empty .plate-in span{color:#FFF6E0;text-shadow:0 .05em .2em rgba(20,10,20,.7)}
.wall-title{font-size:1.5cqw;font-weight:700;letter-spacing:.12em;color:#5A4632;white-space:nowrap;text-shadow:0 .06em 0 rgba(255,246,224,.6)}

/* living room overlays */
.pk{transform:translateX(-50%) translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw))}
.tv{aspect-ratio:284/168;font-size:.74cqw;color:#DCE4FF;display:flex;flex-direction:column;padding:.9em 1em .7em;gap:.5em}
.tv-top,.tv-foot{display:flex;justify-content:space-between;align-items:center}
.tv-top span:first-child{font-weight:700}.tv-foot{font-size:.82em;color:#8E9AC2}
.tv .seg{display:flex;gap:.25em}.tv .seg b{padding:.1em .5em;border-radius:.4em;background:rgba(255,255,255,.1);font-weight:600}
.tv .seg b.on{background:#E8603F;color:#fff}
.tv-bars{flex:1;display:flex;align-items:flex-end;gap:.6em;border-bottom:.08em solid rgba(255,255,255,.2)}
.tv-bars i{flex:1;background:#F0A58E;border-radius:.2em .2em 0 0;box-shadow:0 0 .8em rgba(240,140,110,.5)}
.tv-bars i.now{background:#FFD27A;box-shadow:0 0 1em rgba(255,210,122,.7)}
.tv-bars i.unk{background:repeating-linear-gradient(45deg,transparent 0 .25em,rgba(200,210,240,.35) .25em .5em);border:.08em dashed #8E9AC2;box-shadow:none}
.meal{display:flex;gap:.45em;align-items:center;white-space:nowrap;font-size:.74cqw;padding:.4em .7em;border-radius:.6em;background:rgba(255,253,247,.94);box-shadow:0 .4em 1.2em rgba(15,8,25,.4)}
.meal b{font-size:1.1em;color:var(--ink)}
.meal span{padding:.12em .5em;border-radius:.4em;font-weight:700;font-size:.9em}
.meal .ok{background:#DDEFD9;color:#3F7D4E}.meal .unk{color:#9C8E83;border:.1em dashed #9C8E83}
.tag{font-size:.7cqw;font-weight:700;color:var(--ink);white-space:nowrap;padding:.25em .7em;border-radius:99em;background:rgba(255,253,247,.9);box-shadow:0 .3em .8em rgba(15,8,25,.35)}
.tag::before{content:'';display:inline-block;width:.55em;height:.55em;border-radius:50%;background:#E8603F;margin-right:.4em;box-shadow:0 0 0 .2em rgba(232,96,63,.3)}
.steam{aspect-ratio:1/1.4}
.steam i{position:absolute;bottom:0;width:22%;height:100%;border-radius:50%;background:linear-gradient(transparent,rgba(255,255,255,.75));filter:blur(.25cqw);animation:steam 3s ease-in-out infinite;opacity:0}
.steam i:first-child{left:22%}.steam i:last-child{left:56%;animation-delay:1.4s}
@keyframes steam{0%{transform:translateY(30%) scaleX(.6);opacity:0}40%{opacity:.8}100%{transform:translateY(-40%) scaleX(1.5);opacity:0}}

/* kitchen overlays: tickets on the rail, sticky notes on the fridge */
.ticket{aspect-ratio:110/160;background-size:100% 100%;font-size:.62cqw;display:flex;flex-direction:column;align-items:center;gap:.3em;padding-top:1.4em;color:var(--ink);transform-origin:50% 0}
.ticket b{font-size:1.15em}.ticket span{font-size:.85em;color:var(--ink2)}
.ticket em{font-style:normal;font-weight:700;font-size:.85em;padding:.1em .5em;border-radius:.4em;margin-top:.3em}
.ticket .warn{background:#FBEED2;color:#8A5A00}.ticket .hot{background:#FCE3DA;color:#D9472B}.ticket .unk{color:#9C8E83;border:.1em dashed #9C8E83}
.note{font-size:.6cqw;line-height:1.45;color:var(--ink);padding:1.2em .7em .7em;box-shadow:.1em .3em .7em rgba(20,10,25,.35);
  transform:translateX(-50%) translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw)) rotate(var(--r))}
.note b{display:block;font-size:1.1em;margin-bottom:.15em}
.note.y{background:linear-gradient(#FFF6B8,#F6E690)}.note.g{background:linear-gradient(#E3F2DC,#C9E3BF)}
.note.p{background:linear-gradient(#FCE3DA,#F6C9BA)}.note.w{background:linear-gradient(#FFFEFA,#EFE8DA)}
.note .mag{position:absolute;left:50%;top:-.5em;width:1.3em;height:1.3em;margin-left:-.65em;border-radius:50%;background:radial-gradient(circle at 35% 30%,#FF9A7C,#C8331C 60%,#6E170B);box-shadow:.05em .15em .3em rgba(20,10,25,.5)}
.note.g .mag,.note.w .mag{background:radial-gradient(circle at 35% 30%,#B5DB8E,#4E8A45 60%,#27422C)}
.note .big{font-size:1.5em;font-weight:700}.note .cal{letter-spacing:.08em}.note .cal u{text-decoration:none;background:#E8603F;color:#fff;border-radius:.3em;padding:0 .25em}
.note .unkt{color:#9C8E83;border-bottom:.1em dashed #9C8E83}

/* questionnaire on the order slip (desktop: on the clipboard; phone: bottom sheet) */
.paper{aspect-ratio:458/760;font-size:1.14cqw}
.fm{display:flex;flex-direction:column;gap:.7em;height:100%;padding:1.2em 1.4em 1.4em;color:var(--ink)}
.fm-h{display:flex;justify-content:space-between;align-items:baseline;border-bottom:.14em solid #C2553A;padding-bottom:.4em}
.fm-h b{font-size:1.5em}.fm-h span{font-weight:700;color:#A93019}
.fm-bar{display:flex;gap:.35em}.fm-bar i{flex:1;height:.45em;border-radius:99em;background:#E4DAC6}
.fm-bar i.d{background:#3F7D4E}.fm-bar i.c{background:#D9472B}
.fm-done{font-weight:700;color:#3F7D4E;font-size:.92em}.fm-done span{font-weight:400;color:var(--ink2);margin-left:.8em}
.fm-cur{display:flex;flex-direction:column;gap:.6em;padding:.9em;border-radius:.6em;background:rgba(255,246,224,.7);border:.1em solid #E6CBA3}
.fm-cur b{font-size:1.15em}.fm-cur small{font-size:.8em;color:var(--ink2)}
.fm-row{display:flex;gap:.8em;align-items:stretch}
.cam{flex:none;width:9em;aspect-ratio:4/3;border-radius:.5em;background:#26242C;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.4em;outline:.12em dashed #FFD27A;outline-offset:-.5em}
.cam i{width:2.4em;height:1.7em;border-radius:.35em;border:.2em solid #FFF6E0;position:relative}
.cam i::after{content:'';position:absolute;inset:22%;border-radius:50%;border:.2em solid #FFF6E0}
.cam em{font-style:normal;font-size:.72em;color:#FFF6E0}
.chips{display:flex;flex-wrap:wrap;gap:.4em;align-content:flex-start}
.chips i{font-style:normal;font-weight:700;font-size:.86em;padding:.3em .7em;border-radius:99em}
.chips .ok{background:#DDEFD9;color:#3F7D4E}.chips .maybe{background:#FBEED2;color:#8A5A00}.chips .add{color:#9C8E83;border:.1em dashed #9C8E83}
.fm-next{color:#9C8E83;font-weight:600;font-size:.92em}
.fm-btn{margin-top:auto;display:flex;gap:.7em}
.fm-btn span{flex:1;text-align:center;font-weight:700;padding:.7em;border-radius:.5em;background:#FBF7EE;border:.1em solid #CDBFA3}
.fm-btn .pri{flex:1.6;background:#A93019;border-color:#A93019;color:#fff;box-shadow:0 .2em .5em rgba(40,10,5,.35)}
.sheet{position:absolute;left:0;right:0;bottom:0;height:54%;pointer-events:auto;background:linear-gradient(#FFFEFA,#F1EADB);border-radius:5cqw 5cqw 0 0;box-shadow:0 -2cqw 6cqw rgba(15,8,25,.45);font-size:3.05cqw}
.sheet .grip{position:absolute;top:1.6cqw;left:50%;width:12cqw;height:1.2cqw;margin-left:-6cqw;border-radius:99em;background:#CDBFA3}
.sheet .fm{padding:5cqw 5cqw 4cqw;gap:.6em}
.vp.sheeted .runner,.vp.sheeted .chat{display:none}
.potsteam{display:none;aspect-ratio:1/1.1}
.room[data-pot=lidded] .potsteam{display:block}
.potctl{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px;font-size:13px}
.potctl span{font-weight:700;margin-right:4px}
.potctl button{font:inherit;font-weight:600;border:0;border-radius:999px;padding:6px 12px;background:#FBEFDD;color:var(--ink2);cursor:pointer}
.potctl button.on{background:#A93019;color:#fff}

/* open recipe book */
.page{aspect-ratio:534/640;font-size:1.04cqw;color:var(--ink);display:flex;flex-direction:column;gap:.5em}
.pg-h,.pg-f{display:flex;justify-content:space-between;font-size:.8em;color:var(--ink2)}
.pg-h .search{font-style:italic;border-bottom:.1em dashed #B9AD96;padding:0 2em .1em .2em}
.pg-f{margin-top:auto}
.lp-filter{display:none;gap:.4em;overflow:hidden}.lp-filter i{font-style:normal;font-weight:700;font-size:.82em;padding:.2em .7em;border-radius:99em;background:#EFE7D6;white-space:nowrap}
.lp-filter i.on{background:#D9472B;color:#fff}
.vp.phone .lp-filter{display:flex}
.rlist{list-style:none;display:flex;flex-direction:column;flex:1;justify-content:space-between}
.rlist li{display:flex;align-items:center;gap:.8em;padding:.42em .7em;border-bottom:.08em solid #E4DAC6;counter-increment:r}
.rlist li::before{content:counter(r);width:1.6em;color:#B9AD96;font-weight:700;font-size:.85em}
.rlist{counter-reset:r 20}
.rlist b{flex:1;font-size:1.05em}.rlist span{font-size:.8em;color:var(--ink2)}.rlist em{width:1.2em;font-style:normal;font-weight:700;color:#3F7D4E}
.rlist li.sel{background:rgba(221,239,217,.9);border-radius:.4em;border-bottom-color:transparent}
.page h3{font-size:1.9em;border-bottom:.1em solid #C2553A;padding-bottom:.2em}
.page .meta{color:var(--ink2);font-size:.88em}
.page h4{font-size:1em;margin-top:.5em;color:#A93019}
.page p{line-height:1.6}
.pg-btn{display:flex;gap:.6em;margin-top:.8em}
.pg-btn span{font-weight:700;padding:.5em 1em;border-radius:.4em;background:#FBF7EE;border:.1em solid #CDBFA3}
.pg-btn .pri{background:#3F7D4E;border-color:#3F7D4E;color:#fff}
.tablabel{font-size:.95cqw;font-weight:700;color:#fff;writing-mode:vertical-rl;letter-spacing:.2em;text-shadow:0 .05em .15em rgba(0,0,0,.4);opacity:.85}
.tablabel.on{opacity:1;text-decoration:underline;text-underline-offset:.3em}
.tray{display:flex;gap:.6em;align-items:center;font-size:.86cqw;padding:.2em}
.tray .card{font-weight:700;padding:.7em .9em;border-radius:.3em;background:linear-gradient(#FFFEFA,#EFE8DA);color:var(--ink);box-shadow:0 .2em .5em rgba(10,5,10,.5)}
.tray .card.empty{background:none;box-shadow:none;border:.12em dashed #B9AD96;color:#B9AD96}
.tray .go{margin-left:auto;font-weight:700;padding:.7em 1em;border-radius:.4em;background:#A93019;color:#fff;box-shadow:0 .2em .5em rgba(10,5,10,.5)}

/* dining table: data-driven sprites, labels, receipt, side panels */
.rot{transform:translateX(-50%) translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw)) rotate(var(--r,0deg))}
.unkring{outline:.22cqw dashed #FFF6E0;outline-offset:.2cqw;border-radius:50%}
.seat{font-size:.74cqw;white-space:nowrap;text-align:center;padding:.35em .7em;border-radius:.6em;background:rgba(255,253,247,.95);box-shadow:0 .4em 1em rgba(15,8,25,.45);color:var(--ink)}
.seat b{font-size:1.1em;margin-right:.4em}.seat span{display:block;font-size:.88em;color:var(--ink2)}
.seat em,.side em{font-style:normal;font-weight:700;font-size:.86em;padding:.08em .5em;border-radius:.4em}
.ok{background:#DDEFD9;color:#3F7D4E}.warn{background:#FBEED2;color:#8A5A00}em.unk{color:#9C8E83;border:.1em dashed #9C8E83}
.unkt{text-decoration:none;color:#9C8E83;border-bottom:.1em dashed #9C8E83}
.dlabel{font-size:.66cqw;font-weight:700;white-space:nowrap;padding:.15em .6em;border-radius:99em;background:rgba(30,20,35,.72);color:#fff}
.rcpt{aspect-ratio:150/230;background-size:100% 100%;font-size:.5cqw;color:var(--ink);padding:1.4em 1.5em 0 1.7em;display:flex;flex-direction:column;gap:.35em;
  transform:translateX(-50%) translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw)) rotate(var(--r))}
.rcpt b{font-size:1.05em;text-align:center;letter-spacing:.2em;margin-bottom:.6em}
.rcpt p{display:flex;justify-content:space-between;font-weight:700}.rcpt p i{font-style:normal;font-size:1.2em}
.rcpt p.sub{color:var(--ink2)}.rcpt small{font-size:.85em;color:var(--ink2);margin-top:.5em}
.rcpt em{align-self:flex-end;margin-top:.3em;font-style:normal;font-weight:800;color:#3F7D4E;border:.18em solid #3F7D4E;border-radius:50%;padding:.5em .4em;transform:rotate(-14deg);opacity:.85}
.tent{font-size:.7cqw;white-space:nowrap;padding:.4em .8em;background:linear-gradient(#FFFEFA,#EFE8DA);border-radius:.3em;box-shadow:0 .4em .9em rgba(15,8,25,.45);color:var(--ink)}
.tent b{margin-right:.6em;color:#A93019}
.tipcard{font-size:.66cqw;width:9cqw;padding:.6em .8em;line-height:1.5;background:linear-gradient(#FFF6B8,#F6E690);box-shadow:0 .4em .9em rgba(15,8,25,.45);color:var(--ink);
  transform:translateX(-50%) translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw)) rotate(var(--r))}
.tipcard b{display:block}.tipcard small{display:block;color:var(--ink2);font-size:.86em}
.side{top:16%;width:17%;font-size:.78cqw;padding:1em 1.1em;border-radius:1em;background:rgba(255,253,247,.96);box-shadow:0 1em 2.4em rgba(15,8,25,.5);color:var(--ink);transform:none}
.side.l{left:1.6%}.side.r{left:81.4%}
.side h4{font-size:1.05em;margin-bottom:.6em;color:#A93019}
.side table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.side th{text-align:left;font-size:.84em;color:var(--ink2);font-weight:600;padding:.2em 0}
.side td{padding:.4em 0;border-top:.08em solid #EFE7D6}.side tr.sum td{font-weight:700;border-top:.14em solid #CDBFA3}
.side p{margin-top:.8em;font-size:.86em;line-height:1.6;color:var(--ink2)}
.actbar-d{left:50%;top:92%;display:flex;gap:.8em;font-size:.86cqw;white-space:nowrap}
.actbar-d span,.actbar span{font-weight:700;padding:.7em 1.4em;border-radius:.8em;background:#FFFDF7;color:var(--ink);box-shadow:0 .5em 1.2em rgba(15,8,25,.45)}
.actbar-d .pri,.actbar .pri{background:#3F7D4E;color:#fff}
.vp.phone .side,.vp.phone .actbar-d{display:none}
.actbar{position:absolute;left:3cqw;right:3cqw;bottom:2.6cqw;height:13.5cqw;display:flex;gap:2cqw;font-size:3.4cqw}
.actbar span{flex:1;display:flex;align-items:center;justify-content:center;border-radius:4cqw;padding:0}.actbar .pri{flex:1.7}

/* nutrition knowledge tray + panel states */
.room[data-know=has] .know-none,.room[data-know=has] .know-empty,.room:not([data-know=has]) .know-has{display:none}
.room[data-know=down] .know-none:not(.know-down),.room[data-know=nf] .know-none:not(.know-nf),.room[data-know=retry] .know-none:not(.know-retry){display:none}
.side.k{left:1.6%;top:56%}
.kcard{padding:.7em .8em;border-radius:.4em;background:linear-gradient(#FFFEFA,#EFE8DA);border-top:.5em solid #3F7D4E;box-shadow:0 .2em .6em rgba(15,8,25,.25);margin-bottom:.5em}
.kcard.dim{opacity:.55;transform:scale(.96)}
.kcard b{font-size:1.1em}.kcard p{margin:.2em 0 .4em!important;font-size:.92em!important;color:var(--ink)!important;line-height:1.55}
.kcard small{font-size:.8em;color:var(--ink2)}.kcard small i{font-style:normal;font-weight:700;color:#A93019;text-decoration:underline}
.kpager{display:flex;justify-content:center;gap:1.2em;font-weight:700;color:var(--ink2)}
.kmsg{margin-top:0!important;font-size:.95em!important;color:var(--ink)!important}
.kbtn{display:inline-block;margin-top:.6em;font-weight:700;padding:.5em 1em;border-radius:.5em;background:#A93019;color:#fff}
.kbtn.off{background:#CDBFA3}

/* fridge door: magnet tabs and notes */
.magtab{font-size:1cqw;font-weight:800;white-space:nowrap;padding:.5em 1.2em;border-radius:99em;color:#fff;background:radial-gradient(circle at 30% 25%,#8A9198,#4A5158 60%,#26242C);box-shadow:.1em .3em .6em rgba(20,10,25,.5)}
.magtab.on{background:radial-gradient(circle at 30% 25%,#FF9A7C,#C8331C 60%,#6E170B)}
.fnote{font-size:.86cqw;line-height:1.55;color:var(--ink);padding:1.6em 1.1em 1em;box-shadow:.1em .4em 1em rgba(20,10,25,.35);
  transform:translateX(-50%) translate(calc(var(--px) * var(--k) * -1.2cqw),calc(var(--py) * var(--k) * -.8cqw)) rotate(var(--r))}
.fnote>b{display:block;font-size:1.15em;margin-bottom:.4em}.fnote small{display:block;font-size:.78em;color:var(--ink2);margin-top:.5em}
.fnote.y{background:linear-gradient(#FFF6B8,#F6E690)}.fnote.g{background:linear-gradient(#E3F2DC,#C9E3BF)}.fnote.w{background:linear-gradient(#FFFEFA,#EFE8DA)}
.fnote .mag{position:absolute;left:50%;top:-.7em;width:1.7em;height:1.7em;margin-left:-.85em;border-radius:50%;background:radial-gradient(circle at 35% 30%,#FF9A7C,#C8331C 60%,#6E170B);box-shadow:.05em .2em .4em rgba(20,10,25,.5)}
.fnote .mag.g{background:radial-gradient(circle at 35% 30%,#B5DB8E,#4E8A45 60%,#27422C)}
.fcal{display:grid;grid-template-columns:repeat(7,1fr);gap:.25em;text-align:center}
.fcal u{text-decoration:none;font-size:.8em;color:var(--ink2);font-weight:700}
.fcal i{font-style:normal;aspect-ratio:1;display:flex;align-items:center;justify-content:center;border-radius:.4em;font-weight:600;position:relative}
.fcal i.has{background:#fff;box-shadow:0 .1em .3em rgba(20,10,25,.2)}
.fcal i.has::after{content:'';position:absolute;right:.2em;bottom:.2em;width:.5em;height:.5em;border-radius:50%;background:#3F7D4E}
.fcal i.sel{background:#A93019;color:#fff}.fcal i.sel::after{background:#FFD27A}
.fbtn{display:flex;gap:.5em;margin-top:.7em}
.fbtn span{flex:1;text-align:center;font-weight:700;font-size:.9em;padding:.5em .4em;border-radius:.4em;background:rgba(255,255,255,.75);border:.1em solid #CDBFA3}
.fbtn .pri{background:#A93019;border-color:#A93019;color:#fff}
.fbars{display:flex;align-items:flex-end;gap:1em;height:7em;border-bottom:.1em solid #CDBFA3;padding:0 .6em}
.fbars i{flex:1;background:#F0A58E;border-radius:.3em .3em 0 0;position:relative}.fbars i.now{background:#A93019}
.fbars b{position:absolute;top:-1.5em;left:50%;transform:translateX(-50%);font-size:.78em;white-space:nowrap}
.frow{display:flex;justify-content:space-between;align-items:center;padding:.3em 0;border-top:.08em solid rgba(60,90,60,.25)}
.frow em{font-style:normal;font-weight:700;font-size:.82em;padding:.08em .5em;border-radius:.4em}
.hot{background:#FCE3DA;color:#D9472B}

/* family portrait: crayon names, plaque, panels */
.cname{font-size:.8cqw;text-align:center;white-space:nowrap;font-family:'Comic Sans MS','Chalkboard SE','M PLUS Rounded 1c',cursive;color:#3A2A20}
.cname b{display:block;font-size:1.25em;color:#3F6FC2}.cname span{font-size:.78em;color:#6E5A4C}
.cname.addp b{color:#9C8E83;font-size:1em}
.pencil{text-decoration:none;border-bottom:.12em solid #9C8E83;color:#9C8E83}
.plaque{font-size:.9cqw;font-weight:800;letter-spacing:.3em;color:#4A3610;white-space:nowrap;text-shadow:0 .06em 0 rgba(255,246,200,.7)}
.who{margin:0 0 .6em!important;font-size:1em!important;color:var(--ink)!important}.who b{margin-right:.6em;font-size:1.15em}
.seg3{display:flex;gap:.3em;margin-bottom:.7em}.seg3 span{flex:1;text-align:center;font-weight:700;font-size:.9em;padding:.4em;border-radius:.5em;background:#EFE7D6}
.seg3 .on{background:#A93019;color:#fff}
.side .fbtn span{background:#FBF7EE}

/* mascot: illustrated style, patrols the bottom edge */
.hud{position:absolute;inset:0;pointer-events:none}
.runner{position:absolute;left:0;bottom:1.2cqw;width:var(--mw);animation:patrol 14s linear infinite;filter:drop-shadow(0 .3cqw .5cqw rgba(20,12,30,.35))}
.runner .flip{animation:flip 14s steps(1) infinite}
.runner svg{display:block;width:100%;height:auto;overflow:visible}
@keyframes patrol{0%{transform:translateX(3cqw)}46%{transform:translateX(calc(97cqw - var(--mw)))}50%{transform:translateX(calc(97cqw - var(--mw)))}96%{transform:translateX(3cqw)}100%{transform:translateX(3cqw)}}
@keyframes flip{0%{transform:scaleX(1)}48%{transform:scaleX(-1)}98%{transform:scaleX(1)}}
.m-leg-a{transform-origin:48px 102px;animation:legA .4s ease-in-out infinite}
.m-leg-b{transform-origin:72px 102px;animation:legB .4s ease-in-out infinite}
.m-arm-a{transform-origin:26px 76px;animation:legB .4s ease-in-out infinite}
.m-arm-b{transform-origin:94px 76px;animation:legA .4s ease-in-out infinite}
.m-bob{animation:bob .2s ease-in-out infinite alternate}
.m-shadow{transform-origin:60px 133px;animation:shadow .2s ease-in-out infinite alternate}
@keyframes legA{0%,100%{transform:rotate(30deg)}50%{transform:rotate(-30deg)}}
@keyframes legB{0%,100%{transform:rotate(-30deg)}50%{transform:rotate(30deg)}}
@keyframes bob{from{transform:translateY(0)}to{transform:translateY(-4px)}}
@keyframes shadow{from{transform:scaleX(1)}to{transform:scaleX(.86)}}

/* floating chat window; when open the mascot stops next to it */
.chat{position:absolute;right:2.5cqw;bottom:calc(var(--mw) * 1.25 + 1.5cqw);width:var(--cw);font-size:var(--fs);display:none;flex-direction:column;
  background:#FFFDF7;border-radius:1.2em;box-shadow:0 1.2em 3em rgba(15,8,25,.5),0 0 0 .08em rgba(255,255,255,.5);overflow:hidden;color:var(--ink)}
.chat::after{content:'';position:absolute;right:calc(var(--mw) * .45);bottom:-.7em;border:.8em solid transparent;border-top-color:#FFFDF7;border-bottom:0}
.chat{overflow:visible}
.chat header{display:flex;align-items:center;gap:.5em;padding:.8em 1em;border-bottom:.08em solid #F3E0C3}
.chat header b{font-size:1.05em}.chat header .sub{font-size:.72em;color:var(--ink2);flex:1}.chat header i{font-style:normal;color:var(--ink2)}
.chat .msgs{padding:.9em 1em;display:flex;flex-direction:column;gap:.7em}
.chat p{max-width:88%;padding:.6em .8em;border-radius:1em;line-height:1.55;font-size:.9em}
.chat .me{align-self:flex-end;background:#E8603F;color:#fff;border-bottom-right-radius:.3em}
.chat .bot{align-self:flex-start;background:#FBEFDD;border-bottom-left-radius:.3em}
.chat .src{display:block;margin-top:.5em;padding:.45em .6em;background:#fff;border-radius:.6em;font-size:.86em}
.chat .src em{display:block;font-style:normal;color:var(--ink2);font-size:.9em}
.chat footer{display:flex;gap:.5em;padding:.7em 1em 1em}
.chat footer .inp{flex:1;border:.1em solid #F3E0C3;border-radius:.7em;padding:.55em .8em;color:var(--ink2);font-size:.9em}
.chat footer .send{background:#E8603F;color:#fff;font-weight:700;border-radius:.7em;padding:.55em 1em;font-size:.9em}
.tg-chat:checked~.views .chat{display:flex}
.tg-chat:checked~.views .runner{animation:none;transform:translateX(calc(97cqw - var(--mw) * 1.3))}
.tg-chat:checked~.views .runner .flip{animation:none;transform:scaleX(-1)}
.tg-chat:checked~.views .runner .m-leg-a,.tg-chat:checked~.views .runner .m-leg-b,.tg-chat:checked~.views .runner .m-arm-b{animation:none}
.tg-chat:checked~.views .runner .m-arm-a{animation:wave .5s ease-in-out infinite alternate}
.tg-chat:checked~.views .runner .m-bob{animation-duration:1.2s}
@keyframes wave{from{transform:rotate(100deg)}to{transform:rotate(140deg)}}
.notes{margin:14px 0 0 20px;font-size:13.5px;line-height:1.8;color:var(--ink2)}
@media (prefers-reduced-motion:reduce){.runner,.runner *{animation:none!important}}
"""

JS = """
document.querySelectorAll('.vp').forEach(v=>{
  v.addEventListener('pointermove',e=>{const r=v.getBoundingClientRect();
    v.style.setProperty('--px',(((e.clientX-r.left)/r.width-.5)*2).toFixed(3));
    v.style.setProperty('--py',(((e.clientY-r.top)/r.height-.5)*2).toFixed(3))});
  v.addEventListener('pointerleave',()=>{v.style.setProperty('--px',0);v.style.setProperty('--py',0)});
});
// pot state switcher (passboard scene)
const POTS=__POTS__;
document.querySelectorAll('.potctl:not(.knowctl)').forEach(ctl=>{
  const room=ctl.closest('.room');
  ctl.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    room.dataset.pot=b.dataset.pot;
    ctl.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));
    room.querySelectorAll('.potimg').forEach(i=>i.src=POTS[b.dataset.pot])});
});
document.querySelectorAll('.knowctl').forEach(ctl=>{
  const room=ctl.closest('.room');
  ctl.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    room.dataset.know=b.dataset.know;ctl.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b))});
});
// phone: camera stops (swipe / edge hints) and the hotspot dock
document.querySelectorAll('.vp.phone[data-stops]').forEach(vp=>{
  const stops=JSON.parse(vp.dataset.stops), world=vp.querySelector('.world');
  const dots=[...vp.querySelectorAll('.dots i')], chips=[...vp.querySelectorAll('.dock button')];
  const L=vp.querySelector('.edge.l'), R=vp.querySelector('.edge.r');
  let cur=+vp.dataset.cur;
  const go=i=>{cur=Math.max(0,Math.min(stops.length-1,i));
    world.style.setProperty('--fx',(stops[cur][1]*100)+'%');
    dots.forEach((d,k)=>d.classList.toggle('on',k===cur));
    chips.forEach(c=>c.classList.toggle('cur',+c.dataset.stop===cur));
    L.textContent=cur>0?'\u2039 '+stops[cur-1][0]:'';R.textContent=cur<stops.length-1?stops[cur+1][0]+' \u203a':'';
    const dock=vp.querySelector('.dock'), c=chips.find(c=>+c.dataset.stop===cur);
    if(c)dock.scrollTo({left:c.offsetLeft-dock.clientWidth*.08,behavior:'smooth'})};
  chips.forEach(c=>c.addEventListener('click',()=>go(+c.dataset.stop)));
  [L,R].forEach(b=>b.addEventListener('click',()=>go(cur+ +b.dataset.dir)));
  let x0=null;
  vp.addEventListener('pointerdown',e=>{if(e.target.closest('button'))return;x0=e.clientX});
  vp.addEventListener('pointerup',e=>{if(x0===null)return;const dx=e.clientX-x0;x0=null;if(Math.abs(dx)>24)go(cur+(dx<0?1:-1))});
  vp.addEventListener('pointercancel',()=>{x0=null});
  go(cur);
});
"""


def main() -> None:
    rooms = [r for r in META if (ROOMS / r).is_dir()]
    body = "".join(room_section(r) for r in rooms)
    pots = {k: data_uri(POT_DIR / (f"pot-top-{k}.svg")) for k, _ in POT_STATES}
    js = JS.replace("__POTS__", json.dumps(pots))
    html = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>今天吃啥 插圖審核</title><style>{CSS}</style></head><body>
<h1>🎨 分層插圖審核</h1>
<p class="lead">全站畫風定為<b>寫實風</b>；吉祥物維持插畫風，在畫面底部來回跑，聊天室是懸浮視窗。每個房間一組分層 SVG（<code>src/assets/rooms/&lt;room&gt;/z0–z3.svg</code>）。點上方標籤可單獨開關每一層、吉祥物與聊天室；移動滑鼠看視差。左邊是桌機完整構圖，右邊是手機直式——同一張圖，只是鏡頭對準的位置不同。介面疊層是示意，文字與欄位之後由 React 元件提供。</p>
{body}
<script>{js}</script></body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(html) // 1024} KB, rooms: {', '.join(rooms)})")


if __name__ == "__main__":
    main()
