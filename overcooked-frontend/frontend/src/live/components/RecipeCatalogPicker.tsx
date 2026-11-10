import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Paper, Stack, TextField, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api, errorMessage } from '../api/client'
import type { CatalogDish, CatalogFacet, RecipeCatalog } from '../api/types'
import { tokens } from '../theme'

const MAX_DISHES = 10
type FacetKey = 'main_ingredient' | 'cuisine' | 'category' | 'staple_kind'
const FACETS: { key: FacetKey; label: string }[] = [
  { key: 'main_ingredient', label: '主要食材' },
  { key: 'cuisine', label: '菜系' },
  { key: 'category', label: '類型' },
  { key: 'staple_kind', label: '米飯麵' },
]

const NO_FILTERS: Record<FacetKey, string[]> = { main_ingredient: [], cuisine: [], category: [], staple_kind: [] }

/** 一道菜在某分類的值（主要食材可有多個） */
function valuesOf(d: CatalogDish, key: FacetKey): string[] {
  if (key === 'main_ingredient') return d.main_ingredients
  const v = d[key]
  return v ? [v] : []
}

type Props = {
  requestId: string
  initial: string[]
  /** 儲存中或已不能修改 */
  disabled?: boolean
  /** 確定所選菜名；hasStaple：選到主食類的菜（就不另配主食） */
  onConfirm: (names: string[], hasStaple: boolean) => void
}

/** 食譜庫選菜：勾選分類篩出食譜庫的菜，勾選多道後確定；同一分類內任一符合即可，不同分類需同時符合。 */
export default function RecipeCatalogPicker({ requestId, initial, disabled = false, onConfirm }: Props) {
  const catalog = useQuery({
    queryKey: ['recipe-catalog', requestId],
    queryFn: async () => (await api.get<RecipeCatalog>(`/meal-requests/${requestId}/recipe-catalog`)).data,
  })
  const [filters, setFilters] = useState<Record<FacetKey, string[]>>(NO_FILTERS)
  const [keyword, setKeyword] = useState('')
  const [picked, setPicked] = useState<string[]>(initial)

  const dishes = useMemo(() => catalog.data?.dishes ?? [], [catalog.data])
  const byName = useMemo(() => new Map(dishes.map((d) => [d.dish_name, d])), [dishes])
  const shown = dishes.filter((d) =>
    FACETS.every(({ key }) => filters[key].length === 0 || valuesOf(d, key).some((v) => filters[key].includes(v)))
    && (!keyword.trim() || d.dish_name.includes(keyword.trim()) || d.ingredients.some((i) => i.includes(keyword.trim()))))
  // 問卷原本手打、但食譜庫沒有的菜名不帶進來（確定後會被所選的菜取代）
  const chosen = picked.filter((n) => byName.has(n))

  const toggleFilter = (key: FacetKey, value: string) => setFilters((f) => ({
    ...f, [key]: f[key].includes(value) ? f[key].filter((v) => v !== value) : [...f[key], value],
  }))
  const toggleDish = (name: string) => setPicked((p) => (p.includes(name) ? p.filter((n) => n !== name) : [...p, name]))

  return (
    <Stack spacing={2}>
        {catalog.isError && <Alert severity="error">{errorMessage(catalog.error)}</Alert>}
        {catalog.isPending && <CircularProgress />}
        {catalog.data && (
          <Stack spacing={2}>
            {FACETS.map(({ key, label }) => (
              <FacetRow key={key} label={label} facets={catalog.data.facets[key]} selected={filters[key]}
                onToggle={(v) => toggleFilter(key, v)} />
            ))}
            <TextField size="small" label="搜尋菜名或食材" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
            <Typography variant="caption">
              符合 {shown.length} 道・已選 {chosen.length}／{MAX_DISHES} 道
              {FACETS.some(({ key }) => filters[key].length > 0) && (
                <Button size="small" sx={{ ml: 1 }} onClick={() => setFilters(NO_FILTERS)}>清除分類</Button>
              )}
            </Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 1 }}>
              {shown.map((d) => (
                <DishCard key={d.standard_dish_id} dish={d} checked={chosen.includes(d.dish_name)}
                  full={chosen.length >= MAX_DISHES} onToggle={() => toggleDish(d.dish_name)} />
              ))}
            </Box>
            {shown.length === 0 && <Typography variant="body2" color="text.secondary">沒有符合的菜，請減少勾選的分類。</Typography>}
          </Stack>
        )}
      {/* 確定列固定在畫面底部（手機另留底部導覽列的高度），菜很多時不用捲到最後 */}
      <Paper variant="outlined" sx={{ position: 'sticky', bottom: { xs: 72, md: 16 }, zIndex: 2, p: 1.5,
        display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography variant="caption" sx={{ mr: 'auto', minWidth: 0 }}>
          {chosen.length > 0 ? chosen.join('、') : '勾選想吃的菜（最多 10 道）'}
        </Typography>
        <Button variant="contained" disabled={disabled || chosen.length === 0}
          onClick={() => onConfirm(chosen, chosen.some((n) => byName.get(n)?.category === '主食'))}>
          確定（{chosen.length} 道）
        </Button>
      </Paper>
    </Stack>
  )
}

function FacetRow({ label, facets, selected, onToggle }: {
  label: string; facets: CatalogFacet[]; selected: string[]; onToggle: (value: string) => void
}) {
  return (
    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
      <Typography variant="caption" sx={{ minWidth: 60, letterSpacing: '0.12em' }}>{label}</Typography>
      {facets.map((f) => {
        const on = selected.includes(f.value)
        return (
          <Chip key={f.value} label={`${f.value} ${f.count}`} size="small" clickable onClick={() => onToggle(f.value)}
            color={on ? 'primary' : 'default'} variant={on ? 'filled' : 'outlined'} />
        )
      })}
    </Box>
  )
}

function DishCard({ dish, checked, full, onToggle }: {
  dish: CatalogDish; checked: boolean; full: boolean; onToggle: () => void
}) {
  const blocked = dish.conflicts.length > 0
  const disabled = blocked || (full && !checked)
  return (
    <Paper variant="outlined" onClick={disabled ? undefined : onToggle}
      sx={{ p: 1.25, display: 'flex', gap: 1, cursor: disabled ? 'default' : 'pointer', opacity: blocked ? 0.6 : 1,
        borderColor: checked ? tokens.indigo : tokens.line, bgcolor: checked ? tokens.indigoTint : 'transparent' }}>
      <Checkbox size="small" checked={checked} disabled={disabled} sx={{ p: 0.5, alignSelf: 'flex-start' }}
        onClick={(e) => e.stopPropagation()} onChange={onToggle} slotProps={{ input: { 'aria-label': dish.dish_name } }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>{dish.dish_name}</Typography>
        <Typography variant="caption" component="p">
          {[dish.cuisine, dish.category, dish.staple_kind, ...dish.main_ingredients, dish.meal_type === '早餐' ? '早餐' : null]
            .filter(Boolean).join('・')}
        </Typography>
        <Typography variant="caption" component="p" color="text.secondary" noWrap>{dish.ingredients.join('、')}</Typography>
        {blocked && (
          <Typography variant="caption" component="p" sx={{ color: tokens.brick }}>
            含過敏或不吃的食材：{dish.conflicts.join('、')}
          </Typography>
        )}
      </Box>
    </Paper>
  )
}
