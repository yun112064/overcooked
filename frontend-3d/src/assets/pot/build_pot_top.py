"""Generate the top-view hot-pot states from shared parts.

Usage (from the repository root):  python frontend-3d/src/assets/pot/build_pot_top.py

Writes next to this file:
  pot-top-0.svg .. pot-top-5.svg   empty pot, then one fixed ingredient per questionnaire step
                                   (1 greens, 2 mushrooms, 3 meat, 4 broth, 5 sides + rim glow)
  pot-top-lidded.svg               lid on (generating; steam is a CSS animation in the component)
  pot-top-ready.svg                finished: full pot with a strong glow
  pot-top-failed.svg               lid knocked askew (smoke is a CSS animation in the component)

The ingredients are progress markers only. They never depend on what the user entered.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFS = """
  <defs>
    <filter id="pt-b8" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="8"/></filter>
    <filter id="pt-b3" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
    <linearGradient id="pt-enamel" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#F08266"/><stop offset=".3" stop-color="#C8401F"/><stop offset="1" stop-color="#7E1C0E"/>
    </linearGradient>
    <linearGradient id="pt-wall" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#9A8662"/><stop offset=".6" stop-color="#D9C8A8"/><stop offset="1" stop-color="#F3E7CF"/>
    </linearGradient>
    <radialGradient id="pt-bottom" cx=".6" cy=".62" r=".7">
      <stop offset="0" stop-color="#F8EEDA"/><stop offset="1" stop-color="#D2C09C"/>
    </radialGradient>
    <radialGradient id="pt-broth" cx=".42" cy=".38" r=".75">
      <stop offset="0" stop-color="#F9D2A6"/><stop offset=".6" stop-color="#E9A56B"/><stop offset="1" stop-color="#C97A40"/>
    </radialGradient>
    <linearGradient id="pt-leaf" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#2F5236"/><stop offset=".6" stop-color="#4E8A45"/><stop offset="1" stop-color="#9BCB78"/>
    </linearGradient>
    <radialGradient id="pt-shroom" cx=".4" cy=".35" r=".7">
      <stop offset="0" stop-color="#B98A5C"/><stop offset=".7" stop-color="#7A5636"/><stop offset="1" stop-color="#4A321E"/>
    </radialGradient>
    <linearGradient id="pt-meat" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#F0A090"/><stop offset=".5" stop-color="#D2604A"/><stop offset="1" stop-color="#A63C2A"/>
    </linearGradient>
    <radialGradient id="pt-corn" cx=".4" cy=".35" r=".7">
      <stop offset="0" stop-color="#FFF0A0"/><stop offset=".7" stop-color="#F2C23A"/><stop offset="1" stop-color="#C9952A"/>
    </radialGradient>
    <radialGradient id="pt-yolk" cx=".4" cy=".35" r=".7">
      <stop offset="0" stop-color="#FFD27A"/><stop offset="1" stop-color="#E88A2A"/>
    </radialGradient>
    <radialGradient id="pt-tom" cx=".36" cy=".3" r=".75">
      <stop offset="0" stop-color="#FF9A7C"/><stop offset=".45" stop-color="#DC4426"/><stop offset="1" stop-color="#861C0E"/>
    </radialGradient>
    <radialGradient id="pt-brass" cx=".35" cy=".3" r=".8">
      <stop offset="0" stop-color="#FFF0B8"/><stop offset=".4" stop-color="#D9B04E"/><stop offset="1" stop-color="#6E5216"/>
    </radialGradient>
    <radialGradient id="pt-glow" cx=".5" cy=".5" r=".5">
      <stop offset=".72" stop-color="#FFD27A" stop-opacity="0"/><stop offset=".84" stop-color="#FFE9A8" stop-opacity=".95"/><stop offset="1" stop-color="#FFD27A" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="pt-shine" cx=".5" cy=".5" r=".5">
      <stop offset="0" stop-color="#FFF6D0" stop-opacity=".55"/><stop offset="1" stop-color="#FFF6D0" stop-opacity="0"/>
    </radialGradient>
  </defs>
"""

SHADOW = '  <circle cx="252" cy="258" r="206" fill="#0E0A14" opacity=".5" filter="url(#pt-b8)"/>\n'

HANDLES = """
  <rect x="14" y="204" width="62" height="72" rx="24" fill="none" stroke="url(#pt-enamel)" stroke-width="20"/>
  <rect x="404" y="204" width="62" height="72" rx="24" fill="none" stroke="url(#pt-enamel)" stroke-width="20"/>
