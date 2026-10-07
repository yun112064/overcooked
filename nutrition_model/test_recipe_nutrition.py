from nutrition_service import (
    calculate_recipe_nutrition
)

import json


recipe = {
    "recipe_name":
        "烤雞胸佐胡蘿蔔",

    "ingredients": [
        {
            "name":
                "雞胸肉",

            "weight_g":
                150,

            "cooking_method":
                "烤"
        },

        {
            "name":
                "紅蘿蔔",

            "weight_g":
                80,

            "cooking_method":
                "水煮"
        }
    ]
}


result = calculate_recipe_nutrition(
    recipe_name=
        recipe["recipe_name"],

    recipe_ingredients=
        recipe["ingredients"]
)


print(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )
)