from cooking_yield_service import (
    find_cooking_yield_by_ingredient,
    calculate_cooked_weight
)


ingredient_name = "雞胸肉"
raw_weight_g = 150
cooking_method = "烤"


# 自動找 USDA Cooking Yield
result = find_cooking_yield_by_ingredient(
    ingredient_name=ingredient_name,
    cooking_method=cooking_method
)


print("查詢結果：")
print(result)


if result["success"]:

    cooking_yield_pct = result[
        "cooking_yield_pct"
    ]

    cooked_weight_g = calculate_cooked_weight(
        raw_weight_g=raw_weight_g,
        cooking_yield_pct=cooking_yield_pct
    )

    print()
    print("食材：", ingredient_name)
    print("生重：", raw_weight_g, "g")
    print("料理方式：", cooking_method)
    print(
        "USDA Cooking Yield：",
        cooking_yield_pct,
        "%"
    )
    print(
        "熟後重量：",
        cooked_weight_g,
        "g"
    )

else:

    print()
    print(
        "查詢失敗：",
        result["error"]
    )

    # 如果找到很多候選
    if "candidates" in result:

        print("\n候選資料：")

        for candidate in result["candidates"]:

            print(
                candidate[
                    "yield_description"
                ],
                "/",
                candidate[
                    "preparation_method"
                ],
                "/",
                candidate[
                    "cooking_yield_pct"
                ],
                "%"
            )