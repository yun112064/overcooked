"""Generate the top-view tableware sprites used on the dining table.

Usage (from the repository root):  python frontend-3d/src/assets/tableware/build_tableware.py

One sprite per dish category (the result screen places one vessel per dish in the meal),
plus the per-member place setting, rice bowls, a chair and the receipt.
All sprites are drawn from directly above with the light coming from the top-left.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFS = """
  <defs>
    <filter id="tw-b3" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
    <filter id="tw-b6" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
    <filter id="tw-grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="1.1" numOctaves="2" seed="7"/>
      <feColorMatrix type="matrix" values="0 0 0 0 .4  0 0 0 0 .34  0 0 0 0 .24  1.3 0 0 0 -.56"/>
      <feComposite in2="SourceGraphic" operator="in"/>
    </filter>
    <filter id="tw-wood" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency=".02 .4" numOctaves="3" seed="12"/>
      <feColorMatrix type="matrix" values="0 0 0 0 .2  0 0 0 0 .1  0 0 0 0 .04  1.6 0 0 0 -.62"/>
      <feComposite in2="SourceGraphic" operator="in"/>
    </filter>
    <radialGradient id="tw-china" cx=".4" cy=".35" r=".8">
      <stop offset="0" stop-color="#FFFFFF"/><stop offset=".7" stop-color="#F1ECE2"/><stop offset="1" stop-color="#BDB6A8"/>
    </radialGradient>
    <radialGradient id="tw-well" cx=".6" cy=".65" r=".8">
      <stop offset="0" stop-color="#FBF8F1"/><stop offset="1" stop-color="#DCD5C7"/>
    </radialGradient>
    <radialGradient id="tw-rice" cx=".4" cy=".35" r=".8">
      <stop offset="0" stop-color="#FFFFFF"/><stop offset=".7" stop-color="#F3EEE2"/><stop offset="1" stop-color="#CFC6B4"/>
    </radialGradient>
    <linearGradient id="tw-tub" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#E6CBA0"/><stop offset="1" stop-color="#A9824F"/>
    </linearGradient>
    <linearGradient id="tw-leaf" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#2F5236"/><stop offset=".6" stop-color="#4E8A45"/><stop offset="1" stop-color="#9BCB78"/>
    </linearGradient>
    <radialGradient id="tw-broc" cx=".4" cy=".3" r=".8">
      <stop offset="0" stop-color="#8FBF6A"/><stop offset=".6" stop-color="#4E8A45"/><stop offset="1" stop-color="#2A5230"/>
    </radialGradient>
    <linearGradient id="tw-fish" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#E6E0D2"/><stop offset=".5" stop-color="#B9B4A8"/><stop offset="1" stop-color="#8E8A80"/>
    </linearGradient>
    <linearGradient id="tw-pork" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#B56A3A"/><stop offset=".5" stop-color="#8A4622"/><stop offset="1" stop-color="#5E2C12"/>
    </linearGradient>
    <radialGradient id="tw-broth" cx=".42" cy=".38" r=".75">
      <stop offset="0" stop-color="#FBE9C4"/><stop offset=".6" stop-color="#EBCB8E"/><stop offset="1" stop-color="#C9A25E"/>
    </radialGradient>
    <radialGradient id="tw-sauce" cx=".4" cy=".35" r=".8">
      <stop offset="0" stop-color="#8A5A2A"/><stop offset="1" stop-color="#3E2412"/>
    </radialGradient>
    <linearGradient id="tw-linen" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#EFE7D6"/><stop offset="1" stop-color="#D2C6AE"/>
    </linearGradient>
    <linearGradient id="tw-seat" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#6B8B63"/><stop offset="1" stop-color="#3F5A3C"/>
    </linearGradient>
    <linearGradient id="tw-walnut" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#8E6844"/><stop offset="1" stop-color="#4A321E"/>
    </linearGradient>
    <linearGradient id="tw-paper" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#ECE6DA"/>
    </linearGradient>
    <linearGradient id="tw-steel" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#AEB6BD"/><stop offset=".4" stop-color="#F4F7F9"/><stop offset="1" stop-color="#8A9198"/>
    </linearGradient>
  </defs>