"""

BODY = """
  <circle cx="240" cy="240" r="200" fill="url(#pt-enamel)"/>
  <path d="M70 180 A182 182 0 0 1 300 68" fill="none" stroke="#FFD2C2" stroke-width="5" stroke-linecap="round" opacity=".7"/>
  <circle cx="240" cy="240" r="184" fill="url(#pt-wall)"/>
  <circle cx="240" cy="240" r="164" fill="url(#pt-bottom)"/>
  <path d="M96 190 A152 152 0 0 1 290 96 A170 170 0 0 0 96 190Z" fill="#3A2A20" opacity=".22" filter="url(#pt-b3)"/>
"""


def leaf(x: float, y: float, rot: float, s: float = 1.0) -> str:
    return (
        f'<g transform="translate({x} {y}) rotate({rot}) scale({s})">'
        '<path d="M0 6 C-26 -24 -24 -66 0 -92 C24 -66 26 -24 0 6Z" fill="url(#pt-leaf)"/>'
        '<path d="M0 6 C-9 -14 -8 -34 0 -50 C8 -34 9 -14 0 6Z" fill="#EAF2D6"/>'
        '<path d="M0 -50 V-86" stroke="#DCEBC0" stroke-width="2" opacity=".7"/></g>'
    )


def shroom(x: float, y: float, r: float) -> str:
    return (
        f'<g transform="translate({x} {y})"><circle r="{r + 3}" cy="3" fill="#2A1A10" opacity=".3" filter="url(#pt-b3)"/>'
        f'<circle r="{r}" fill="url(#pt-shroom)"/>'
        f'<path d="M0 {-r * .55} V{r * .55} M{-r * .48} {-r * .28} L{r * .48} {r * .28} M{-r * .48} {r * .28} L{r * .48} {-r * .28}" '
        'stroke="#F3E3CA" stroke-width="4" stroke-linecap="round"/></g>'
    )


def meat(x: float, y: float, rot: float) -> str:
    return (
        f'<g transform="translate({x} {y}) rotate({rot})"><ellipse rx="33" ry="19" cy="3" fill="#2A1A10" opacity=".28" filter="url(#pt-b3)"/>'
        '<ellipse rx="32" ry="18" fill="url(#pt-meat)"/>'
        '<path d="M-22 0 q8 -12 22 -10 q16 2 18 12 q-6 8 -18 8 q-10 0 -12 -8 q4 -6 12 -4" fill="none" stroke="#FBE3DA" stroke-width="3.5" stroke-linecap="round"/></g>'
    )


GREENS = "\n  <g>" + leaf(156, 220, -62, 0.8) + leaf(172, 186, -34, 0.8) + leaf(204, 162, -4, 0.8) + leaf(150, 262, -92, 0.72) + leaf(196, 222, -46, 0.62) + "</g>\n"
SHROOMS = "\n  <g>" + shroom(128, 312, 30) + shroom(192, 356, 28) + shroom(142, 372, 22) + "</g>\n"
MEAT = "\n  <g>" + meat(292, 130, -40) + meat(338, 176, -18) + meat(354, 236, 2) + meat(342, 296, 24) + meat(298, 340, 44) + "</g>\n"

BROTH_UNDER = '\n  <circle cx="240" cy="240" r="170" fill="url(#pt-broth)"/>\n'
BROTH_OVER = """
  <circle cx="240" cy="240" r="170" fill="#E9A56B" opacity=".22"/>
  <path d="M110 170 A150 150 0 0 1 250 92" fill="none" stroke="#FFF3DC" stroke-width="6" stroke-linecap="round" opacity=".5"/>
  <g fill="#FFF3DC" opacity=".6"><circle cx="250" cy="250" r="5"/><circle cx="222" cy="288" r="3.5"/><circle cx="276" cy="212" r="3"/><circle cx="206" cy="232" r="2.5"/></g>
