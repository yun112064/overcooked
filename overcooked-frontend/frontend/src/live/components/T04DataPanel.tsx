import { Alert, Box, Button, Stack, Typography } from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorMessage } from '../api/client'
import type { MealRequest } from '../api/types'

type Source = { source_id: string; provider: string; name: string; source_url: string | null;
  per_100g: Record<string, string | null>; state: string | null; cooking_method: string | null;
  included_in_original_aggregate: boolean | null; binding_kind: string;
  confirmation_required?: boolean; confirmation_question?: string | null }
type Feedback = { input_version: number; message: string; ingredients: { recipe_id: string; line_id: string;
  name: string; status: string; whole_recipe_pre_cooking_grams: string | null; sources: Source[] }[];
  recipes: { recipe_id: string; name: string; servings_raw: string; servings_basis: string }[] }
const labels: Record<string, string> = { source_available_pending_review: '已有必要營養來源，待核對',
  source_candidate_needs_confirmation: '找到參考樣品，食材規格需確認',
  source_values_incomplete: '來源資料仍不完整', source_missing: '尚缺來源' }
const nutrients = [['energy_kcal', '熱量', 'kcal'], ['protein_g', '蛋白質', 'g'], ['fat_g', '脂肪', 'g'],
  ['carbohydrate_g', '碳水', 'g'], ['sugars_g', '糖', 'g'], ['sodium_mg', '鈉', 'mg'], ['dietary_fiber_g', '纖維', 'g']]

function SourceList({ sources }: { sources: Source[] }) {
  const [expanded, setExpanded] = useState(false)
  const usable = sources.filter((s) => s.provider !== 'recipe_dataset')
  return <Stack spacing={1} sx={{ mt: 1 }}>
    {(expanded ? usable : usable.slice(0, 2)).map((s) => <Box key={s.source_id} sx={{ pl: 1, borderLeft: '2px solid', borderColor: 'divider' }}>
      <Typography variant="body2">{s.provider} · {s.name}（{s.state ?? '狀態待確認'}{s.cooking_method ? `／${s.cooking_method}` : ''}）</Typography>
      {s.confirmation_required && <Alert severity="warning">{s.confirmation_question ?? '此來源的食材規格尚未確認，只供比較。'}</Alert>}
      <Typography variant="caption">每 100 g：{nutrients.map(([k, label, unit]) => `${label} ${s.per_100g[k] == null ? '未知' : `${s.per_100g[k]} ${unit}`}`).join(' · ')}</Typography>
      {s.included_in_original_aggregate === false && <Typography variant="caption" component="p" color="warning.main">原來源標記未納入彙整，尚待確認。</Typography>}
      {s.source_url && <Typography variant="caption" component="p"><a href={s.source_url} target="_blank" rel="noreferrer">原始來源</a></Typography>}
    </Box>)}
    {usable.length > 2 && <Button size="small" onClick={() => setExpanded(!expanded)}>{expanded ? '收合來源' : `檢視全部 ${usable.length} 份來源`}</Button>}
    {!usable.length && <Typography variant="caption">尚無可展示的官方／產品每 100 g 來源；歷史配方推導不作替代。</Typography>}
  </Stack>
}

export default function T04DataPanel({ row }: { row: MealRequest }) {
  const check = useMutation({ mutationFn: async () => {
    const retrieval = (await api.get<{ selected: { recipe_id: string }[]; status: string }>(`/meal-requests/${row.id}/recipes`)).data
    if (!retrieval.selected?.length) throw new Error('目前條件查無可檢查的資料庫菜單，請調整問卷後重試。')
    return (await api.post<Feedback>(`/meal-requests/${row.id}/t04-data`, {
      request_version: row.version, recipe_ids: retrieval.selected.map((r) => r.recipe_id),
    })).data
  } })
  const result = check.data?.input_version === row.version ? check.data : null
  return <Stack spacing={1}>
    <Button variant="outlined" disabled={!row.complete || check.isPending} onClick={() => check.mutate()}>
      {check.isPending ? '正在檢查來源…' : '檢查候選菜單的營養資料'}
    </Button>
    {check.isError && <Alert severity="warning">{check.error instanceof Error && !('response' in check.error)
      ? check.error.message : errorMessage(check.error)}</Alert>}
    {result && <>
      <Alert severity="info">{result.message} 下列數字為來源每 100 g 值，整餐營養與烹調轉換仍待驗證。</Alert>
      {result.recipes.map((recipe) => <Box key={recipe.recipe_id}>
        <Typography sx={{ fontWeight: 'bold' }}>{recipe.name} · 原配方 {recipe.servings_raw}{recipe.servings_basis === 'batch' ? '（批次不作人份）' : ''}</Typography>
        {result.ingredients.filter((i) => i.recipe_id === recipe.recipe_id).map((i) => <Box key={i.line_id} sx={{ my: 1.5 }}>
          <Typography variant="body2">{i.name} · 整份配方烹調前 {i.whole_recipe_pre_cooking_grams ?? '未知'} g · {labels[i.status] ?? i.status}</Typography>
          <SourceList sources={i.sources} />
        </Box>)}
      </Box>)}
    </>}
  </Stack>
}
