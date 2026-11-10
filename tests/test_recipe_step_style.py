from app.services.recipe_step_style import STYLE_VERSION, check_recipe_steps


def test_only_changes_wording_and_matching_display_numbers():
    source = [
        (1, "本版備料以食材表採值為準。步驟一：鍋中加入2大匙油，用中小火加熱。然後加進番茄，煮1–2分鐘。"),
        (2, "步驟２：把雞蛋放進鍋中，煎到金黃後把火關掉。"),
        (3, "倒入醬汁，轉中火收汁。"),
    ]
    result = check_recipe_steps(source)
    assert result["style_version"] == STYLE_VERSION
    assert result["checked_count"] == 3
    assert [step["suggestion"] for step in result["findings"]] == [
        "本版備料以食材表採值為準。鍋中加入2大匙油，用中小火加熱。接著加入番茄，煮1–2分鐘。",
        "把雞蛋放入鍋中，煎到金黃後關火。",
    ]
    assert result["findings"][0]["original"] == source[0][1]
    assert check_recipe_steps((row["step_no"], row["suggestion"]) for row in result["findings"])["findings"] == []


def test_does_not_guess_container_references_or_reorder_steps():
    source = [
        (1, "Ａ號湯鍋放入高湯。"),
        (2, "將炒好的食材倒入剛才那鍋，續煮 7 分鐘。"),
        (3, "步驟四：將蛋放進鍋中。"),
        (4, "1. 切蒜。2. 放入鍋中。"),
        (5, "另起一鍋，放入蔬菜。"),
        (6, "鍋具：Ｂ 加水。"),
    ]
    result = check_recipe_steps(source)
    assert [row["step_no"] for row in result["findings"]] == [1, 2, 3, 4, 6]
    assert all(row["status"] == "needs_review" and row["suggestion"] is None for row in result["findings"])
