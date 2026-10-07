from usda_cooked_nutrition_service import (
    find_usda_cooked_nutrition
)

import json


result = find_usda_cooked_nutrition(
    ingredient_name="雞胸肉",
    cooking_method="烤"
)


print(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )
)