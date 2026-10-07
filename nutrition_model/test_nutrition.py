from nutrition_service import (
    calculate_nutrition
)

import json


result = calculate_nutrition(
    name="雞胸肉",
    weight_g=150
)


print(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )
)