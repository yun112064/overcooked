/** 全家配方：每道菜的食材依「主食材／佐料／調味」分組成兩欄表格（食材名靠左、用量靠右），下接步驟。
 *  結果頁與已採用紀錄共用。分組由後端依調味料清單與佐料清單判定。 */
import { Box, Chip, Stack, Typography } from '@mui/material'
import { GRAMS_PER_BOWL, rice } from '../api/nutrition'
import CookingQuantity from './CookingQuantity'
import type { NutritionPreview, RecipeSummary } from '../api/types'
import { tokens } from '../theme'
import { DishSteps } from './DishDetail'

type Dish = NonNullable<NutritionPreview['dishes']>[number]
type Ingredient = Dish['ingredients'][number]

/** 分組順序與標題列底色（沿用主題的淡色，不另用新色） */
const GROUPS = [
  { key: '主食材', bg: tokens.sand },
  { key: '佐料', bg: tokens.matchaTint },
  { key: '調味', bg: tokens.indigoTint },
] as const

function IngredientTable({ ingredients }: { ingredients: Ingredient[] }) {
  const grouped = GROUPS.map((g) => ({ ...g, items: ingredients.filter((i) => (i.group ?? '主食材') === g.key) }))
    .filter((g) => g.items.length > 0)
  return (
    <Box sx={{ border: `1px solid ${tokens.line}`, borderRadius: 1, overflow: 'hidden', bgcolor: '#FFFDF9', my: 1 }}>
      {grouped.map((g, gi) => (
        <Box key={g.key} sx={{ borderTop: gi > 0 ? `1px solid ${tokens.line}` : 'none' }}>
          <Typography variant="body2" sx={{ bgcolor: g.bg, px: 2, py: 0.75, color: tokens.inkSoft, letterSpacing: '0.12em', fontWeight: 500 }}>
            {g.key}
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))' }, columnGap: 4, px: 2, py: 1 }}>
            {g.items.map((i, k) => (
              <Box key={`${i.name}-${k}`} sx={{ display: 'flex', justifyContent: 'space-between', gap: 2, py: 0.75,
                borderBottom: `1px dashed ${tokens.line}` }}>
                <Typography variant="body2">{i.name}</Typography>
                <Typography variant="body2" component="div" sx={{ color: tokens.inkSoft, whiteSpace: 'nowrap' }}><CookingQuantity ingredient={i} name={i.name} /></Typography>
              </Box>
            ))}
          </Box>
        </Box>
      ))}
    </Box>
  )
}

export default function DishList({ selected, dishes, staple, stapleName }: {
  selected: RecipeSummary[]; dishes: Dish[] | undefined; staple: NutritionPreview['staple']; stapleName: string | null
}) {
  return (
    <Stack spacing={3} divider={<Box sx={{ borderTop: `1px solid ${tokens.line}` }} />}>
      {selected.map((d, idx) => {
        const dish = dishes?.find((x) => x.recipe_id === d.recipe_id)
        return (
          <Box key={d.recipe_id} sx={{ display: 'flex', gap: 2 }}>
            <Typography sx={{ fontFamily: tokens.serif, color: tokens.brick, fontSize: '0.95rem', pt: 0.4, width: 22, flexShrink: 0 }}>
              {String(idx + 1).padStart(2, '0')}
            </Typography>
            <Box sx={{ minWidth: 0, flexGrow: 1 }}>
              <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
                <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>{d.recipe_name}</Typography>
                {d.category && <Typography variant="caption">{d.category}</Typography>}
                {d.specified && <Chip size="small" variant="outlined" color="primary" label="指定菜" />}
              </Box>
              {dish && !dish.adjusted && (
                <Typography variant="caption" component="p">原食譜用量（營養計算未通過，未依目標調整）</Typography>
              )}
              {dish && dish.ingredients.length > 0 && <IngredientTable ingredients={dish.ingredients} />}
              {dish && <DishSteps steps={dish.steps} />}
            </Box>
          </Box>
        )
      })}
      {staple && (
        <Box sx={{ display: 'flex', gap: 2 }}>
          <Typography sx={{ fontFamily: tokens.serif, color: tokens.inkMuted, fontSize: '0.95rem', pt: 0.4, width: 22, flexShrink: 0 }}>主</Typography>
          <Box>
            <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>{staple.name ?? stapleName}</Typography>
            {staple.final_grams && <Typography variant="body2">{rice(staple.final_grams)}</Typography>}
            <Typography variant="caption">一碗 {GRAMS_PER_BOWL} 克；計入營養驗證</Typography>
          </Box>
        </Box>
      )}
    </Stack>
  )
}
