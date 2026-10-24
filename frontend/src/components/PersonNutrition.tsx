import { Box, Button, Chip, Link, Stack, Tooltip, Typography } from '@mui/material'
import { useState } from 'react'
import { NUTRIENTS, type Nutrition } from '../api/nutrition'
import type { DailyReference, PersonNutrition as Person } from '../api/types'
import { tokens } from '../theme'
import NutritionTable from './NutritionTable'

const KIND_LABELS = { dining: '用餐', bento: '便當' } as const

/** 顯示用取整：kcal、mg 到整數，其餘到 0.1 */
function fmt(v: number, unit: string) {
  return unit === 'kcal' || unit === 'mg' ? `${Math.round(v)}` : v.toFixed(1)
}

/** 每日建議的文字與比較基準（範圍用上下限、上限用上限、建議值用建議值）。 */
function reference(d: DailyReference | undefined, unit: string) {
  if (!d) return null
  const min = d.min !== undefined ? Number(d.min) : null
  const max = d.max !== undefined ? Number(d.max) : null
  const value = d.value !== undefined ? Number(d.value) : null
  if (d.kind === 'range' && min !== null && max !== null) {
    return { text: `${Math.round(min)}–${Math.round(max)} ${unit}`, base: max, low: min, high: max }
  }
  if (d.kind === 'max' && max !== null) return { text: `< ${Math.round(max)} ${unit}`, base: max, low: null, high: max }
  if (value !== null) return { text: `${fmt(value, unit)} ${unit}`, base: value, low: value, high: value }
  return null
}

function percentText(intake: number, ref: NonNullable<ReturnType<typeof reference>>) {
  if (ref.low !== null && ref.high !== null && ref.low !== ref.high) {
    return `${Math.round((intake / ref.high) * 100)}–${Math.round((intake / ref.low) * 100)}%`
  }
  return `${Math.round((intake / ref.base) * 100)}%`
}

/** 長條：這一餐占每日建議的比例；刻度線是這一餐應占的比例（例如晚餐 40%）。上限類超過該餐份額時轉為警示色。 */
function Bar({ ratio, mealRatio, kind }: { ratio: number; mealRatio: number; kind: DailyReference['kind'] }) {
  const over = kind === 'max' && ratio > mealRatio
  const color = kind === 'max' && ratio > 1 ? tokens.brick : over ? tokens.ochre : tokens.indigo
  return (
    <Box sx={{ position: 'relative', height: 6, bgcolor: tokens.sand, borderRadius: 3, overflow: 'visible' }}>
      <Box sx={{ position: 'absolute', inset: 0, width: `${Math.min(ratio, 1) * 100}%`, bgcolor: color, borderRadius: 3, opacity: 0.85 }} />
      <Tooltip title={`這一餐建議約占每日 ${Math.round(mealRatio * 100)}%`}>
        <Box sx={{ position: 'absolute', top: -3, bottom: -3, left: `calc(${Math.min(mealRatio, 1) * 100}% - 1px)`, width: 2, bgcolor: tokens.ink, opacity: 0.55 }} />
      </Tooltip>
    </Box>
  )
}

function PersonCard({ person }: { person: Person }) {
  const mealRatio = Number(person.meal_ratio)
  const energy = person.intake.energy_kcal !== null ? Number(person.intake.energy_kcal) : null
  const energyDaily = person.daily.energy_kcal?.value ? Number(person.daily.energy_kcal.value) : null
  const energyShare = energy !== null && energyDaily ? energy / energyDaily : null
  return (
    <Box sx={{ border: `1px solid ${tokens.line}`, borderRadius: 1, bgcolor: '#FFFDF9', p: { xs: 2, sm: 2.5 } }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
        <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>
          {person.name ?? '已刪除成員'}
        </Typography>
        {person.kinds.map((k) => <Chip key={k} size="small" label={KIND_LABELS[k]} />)}
        <Typography variant="caption">食量 {Number(person.n)}n</Typography>
      </Box>
      {energyShare !== null && (
        <Typography variant="body2" sx={{ mb: 2, color: Math.abs(energyShare - mealRatio) <= mealRatio * 0.1 ? '#3E5539' : tokens.inkSoft }}>
          這一餐約占每日熱量 <b>{Math.round(energyShare * 100)}%</b>
          <Typography component="span" variant="caption" sx={{ ml: 1 }}>建議約 {Math.round(mealRatio * 100)}%</Typography>
        </Typography>
      )}
      <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'minmax(64px, auto) 1fr', columnGap: 2, rowGap: 1.5 }}>
        {NUTRIENTS.map(({ key, label, unit }) => {
          const raw = person.intake[key]
          const value = raw !== null && raw !== undefined ? Number(raw) : null
          const daily = person.daily[key]
          const ref = reference(daily, unit)
          const partial = person.known_partial[key]
          return (
            <Box key={key} sx={{ display: 'contents' }}>
              <Typography component="dt" variant="body2" sx={{ fontWeight: key === 'energy_kcal' || key === 'protein_g' ? 500 : 400, pt: 0.25 }}>
                {label}
              </Typography>
              <Box component="dd" sx={{ m: 0, minWidth: 0 }}>
                <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap' }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {value !== null ? `${fmt(value, unit)} ${unit}` : '未知'}
                  </Typography>
                  {value === null && partial !== undefined && (
                    <Typography variant="caption">已知 {fmt(Number(partial), unit)} {unit}</Typography>
                  )}
                  {ref && (
                    <Tooltip title={[daily?.label, daily?.caveat].filter(Boolean).join('；')}>
                      <Typography variant="caption" sx={{ ml: 'auto', cursor: 'help' }}>
                        每日 {ref.text}{value !== null && `・${percentText(value, ref)}`}
                      </Typography>
                    </Tooltip>
                  )}
                </Box>
                {ref && value !== null && daily && (
                  <Box sx={{ mt: 0.75 }}><Bar ratio={value / ref.base} mealRatio={mealRatio} kind={daily.kind} /></Box>
                )}
              </Box>
            </Box>
          )
        })}
      </Box>
    </Box>
  )
}

/** 營養：每人這一餐吃到多少，對照每人每日建議。舊結果沒有每人資料時改顯示整餐表。 */
export default function PersonNutrition({ nutrition }: { nutrition: Nutrition }) {
  const [showWhole, setShowWhole] = useState(false)
  const people = nutrition.per_person ?? []
  if (people.length === 0) return <NutritionTable nutrition={nutrition} title={null} />
  const src = nutrition.daily_reference?.source
  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: people.length > 1 ? 'repeat(2, minmax(0, 1fr))' : '1fr' } }}>
        {people.map((p) => <PersonCard key={p.member_id} person={p} />)}
      </Box>
      <Typography variant="caption" component="p">
        每人吃到的量依食量 n 分配整餐；長條上的刻度是這一餐建議占每日的比例。熱量與蛋白質的每日建議依國健署公式與成員資料計算，
        其餘依{src ? <Link href={src.url} target="_blank" rel="noreferrer">{src.publisher}建議</Link> : '國健署建議'}由每日熱量推算；
        糖為 TFDA 總糖，對照的是添加糖上限，僅供參考。
      </Typography>
      <Box>
        <Button size="small" color="inherit" onClick={() => setShowWhole((v) => !v)} sx={{ color: tokens.inkSoft, ml: -1 }}>
          {showWhole ? '收起整餐合計' : '查看整餐合計'}
        </Button>
        {showWhole && <Box sx={{ mt: 1 }}><NutritionTable nutrition={nutrition} title={null} /></Box>}
      </Box>
    </Stack>
  )
}
