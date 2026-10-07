import json
from pathlib import Path


path = (
    Path(__file__).parent
    / "data"
    / "ingredient_master.json"
)

with open(
    path,
    "r",
    encoding="utf-8"
) as f:
    ingredients = json.load(f)


for item in ingredients:

    name = item.get(
        "standard_name",
        ""
    )

    aliases = item.get(
        "aliases",
        []
    )

    text = (
        name
        + " "
        + " ".join(
            aliases
        )
    )

    if "雞胸" in text:

        print(
            json.dumps(
                item,
                ensure_ascii=False,
                indent=2
            )
        )