"""


def plate(cx: float, cy: float, r: float, well: float = 0.74) -> str:
    return (
        f'<circle cx="{cx + 4}" cy="{cy + 6}" r="{r}" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#tw-china)"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r * well}" fill="url(#tw-well)"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r * well}" fill="none" stroke="#CFC8BA" stroke-width="1.5"/>'
        f'<path d="M{cx - r * .72} {cy - r * .58} A{r * .93} {r * .93} 0 0 1 {cx + r * .4} {cy - r * .84}" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" opacity=".9"/>'
    )


def svg(vb: str, comment: str, body: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">\n  <!-- {comment} -->{DEFS}{body}\n</svg>\n'


RICE_GRAINS = '<g fill="#FFFFFF" opacity=".9">' + "".join(
    f'<ellipse cx="{x}" cy="{y}" rx="4" ry="2" transform="rotate({r} {x} {y})"/>'
    for x, y, r in [(84, 78, 20), (104, 70, -30), (120, 84, 50), (92, 100, -10), (114, 108, 30), (76, 96, 70), (130, 104, -50), (100, 124, 10), (86, 118, -40), (122, 126, 60)]
) + "</g>"

SPRITES = {
    "dish-staple.svg": ("0 0 200 200", "主食：木飯桶裡的飯與飯匙（俯視）", f"""
  <circle cx="104" cy="106" r="92" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <circle cx="100" cy="100" r="92" fill="url(#tw-tub)"/>
  <circle cx="100" cy="100" r="92" fill="#000" filter="url(#tw-wood)"/>
  <circle cx="100" cy="100" r="92" fill="none" stroke="#6B4A2C" stroke-width="5"/>
  <circle cx="100" cy="100" r="84" fill="none" stroke="#3E2A18" stroke-width="3" stroke-dasharray="2 20" opacity=".6"/>
  <circle cx="100" cy="100" r="76" fill="#8E6A46"/>
  <circle cx="100" cy="100" r="72" fill="url(#tw-rice)"/>
  <circle cx="100" cy="100" r="72" fill="#000" filter="url(#tw-grain)" opacity=".3"/>
  {RICE_GRAINS}
  <g transform="rotate(-38 136 70)"><rect x="128" y="20" width="14" height="64" rx="6" fill="#C39A66"/><ellipse cx="135" cy="92" rx="18" ry="24" fill="#D9B98A"/><ellipse cx="131" cy="86" rx="7" ry="11" fill="#F1DDB8" opacity=".7"/></g>"""),

    "dish-seafood.svg": ("0 0 260 200", "海鮮：橢圓盤上的清蒸魚，蔥薑絲與醬汁（俯視）", """
  <ellipse cx="134" cy="106" rx="122" ry="86" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <ellipse cx="130" cy="100" rx="122" ry="86" fill="url(#tw-china)"/>
  <ellipse cx="130" cy="100" rx="98" ry="64" fill="url(#tw-well)"/>
  <ellipse cx="130" cy="104" rx="88" ry="52" fill="url(#tw-sauce)" opacity=".35"/>
  <path d="M40 54 A116 80 0 0 1 180 22" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" opacity=".9"/>
  <path d="M214 100 l26 -24 l-6 24 l6 24Z" fill="#9A968C"/>
  <path d="M52 100 C70 62 150 52 214 100 C150 148 70 138 52 100Z" fill="url(#tw-fish)"/>
  <path d="M70 100 C90 76 150 70 200 100" fill="none" stroke="#FFFFFF" stroke-width="3" opacity=".45"/>
  <g stroke="#6E6A62" stroke-width="2" opacity=".5" fill="none"><path d="M110 76 l-8 46 M134 72 l-6 54 M158 76 l-4 46 M180 84 l-2 30"/></g>
  <circle cx="72" cy="94" r="5" fill="#2A2026"/><circle cx="71" cy="93" r="1.6" fill="#FFFFFF"/>
  <g stroke="#4E8A45" stroke-width="3" stroke-linecap="round" fill="none"><path d="M100 92 q16 -10 30 0 M120 104 q18 -8 34 2 M146 90 q14 -8 28 0 M108 112 q12 6 26 0"/></g>
  <g stroke="#F2DC94" stroke-width="2.5" stroke-linecap="round" fill="none"><path d="M112 98 q14 -6 26 2 M140 108 q12 -6 24 0 M128 86 q10 -6 22 0"/></g>
  <g fill="#D9472B"><circle cx="126" cy="96" r="3"/><circle cx="160" cy="102" r="3"/><circle cx="142" cy="116" r="2.6"/></g>"""),

    "dish-vegetable.svg": ("0 0 200 200", "蔬菜：圓盤上的蒜炒青花菜（俯視）", plate(100, 100, 92) + """
  <g>
    <g fill="url(#tw-broc)">
      <circle cx="78" cy="80" r="22"/><circle cx="112" cy="70" r="24"/><circle cx="130" cy="100" r="23"/><circle cx="96" cy="108" r="24"/><circle cx="70" cy="116" r="19"/><circle cx="118" cy="130" r="20"/>
    </g>
    <g fill="#B5DB8E" opacity=".55"><circle cx="72" cy="74" r="7"/><circle cx="106" cy="62" r="8"/><circle cx="124" cy="94" r="7"/><circle cx="90" cy="102" r="8"/><circle cx="112" cy="124" r="6"/></g>
    <g fill="#2A5230" opacity=".5"><circle cx="86" cy="90" r="4"/><circle cx="120" cy="80" r="4"/><circle cx="104" cy="118" r="4"/><circle cx="76" cy="124" r="3.5"/></g>
    <g fill="#F6EBC8"><ellipse cx="60" cy="96" rx="7" ry="5"/><ellipse cx="140" cy="122" rx="7" ry="5"/><ellipse cx="98" cy="140" rx="6" ry="4.5"/><ellipse cx="136" cy="74" rx="6" ry="4.5"/></g>
    <g fill="#D9472B"><circle cx="88" cy="60" r="3"/><circle cx="144" cy="104" r="3"/></g>
  </g>"""),

    "dish-meat.svg": ("0 0 200 200", "肉類：圓盤上的滷肉片與青菜（俯視）", plate(100, 100, 92) + """
  <ellipse cx="100" cy="104" rx="60" ry="52" fill="url(#tw-sauce)" opacity=".4"/>
  <g>
    <g transform="rotate(-24 78 88)"><rect x="50" y="72" width="60" height="34" rx="10" fill="url(#tw-pork)"/><rect x="50" y="72" width="60" height="9" rx="4" fill="#F1DDB8"/><rect x="54" y="88" width="52" height="4" rx="2" fill="#E9B98A" opacity=".7"/></g>
    <g transform="rotate(-8 104 96)"><rect x="76" y="82" width="60" height="34" rx="10" fill="url(#tw-pork)"/><rect x="76" y="82" width="60" height="9" rx="4" fill="#F1DDB8"/><rect x="80" y="98" width="52" height="4" rx="2" fill="#E9B98A" opacity=".7"/></g>
    <g transform="rotate(10 122 108)"><rect x="94" y="96" width="60" height="34" rx="10" fill="url(#tw-pork)"/><rect x="94" y="96" width="60" height="9" rx="4" fill="#F1DDB8"/><rect x="98" y="112" width="52" height="4" rx="2" fill="#E9B98A" opacity=".7"/></g>
    <g fill="url(#tw-leaf)"><path d="M56 132 c-10 -18 0 -34 16 -38 c8 16 0 32 -16 38Z"/><path d="M74 146 c-6 -18 6 -32 22 -32 c4 16 -6 30 -22 32Z"/></g>
    <g fill="#FFFFFF" opacity=".55"><ellipse cx="96" cy="88" rx="12" ry="3"/><ellipse cx="124" cy="104" rx="10" ry="3"/></g>
  </g>"""),

    "dish-soup.svg": ("0 0 240 200", "湯：雙耳白瓷湯鍋裡的蘿蔔排骨湯（俯視）", """
  <circle cx="124" cy="106" r="92" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <rect x="8" y="84" width="44" height="32" rx="14" fill="url(#tw-china)"/><rect x="188" y="84" width="44" height="32" rx="14" fill="url(#tw-china)"/>
  <circle cx="120" cy="100" r="92" fill="url(#tw-china)"/>
  <circle cx="120" cy="100" r="80" fill="#CFC8BA"/>
  <circle cx="120" cy="100" r="76" fill="url(#tw-broth)"/>
  <path d="M56 70 A80 80 0 0 1 140 26" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" opacity=".9"/>
  <g>
    <g fill="#FBF8F1"><rect x="72" y="70" width="34" height="30" rx="9" transform="rotate(-14 89 85)"/><rect x="124" y="110" width="34" height="30" rx="9" transform="rotate(18 141 125)"/><rect x="84" y="118" width="30" height="26" rx="8" transform="rotate(-6 99 131)"/></g>
    <g fill="#EDE6D2" opacity=".8"><rect x="76" y="74" width="26" height="8" rx="4" transform="rotate(-14 89 85)"/><rect x="128" y="114" width="26" height="8" rx="4" transform="rotate(18 141 125)"/></g>
    <g fill="#B5795A"><rect x="118" y="60" width="44" height="26" rx="11" transform="rotate(24 140 73)"/><rect x="58" y="100" width="40" height="24" rx="10" transform="rotate(-30 78 112)"/></g>
    <g fill="#F1DDB8"><circle cx="126" cy="66" r="7"/><circle cx="62" cy="120" r="6"/></g>
    <g fill="none" stroke="#6FA85A" stroke-width="3"><circle cx="110" cy="100" r="4"/><circle cx="150" cy="96" r="4"/><circle cx="96" cy="60" r="3.5"/><circle cx="134" cy="150" r="3.5"/></g>
    <g fill="#F29A3A"><circle cx="160" cy="80" r="8"/><circle cx="74" cy="142" r="7"/></g>
  </g>
  <path d="M70 76 A64 64 0 0 1 130 40" fill="none" stroke="#FFF8E6" stroke-width="5" stroke-linecap="round" opacity=".5"/>"""),

    "dish-other.svg": ("0 0 140 140", "其他：小碟裡的涼拌小菜（俯視）", plate(70, 70, 60, 0.7) + """
  <g><g fill="#E9D9A0"><ellipse cx="58" cy="62" rx="14" ry="9" transform="rotate(-20 58 62)"/><ellipse cx="82" cy="68" rx="14" ry="9" transform="rotate(24 82 68)"/><ellipse cx="68" cy="84" rx="13" ry="8"/></g>
  <g fill="#D9472B"><circle cx="72" cy="58" r="3"/><circle cx="60" cy="78" r="2.6"/></g><g fill="#4E8A45"><circle cx="84" cy="80" r="3"/><circle cx="66" cy="68" r="2.6"/></g></g>"""),

    "rice-bowl.svg": ("0 0 120 120", "個人飯碗（有值）：一碗白飯（俯視）", f"""
  <circle cx="63" cy="65" r="54" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <circle cx="60" cy="60" r="54" fill="url(#tw-china)"/>
  <circle cx="60" cy="60" r="54" fill="none" stroke="#3F6FC2" stroke-width="3" opacity=".7"/>
  <circle cx="60" cy="60" r="46" fill="#CFC8BA"/>
  <circle cx="60" cy="60" r="43" fill="url(#tw-rice)"/>
  <circle cx="60" cy="60" r="43" fill="#000" filter="url(#tw-grain)" opacity=".3"/>
  <g transform="translate(-40 -40) scale(1)">{RICE_GRAINS}</g>
  <path d="M22 42 A46 46 0 0 1 72 12" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" opacity=".9"/>"""),

    "rice-bowl-unknown.svg": ("0 0 120 120", "個人飯碗（未知）：空碗，不畫食物。虛線外框由元件的 CSS 加上", """
  <circle cx="63" cy="65" r="54" fill="#0E0A14" opacity=".4" filter="url(#tw-b6)"/>
  <circle cx="60" cy="60" r="54" fill="url(#tw-china)" opacity=".92"/>
  <circle cx="60" cy="60" r="46" fill="#CFC8BA"/>
  <circle cx="60" cy="60" r="43" fill="url(#tw-well)"/>
  <path d="M30 40 A38 38 0 0 1 70 22 A46 46 0 0 0 30 40Z" fill="#3A2A20" opacity=".16"/>
  <path d="M22 42 A46 46 0 0 1 72 12" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" opacity=".9"/>"""),

    "place-setting.svg": ("0 0 220 140", "個人餐墊：亞麻餐墊、筷子與筷架、湯匙。飯碗疊在左側圓位（俯視，座位朝下方）", """
  <rect x="10" y="12" width="204" height="124" rx="12" fill="#0E0A14" opacity=".4" filter="url(#tw-b3)"/>
  <rect x="6" y="6" width="204" height="124" rx="12" fill="url(#tw-linen)"/>
  <rect x="6" y="6" width="204" height="124" rx="12" fill="#000" filter="url(#tw-grain)" opacity=".4"/>
  <rect x="14" y="14" width="188" height="108" rx="7" fill="none" stroke="#B9AD96" stroke-width="1.5" stroke-dasharray="5 5"/>
  <g>
    <rect x="150" y="96" width="34" height="12" rx="5" fill="#0E0A14" opacity=".4" filter="url(#tw-b3)" transform="translate(2 3)"/>
    <rect x="150" y="96" width="34" height="12" rx="5" fill="#FBF8F1"/>
    <rect x="158" y="18" width="6" height="96" rx="3" fill="#3E2412"/><rect x="170" y="18" width="6" height="96" rx="3" fill="#4A2C16"/>
    <rect x="158" y="18" width="6" height="20" rx="3" fill="#D9472B"/><rect x="170" y="18" width="6" height="20" rx="3" fill="#D9472B"/>
  </g>
  <g>
    <rect x="190" y="52" width="9" height="62" rx="4" fill="url(#tw-steel)"/><ellipse cx="194.500" cy="38" rx="11" ry="18" fill="url(#tw-steel)"/><ellipse cx="192" cy="34" rx="4" ry="8" fill="#FFFFFF" opacity=".6"/>
  </g>"""),

    "chair-top.svg": ("0 0 200 180", "餐椅（俯視，椅背在下方、面向上方的桌子）", """
  <rect x="24" y="22" width="160" height="150" rx="26" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <rect x="16" y="10" width="168" height="130" rx="26" fill="url(#tw-walnut)"/>
  <rect x="16" y="10" width="168" height="130" rx="26" fill="#000" filter="url(#tw-wood)"/>
  <rect x="28" y="20" width="144" height="108" rx="20" fill="url(#tw-seat)"/>
  <rect x="28" y="20" width="144" height="108" rx="20" fill="#000" filter="url(#tw-grain)" opacity=".35"/>
  <path d="M40 32 q60 -10 120 0" fill="none" stroke="#A9C49C" stroke-width="3" opacity=".5" stroke-linecap="round"/>
  <rect x="10" y="136" width="180" height="30" rx="14" fill="url(#tw-walnut)"/>
  <rect x="10" y="136" width="180" height="30" rx="14" fill="#000" filter="url(#tw-wood)"/>
  <rect x="14" y="138" width="172" height="5" rx="2.5" fill="#C39A66" opacity=".7"/>"""),

    "tip-tray.svg": ("0 0 180 130", "營養小知識：胡桃木小卡盤與一疊知識卡（俯視）。每張卡＝一樣食材的小知識；則數與內容由 HTML 疊上", """
  <rect x="12" y="16" width="164" height="110" rx="14" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <rect x="6" y="6" width="164" height="110" rx="14" fill="url(#tw-walnut)"/>
  <rect x="6" y="6" width="164" height="110" rx="14" fill="#000" filter="url(#tw-wood)"/>
  <path d="M6 102 V20 q0 -14 14 -14 H156" fill="none" stroke="#C39A66" stroke-width="2.5" opacity=".8"/>
  <rect x="18" y="18" width="140" height="86" rx="8" fill="#3E2A18"/>
  <rect x="18" y="18" width="140" height="12" rx="6" fill="#17121C" opacity=".4" filter="url(#tw-b3)"/>
  <g>
    <rect x="30" y="30" width="116" height="68" rx="4" fill="#D9CFBC" transform="rotate(4 88 64)"/>
    <rect x="30" y="28" width="116" height="68" rx="4" fill="#E9E0CE" transform="rotate(-3 88 62)"/>
    <rect x="30" y="26" width="116" height="68" rx="4" fill="url(#tw-paper)"/>
    <rect x="30" y="26" width="116" height="16" rx="4" fill="#3F7D4E"/>
    <rect x="30" y="38" width="116" height="4" fill="#3F7D4E"/>
    <circle cx="44" cy="34" r="5" fill="#FFD27A"/><path d="M41 40 h6 v3 h-6Z" fill="#FFD27A"/>
    <g stroke="#B9AD96" stroke-width="2.5" stroke-linecap="round"><path d="M40 54 H136 M40 64 H128 M40 74 H134 M40 84 H96"/></g>
  </g>"""),

    "tip-tray-empty.svg": ("0 0 180 130", "營養小知識（暫時沒有）：空的小卡盤。狀態說明由 HTML 疊上", """
  <rect x="12" y="16" width="164" height="110" rx="14" fill="#0E0A14" opacity=".5" filter="url(#tw-b6)"/>
  <rect x="6" y="6" width="164" height="110" rx="14" fill="url(#tw-walnut)"/>
  <rect x="6" y="6" width="164" height="110" rx="14" fill="#000" filter="url(#tw-wood)"/>
  <path d="M6 102 V20 q0 -14 14 -14 H156" fill="none" stroke="#C39A66" stroke-width="2.5" opacity=".8"/>
  <rect x="18" y="18" width="140" height="86" rx="8" fill="#3E2A18"/>
  <rect x="18" y="18" width="140" height="12" rx="6" fill="#17121C" opacity=".4" filter="url(#tw-b3)"/>
  <rect x="34" y="30" width="108" height="62" rx="4" fill="none" stroke="#9C8E83" stroke-width="2" stroke-dasharray="6 5" opacity=".8"/>"""),

    "receipt.svg": ("0 0 150 230", "超市收據：熱感紙、鋸齒下緣、微捲。金額與預算章由 HTML 疊上", """
  <path d="M16 12 H142 V214 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8Z" fill="#0E0A14" opacity=".45" filter="url(#tw-b6)"/>
  <path d="M8 4 H134 V206 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8 l-9 -8 l-9 8Z" fill="url(#tw-paper)"/>
  <rect x="8" y="4" width="126" height="10" fill="#17121C" opacity=".06"/>
  <path d="M8 4 V198" stroke="#FFFFFF" stroke-width="2"/><path d="M134 4 V198" stroke="#B9AD96" stroke-width="1"/>
  <g stroke="#B9AD96" stroke-width="1" stroke-dasharray="3 3"><path d="M20 44 H122 M20 132 H122"/></g>
  <g fill="#3A2A20" opacity=".8"><rect x="34" y="176" width="2" height="18"/><rect x="39" y="176" width="4" height="18"/><rect x="46" y="176" width="2" height="18"/><rect x="51" y="176" width="3" height="18"/><rect x="58" y="176" width="2" height="18"/><rect x="63" y="176" width="5" height="18"/><rect x="71" y="176" width="2" height="18"/><rect x="76" y="176" width="3" height="18"/><rect x="83" y="176" width="2" height="18"/><rect x="88" y="176" width="4" height="18"/><rect x="95" y="176" width="2" height="18"/><rect x="100" y="176" width="3" height="18"/><rect x="106" y="176" width="2" height="18"/></g>"""),
}


def main() -> None:
    for name, (vb, comment, body) in SPRITES.items():
        (HERE / name).write_text(svg(vb, comment, body), encoding="utf-8")
    print("wrote", ", ".join(sorted(SPRITES)))


if __name__ == "__main__":
    main()
