"""決策 #41：食譜步驟中的食材分組代稱（[調味]、[醃料]…）在顯示時展開成該分組的實際食材。

分組來自 recipe_ingredients.raw_text 開頭的 [X] 標記；只做展開標註，不改寫原文其他字句，不用 LLM。
"""
import unittest

from app.services.recipe_steps import expand_steps, ingredient_group

# R0118 空心菜沙茶牛肉（真實工作簿的 raw_text 與步驟）
R0118_RAW = ["牛肉片or火鍋肉片 適量", "空心菜 1把", "[調味]沙茶醬 1匙", "[調味]醬油 1匙", "[調味]糖or味琳 1匙",
             "[配料]蒜", "蔥", "辣椒 適量", "[醃料]米酒", "水 各1匙", "[醃料]蠔油 1小匙",
             "[醃料]沙茶醬與沙茶的油 各1匙", "[醃料]沙茶醬與沙茶的油 各1匙", "[醃料]太白粉 2匙"]
R0118_STEPS = ["肉先用[醃料]醃約5分鐘，先把[調味]在碗事先調好。 起油鍋下香油將牛肉炒6分熟",
               "接著下所有[調味]與[配料]快炒即可",
               "簡單炒出沙茶牛肉，不要把它想的太難，上手後料理超簡單^^"]


class IngredientGroupTests(unittest.TestCase):
    def test_leading_bracket_marker_gives_group_and_name_without_quantity(self):
        self.assertEqual(ingredient_group("[調味]糖or味琳 1匙"), ("調味", "糖or味琳"))
        self.assertEqual(ingredient_group("[配料]蒜"), ("配料", "蒜"))
        self.assertEqual(ingredient_group("[調味料] 雞粉 鹽 黑胡椒粉 適量"), ("調味料", "雞粉 鹽 黑胡椒粉"))

    def test_marker_variants_are_normalized(self):
        self.assertEqual(ingredient_group("[醃 料 ] 醬油 1大匙"), ("醃料", "醬油"))
        self.assertEqual(ingredient_group("[【調味】] 香油 適量"), ("調味", "香油"))
        self.assertEqual(ingredient_group("(調味料)八角 2個"), ("調味料", "八角"))

    def test_no_marker_has_no_group(self):
        self.assertEqual(ingredient_group("空心菜 1把"), (None, "空心菜"))
        self.assertEqual(ingredient_group(None), (None, None))


class ExpandStepsTests(unittest.TestCase):
    def test_r0118_bracketed_groups_are_expanded_with_ingredient_names(self):
        shown = expand_steps(R0118_STEPS, R0118_RAW)
        self.assertEqual(shown[0], "肉先用醃料（米酒、蠔油、沙茶醬與沙茶的油、太白粉）醃約5分鐘，"
                                   "先把調味（沙茶醬、醬油、糖or味琳）在碗事先調好。 起油鍋下香油將牛肉炒6分熟")
        self.assertEqual(shown[1], "接著下所有調味（沙茶醬、醬油、糖or味琳）與配料（蒜）快炒即可")
        self.assertEqual(shown[2], R0118_STEPS[2])  # 沒有代稱的步驟不動

    def test_step_number_prefix_is_removed_for_display(self):
        # 1027 版原文自帶「步驟一：」，畫面已用有序清單編號
        self.assertEqual(expand_steps(["步驟一：熱鍋下油。", "步驟十二: 盛盤"], ["空心菜 1把"]), ["熱鍋下油。", "盛盤"])
        self.assertEqual(expand_steps(["步驟二：下所有[調味]快炒"], ["[調味]醬油 1匙"]), ["下所有調味（醬油）快炒"])
        self.assertEqual(expand_steps(["依步驟一調好醬汁"], []), ["依步驟一調好醬汁"])  # 句中的「步驟」不動

    def test_original_list_is_not_modified(self):
        steps = list(R0118_STEPS)
        expand_steps(steps, R0118_RAW)
        self.assertEqual(steps, R0118_STEPS)

    def test_unbracketed_group_word_expands_when_the_recipe_has_that_group(self):
        raw = ["[調味]醬油 2匙", "[調味]冰糖 1匙", "洋蔥 1顆"]
        self.assertEqual(expand_steps(["放入所有調味料，以小火煮約2分鐘"], raw),
                         ["放入所有調味料（醬油、冰糖），以小火煮約2分鐘"])
        # 「調味」單獨出現是動詞用法，不展開
        self.assertEqual(expand_steps(["起鍋前調味上桌"], raw), ["起鍋前調味上桌"])

    def test_group_words_are_left_alone_without_matching_groups(self):
        raw = ["醬油 2匙", "洋蔥 1顆"]
        steps = ["加入所有食材和調味料拌勻。", "肉絲用醃料略醃10分鐘", "[食材 ]切好"]
        self.assertEqual(expand_steps(steps, raw), steps)

    def test_longer_group_names_win_and_full_width_brackets_are_recognised(self):
        raw = ["[牛肉醃料] 醬油 1大匙", "[調味料] 鹽 少許", "[調味料]糖 1小匙"]
        self.assertEqual(expand_steps(["牛肉加入[牛肉醃料]拌勻", "再加入蔥末及【調味料】拌勻即可"], raw),
                         ["牛肉加入牛肉醃料（醬油）拌勻", "再加入蔥末及調味料（鹽、糖）拌勻即可"])

    def test_each_group_is_expanded_only_once_per_step(self):
        raw = ["[醃料] 鹽 少許", "[醃料] 米酒 1大匙"]
        self.assertEqual(expand_steps(["將醃料抓勻，讓雞胸肉吸收醃料後，靜置"], raw),
                         ["將醃料（鹽、米酒）抓勻，讓雞胸肉吸收醃料後，靜置"])

    def test_numbered_groups_expand_bare(self):
        raw = ["[調味料1] 鹽 少許", "[調味料2] 醬油 1大匙"]
        self.assertEqual(expand_steps(["雞丁以調味料1抓醃", "放進雞丁.調味料2.1/2碗的水"], raw),
                         ["雞丁以調味料1（鹽）抓醃", "放進雞丁.調味料2（醬油）.1/2碗的水"])


if __name__ == "__main__":
    unittest.main()
