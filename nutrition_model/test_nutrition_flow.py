from nutrition_service import (
    calculate_ingredient_nutrition_with_cooking
)

import json

from nutrition_service import calculate_nutrition

raw_test = calculate_nutrition(
    name="雞胸肉",
    weight_g=150
)

print("TFDA raw test:")
print(raw_test)

result = (
    calculate_ingredient_nutrition_with_cooking(
        name="雞胸肉",
        raw_weight_g=150,
        cooking_method="烤"
    )
)


print(
    json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )
)