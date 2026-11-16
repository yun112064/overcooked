import { Typography } from '@mui/material'
import { GRAMS_PER_BOWL, rice } from '../api/nutrition'
import type { NutritionPreview, RecipeSummary } from '../api/types'
import { DishIngredients, DishSteps } from './DishDetail'
import RecipeBook, { type RecipeEntry } from './RecipeBook'

type Dish = NonNullable<NutritionPreview['dishes']>[number]

/** 預覽與已採用紀錄共用後端的整餐用量，不把原食譜量當成調整後的配方。 */
export default function DishList({ selected, dishes, staple, stapleName }: {
  selected: RecipeSummary[]; dishes: Dish[] | undefined; staple: NutritionPreview['staple']; stapleName: string | null
}) {
  const recipes: RecipeEntry[] = selected.map(d => {
    const dish = dishes?.find(item => item.recipe_id === d.recipe_id)
    return {
      id: d.recipe_id, name: d.recipe_name, category: d.category, specified: d.specified,
      note: dish && !dish.adjusted
        ? <Typography variant="caption">原食譜用量（營養計算未通過，未依目標調整）</Typography> : undefined,
      ingredients: dish?.ingredients.length
        ? <DishIngredients ingredients={dish.ingredients} />
        : <Typography variant="body2" color="text.secondary">尚未取得食材用量。</Typography>,
      steps: dish?.steps.length ? <DishSteps steps={dish.steps} /> : undefined,
    }
  })
  if (staple) recipes.push({
    id: 'staple', name: staple.name ?? stapleName ?? '主食', category: '主食',
    ingredients: <>
      {staple.final_grams && <Typography variant="body2">{rice(staple.final_grams)}</Typography>}
      <Typography variant="caption">一碗 {GRAMS_PER_BOWL} 克；計入營養驗證</Typography>
    </>,
  })
  return <RecipeBook recipes={recipes} />
}
