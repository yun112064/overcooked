import { Box, Chip, Tooltip, Typography } from '@mui/material'
import CookingQuantity from './CookingQuantity'
import { cleanSteps } from './recipeSteps'

type Ingredient = { name: string; group?: string | null } & Parameters<typeof CookingQuantity>[0]['ingredient']

/** 每日鈉上限（mg），與 config/daily_reference_nutrients.json 一致；只用於提示文字 */
const SODIUM_DAILY_MAX = 2400

/** 高鈉提醒：每人份鈉達門檻的菜加上標籤（只提醒，不影響採用）。 */
export function HighSodiumTag({ high, sodium }: { high?: boolean | null; sodium?: string | null }) {
  if (!high || sodium == null) return null
  const mg = Number(sodium)
  return (
    <Tooltip title={`每人份約 ${Math.round(mg)} mg 鈉，約占每日上限 ${SODIUM_DAILY_MAX} mg 的 ${Math.round((mg / SODIUM_DAILY_MAX) * 100)}%`}>
      <Chip size="small" color="warning" variant="outlined" label={`高鈉・${Math.round(mg)} mg`} sx={{ cursor: 'help' }} />
    </Tooltip>
  )
}

/** 食材清單：沒有分組的先列，其後依食譜原文的分組（decisions #41，如「調味」「醃料」）加小標。 */
export function DishIngredients({ ingredients }: { ingredients: Ingredient[] }) {
  const groups = new Map<string, Ingredient[]>()
  for (const i of ingredients) {
    const key = i.group ?? ''
    groups.set(key, [...(groups.get(key) ?? []), i])
  }
  const ordered = [...groups.entries()].sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : 0))
  return (
    <Box className="live-recipe-ingredients">
      {ordered.map(([group, items]) => (
        <Box key={group || '_'}>
          {group && <Typography className="live-ingredient-group" variant="caption" component="p">{group}</Typography>}
          <Box component="dl">
            {items.map((i, idx) => <Box key={`${i.name}-${idx}`}>
              <Typography component="dt" variant="body2">{i.name}</Typography>
              <Box component="dd"><CookingQuantity ingredient={i} name={i.name} /></Box>
            </Box>)}
          </Box>
        </Box>
      ))}
    </Box>
  )
}

/** 步驟：顯示分組代稱已展開的文字（steps_display）；舊資料沒有時用原文。版本說明與重複的步驟標籤不顯示。 */
export function DishSteps({ steps, display }: { steps: string[]; display?: string[] }) {
  const lines = cleanSteps(display && display.length === steps.length ? display : steps)
  if (lines.length === 0) return null
  return (
    <Box component="ol" sx={{ my: 0.5, pl: 3 }}>
      {lines.map((s, idx) => <li key={idx}><Typography variant="body2">{s}</Typography></li>)}
    </Box>
  )
}
