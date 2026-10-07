from nutrition_service import (
    calculate_cooked_nutrition
)

import json


result = calculate_cooked_nutrition(
    name="雞胸肉",
    raw_weight_g=150,
    cooking_method="烤"
)


print(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )
)