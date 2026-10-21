import { Box, Typography } from '@mui/material'
import type { ResultIngredient } from '../api/types'

type Ingredient = Pick<ResultIngredient, 'name' | 'group' | 'display_amount' | 'display_unit' | 'display_text' | 'display_grams'>

/** 後端已取整的畫面用量（克到 1 g、其他單位到整數）；沒有數量時顯示原文 */
function shown(i: Ingredient) {
  if (i.display_amount != null && i.display_unit) return `${Number(i.display_amount)} ${i.display_unit}`
  if (i.display_text) return i.display_text
  return i.display_grams == null ? '無法換算' : `${Math.round(Number(i.display_grams))} g`
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
    <>
      {ordered.map(([group, items]) => (
        <Typography key={group || '_'} variant="body2">
          {group && <Box component="span" sx={{ fontWeight: 'bold' }}>{group}：</Box>}
          {items.map((i) => `${i.name} ${shown(i)}`).join('、')}
        </Typography>
      ))}
    </>
  )
}

/** 步驟：顯示分組代稱已展開的文字（steps_display）；舊資料沒有時用原文。 */
export function DishSteps({ steps, display }: { steps: string[]; display?: string[] }) {
  const lines = display && display.length === steps.length ? display : steps
  if (lines.length === 0) return null
  return (
    <Box component="ol" sx={{ my: 0.5, pl: 3 }}>
      {lines.map((s, idx) => <li key={idx}><Typography variant="body2">{s}</Typography></li>)}
    </Box>
  )
}