"""

SIDES = """
  <g>
    <!-- 玉米 -->
    <g transform="translate(238 332)"><circle r="27" cy="3" fill="#2A1A10" opacity=".28" filter="url(#pt-b3)"/><circle r="26" fill="url(#pt-corn)"/>
      <circle r="9" fill="#F6E2A0"/><g fill="#C9952A" opacity=".7"><circle cx="0" cy="-17" r="3"/><circle cx="12" cy="-12" r="3"/><circle cx="17" cy="0" r="3"/><circle cx="12" cy="12" r="3"/><circle cx="0" cy="17" r="3"/><circle cx="-12" cy="12" r="3"/><circle cx="-17" cy="0" r="3"/><circle cx="-12" cy="-12" r="3"/></g></g>
    <!-- 半熟蛋 -->
    <g transform="translate(252 196) rotate(-18)"><ellipse rx="26" ry="32" cy="3" fill="#2A1A10" opacity=".28" filter="url(#pt-b3)"/><ellipse rx="25" ry="31" fill="#FFFDF7"/><circle r="13" cy="3" fill="url(#pt-yolk)"/><circle cx="-4" cy="-1" r="3.5" fill="#FFF0B8" opacity=".8"/></g>
    <!-- 番茄 -->
    <g transform="translate(206 268)"><circle r="22" cy="3" fill="#2A1A10" opacity=".28" filter="url(#pt-b3)"/><circle r="21" fill="url(#pt-tom)"/><path d="M0 -6 l-7 -4 l5 -1 l2 -6 l2 6 l5 1Z" fill="#34573A"/></g>
    <!-- 豆腐 -->
    <g transform="translate(286 262) rotate(12)"><rect x="-20" y="-17" width="40" height="40" rx="6" fill="#2A1A10" opacity=".25" filter="url(#pt-b3)"/><rect x="-20" y="-20" width="40" height="40" rx="6" fill="#FBF6EA"/><rect x="-20" y="-20" width="40" height="8" rx="4" fill="#FFFFFF"/></g>
    <!-- 蔥花 -->
    <g fill="none" stroke="#6FA85A" stroke-width="3"><circle cx="232" cy="238" r="4"/><circle cx="268" cy="302" r="4"/><circle cx="196" cy="306" r="3.5"/><circle cx="300" cy="212" r="3.5"/><circle cx="172" cy="252" r="3.5"/></g>
  </g>
"""

RIM_GLOW = '\n  <circle cx="240" cy="240" r="238" fill="url(#pt-glow)"/>\n'
READY_GLOW = RIM_GLOW + '  <circle cx="240" cy="240" r="170" fill="url(#pt-shine)"/>\n'

LID = """
  <g{t}>
    <circle cx="240" cy="240" r="197" fill="url(#pt-enamel)"/>
    <circle cx="240" cy="240" r="152" fill="none" stroke="#7E1C0E" stroke-width="4" opacity=".6"/>
    <circle cx="240" cy="240" r="148" fill="none" stroke="#F4A088" stroke-width="2" opacity=".6"/>
    <circle cx="240" cy="240" r="70" fill="none" stroke="#7E1C0E" stroke-width="3" opacity=".5"/>
    <path d="M78 176 A176 176 0 0 1 296 72" fill="none" stroke="#FFD2C2" stroke-width="7" stroke-linecap="round" opacity=".6"/>
    <circle cx="244" cy="246" r="30" fill="#2A0C06" opacity=".45" filter="url(#pt-b3)"/>
    <circle cx="240" cy="240" r="27" fill="url(#pt-brass)"/>
    <circle cx="232" cy="231" r="8" fill="#FFF8DC" opacity=".8"/>
  </g>
"""


def svg(comment: str, *parts: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 480">\n'
        f"  <!-- {comment} -->" + DEFS + SHADOW + HANDLES + BODY + "".join(parts) + "</svg>\n"
    )


def main() -> None:
    steps = [
        ("空鍋：沒有進行中的問卷", ()),
        ("步驟 1 完成（日期餐別、預算）：加入菜", (GREENS,)),
        ("步驟 2 完成（成員）：加入菇", (GREENS, SHROOMS)),
        ("步驟 3 完成（現有食材辨識）：加入肉", (GREENS, SHROOMS, MEAT)),
        ("步驟 4 完成（主食偏好與不吃的）：倒入湯", (BROTH_UNDER, GREENS, SHROOMS, MEAT, BROTH_OVER)),
        ("步驟 5 確認：配菜補滿、鍋緣亮", (BROTH_UNDER, GREENS, SHROOMS, MEAT, BROTH_OVER, SIDES, RIM_GLOW)),
    ]
    for i, (comment, parts) in enumerate(steps):
        (HERE / f"pot-top-{i}.svg").write_text(svg(f"火鍋（俯視）{comment}", *parts), encoding="utf-8")
    (HERE / "pot-top-lidded.svg").write_text(
        svg("火鍋（俯視）蓋上鍋蓋：生成中。蒸氣由元件的 CSS 動畫疊上", LID.format(t="")), encoding="utf-8")
    (HERE / "pot-top-ready.svg").write_text(
        svg("火鍋（俯視）完成：開蓋、整鍋發亮", BROTH_UNDER, GREENS, SHROOMS, MEAT, BROTH_OVER, SIDES, READY_GLOW), encoding="utf-8")
    (HERE / "pot-top-failed.svg").write_text(
        svg("火鍋（俯視）失敗：鍋蓋歪掉露出黑縫。黑煙由元件的 CSS 動畫疊上",
            '\n  <circle cx="240" cy="240" r="170" fill="#2A2026"/>\n', LID.format(t=' transform="rotate(-9 240 240) translate(26 -18)"')),
        encoding="utf-8")
    print("wrote", ", ".join(sorted(p.name for p in HERE.glob("pot-top-*.svg"))))


if __name__ == "__main__":
    main()
