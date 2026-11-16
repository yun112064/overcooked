"""Generate the crayon people for the family portrait.

Usage (from the repository root):  python frontend-3d/src/assets/family/build_crayon.py

Each household member is one crayon figure standing on the grass in the framed drawing.
The figures are deliberately child-like (wobbly outlines, uneven fill) and are the one
place besides the mascot where the art is not realistic.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFS = """
  <defs>
    <filter id="cr-wobble" x="-10%" y="-10%" width="120%" height="120%">
      <feTurbulence type="fractalNoise" baseFrequency=".06" numOctaves="2" seed="{seed}" result="n"/>
      <feDisplacementMap in="SourceGraphic" in2="n" scale="7"/>
    </filter>
    <pattern id="cr-fill" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)">
      <rect width="9" height="9" fill="{body}" opacity=".5"/><rect width="9" height="5" fill="{body}"/>
    </pattern>
  </defs>
"""

FACE = (
    '<circle cx="50" cy="42" r="3.4" fill="#3A2A20"/><circle cx="70" cy="42" r="3.4" fill="#3A2A20"/>'
    '<path d="M48 54 q12 12 24 0" stroke="#D9472B" stroke-width="4"/>'
    '<circle cx="42" cy="52" r="5" fill="#F4A8A0" opacity=".7" stroke="none"/><circle cx="78" cy="52" r="5" fill="#F4A8A0" opacity=".7" stroke="none"/>'
)


def figure(kind: str, body: str, seed: int) -> str:
    skin = '<circle cx="60" cy="44" r="27" fill="#FFE2C8" stroke="#E0705A" stroke-width="5"/>'
    limbs = "#3A2A20"
    if kind == "man":
        hair = '<path d="M34 36 q8 -26 30 -24 q22 2 24 24" stroke="#3A2A20" stroke-width="8"/>'
        extra = '<path d="M42 42 h14 M64 42 h14 M56 42 h8" stroke="#3A2A20" stroke-width="3"/><rect x="41" y="36" width="15" height="12" rx="4" stroke="#3A2A20" stroke-width="3"/><rect x="64" y="36" width="15" height="12" rx="4" stroke="#3A2A20" stroke-width="3"/>'
        torso = '<path d="M38 78 h44 v62 h-44Z" fill="url(#cr-fill)" stroke="{b}" stroke-width="6"/>'
        legs = f'<path d="M50 140 v44 M70 140 v44 M42 186 h14 M64 186 h14" stroke="{limbs}" stroke-width="6"/>'
        arms = f'<path d="M38 88 l-20 34 M82 88 l20 34" stroke="{limbs}" stroke-width="6"/>'
    elif kind == "woman":
        hair = '<path d="M30 60 q-6 -46 30 -46 q36 0 30 46" stroke="#5A3D24" stroke-width="9"/><path d="M36 32 q24 -14 48 0" stroke="#5A3D24" stroke-width="8"/>'
        extra = ""
        torso = '<path d="M60 76 l-34 70 h68Z" fill="url(#cr-fill)" stroke="{b}" stroke-width="6"/>'
        legs = f'<path d="M50 146 v38 M70 146 v38 M42 186 h14 M64 186 h14" stroke="{limbs}" stroke-width="6"/>'
        arms = f'<path d="M48 96 l-26 26 M72 96 l26 26" stroke="{limbs}" stroke-width="6"/>'
    elif kind == "kid":
        hair = '<circle cx="30" cy="26" r="11" fill="#F2B32E" stroke="#C9952A" stroke-width="4"/><circle cx="90" cy="26" r="11" fill="#F2B32E" stroke="#C9952A" stroke-width="4"/><path d="M36 30 q24 -18 48 0" stroke="#3A2A20" stroke-width="8"/>'
        extra = ""
        torso = '<path d="M40 78 h40 v46 h-40Z" fill="url(#cr-fill)" stroke="{b}" stroke-width="6"/>'
        legs = f'<path d="M50 124 v40 M70 124 v40 M42 166 h14 M64 166 h14" stroke="{limbs}" stroke-width="6"/>'
        arms = f'<path d="M40 86 l-22 -16 M80 86 l22 -16" stroke="{limbs}" stroke-width="6"/>'
    else:  # elder
        hair = '<path d="M32 44 q-4 -34 28 -34 q32 0 28 34" stroke="#A9A9B4" stroke-width="9"/><circle cx="60" cy="8" r="9" fill="#C9C9D2" stroke="#A9A9B4" stroke-width="4"/>'
        extra = '<path d="M44 34 q6 -4 12 0 M64 34 q6 -4 12 0" stroke="#A9A9B4" stroke-width="3"/>'
        torso = '<path d="M60 76 l-30 68 h60Z" fill="url(#cr-fill)" stroke="{b}" stroke-width="6"/>'
        legs = f'<path d="M50 144 v40 M70 144 v40 M42 186 h14 M64 186 h14" stroke="{limbs}" stroke-width="6"/>'
        arms = f'<path d="M48 98 l-22 26 M72 98 l24 24 M98 120 v66 M90 122 q8 -8 16 0" stroke="{limbs}" stroke-width="6"/>'
    torso = torso.replace("{b}", body)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 200">\n  <!-- 蠟筆人物：{kind} -->'
        + DEFS.format(seed=seed, body=body)
        + f'  <g filter="url(#cr-wobble)" fill="none" stroke-linecap="round" stroke-linejoin="round">\n    {legs}{arms}{torso}{skin}{hair}{extra}{FACE}\n  </g>\n</svg>\n'
    )


ADD = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 200">
  <!-- 蠟筆人物：還沒畫上的新成員（鉛筆虛線輪廓＋加號） -->
  <g fill="none" stroke="#9C8E83" stroke-width="4" stroke-linecap="round" stroke-dasharray="7 9">
    <circle cx="60" cy="44" r="27"/><path d="M38 78 h44 v62 h-44Z M50 140 v44 M70 140 v44 M38 88 l-20 34 M82 88 l20 34"/>
  </g>
  <path d="M60 30 v28 M46 44 h28" stroke="#9C8E83" stroke-width="6" stroke-linecap="round"/>
</svg>
"""


def main() -> None:
    kinds = [("man", "#3F6FC2", 3), ("woman", "#E0483A", 5), ("kid", "#F2B32E", 8), ("elder", "#9A6CC2", 11)]
    for kind, body, seed in kinds:
        (HERE / f"crayon-{kind}.svg").write_text(figure(kind, body, seed), encoding="utf-8")
    (HERE / "crayon-add.svg").write_text(ADD, encoding="utf-8")
    print("wrote", ", ".join(sorted(p.name for p in HERE.glob("crayon-*.svg"))))


if __name__ == "__main__":
    main()
