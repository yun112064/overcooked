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


target = "紅蘿蔔"

matches = [
    item
    for item in ingredients
    if target in item.get(
        "aliases",
        []
    )
]


print(
    "符合筆數：",
    len(matches)
)


for item in matches:

    print(
        item["ingredient_id"],
        item["standard_name"],
        item["aliases"]
    )