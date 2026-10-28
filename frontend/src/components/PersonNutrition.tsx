import { Box, Button, Chip, Link, Stack, Tooltip, Typography } from '@mui/material'
import { useState } from 'react'
import { NUTRIENTS, type Nutrition } from '../api/nutrition'
import { MEAL_LABELS, type DailyReference, type PersonNutrition as Person, type PersonPortion } from '../api/types'
import { tokens } from '../theme'
import NutritionTable from './NutritionTable'
import { IconTile } from './ui'
import { NUTRIENT_VISUALS, TONES } from './visuals'

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

/** 這一份占每日熱量的比例（與建議比例並列）。 */
function EnergyLine({ portion, daily, label }: { portion: PersonPortion; daily: Person['daily']; label: string }) {
  const mealRatio = Number(portion.meal_ratio)
  const energy = portion.intake.energy_kcal != null ? Number(portion.intake.energy_kcal) : null
  const energyDaily = daily.energy_kcal?.value ? Number(daily.energy_kcal.value) : null
  if (energy === null || !energyDaily) return null
  const energyShare = energy / energyDaily
  return (
    <Typography variant="body2" sx={{ color: Math.abs(energyShare - mealRatio) <= mealRatio * 0.1 ? '#3E5539' : tokens.inkSoft }}>
      {label}約占每日熱量 <b>{Math.round(energyShare * 100)}%</b>
      <Typography component="span" variant="caption" sx={{ ml: 1 }}>建議約 {Math.round(mealRatio * 100)}%</Typography>
    </Typography>
  )
}

/** 七項營養素：攝取量、每日建議與占比長條。 */
function NutrientRows({ portion, daily }: { portion: PersonPortion; daily: Person['daily'] }) {
  const mealRatio = Number(portion.meal_ratio)
  return (
    <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'minmax(96px, auto) 1fr', columnGap: 1.5, rowGap: 1.5, alignItems: 'start' }}>
      {NUTRIENTS.map(({ key, label, unit }) => {
        const raw = portion.intake[key]
        const value = raw !== null && raw !== undefined ? Number(raw) : null
        const ref = reference(daily[key], unit)
        const partial = portion.known_partial[key]
        return (
          <Box key={key} sx={{ display: 'contents' }}>
            <Typography component="dt" variant="body2" sx={{ fontWeight: key === 'energy_kcal' || key === 'protein_g' ? 500 : 400,
              display: 'flex', alignItems: 'center', gap: 1 }}>
              <IconTile icon={NUTRIENT_VISUALS[key].icon} tone={NUTRIENT_VISUALS[key].tone} size={24} />
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
                  <Tooltip title={[daily[key]?.label, daily[key]?.caveat].filter(Boolean).join('；')}>
                    <Typography variant="caption" sx={{ ml: 'auto', cursor: 'help' }}>
                      每日 {ref.text}{value !== null && `・${percentText(value, ref)}`}
                    </Typography>
                  </Tooltip>
                )}
              </Box>
              {ref && value !== null && daily[key] && (
                <Box sx={{ mt: 0.75 }}><Bar ratio={value / ref.base} mealRatio={mealRatio} kind={daily[key].kind} /></Box>
              )}
            </Box>
          </Box>
        )
      })}
    </Box>
  )
}

/** 便當另列（隔天午餐），預設收合；只帶便當不在家吃時直接展開。 */
function BentoSection({ person, open: initiallyOpen }: { person: Person; open: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  const bento = person.bento
  if (!bento) return null
  const meal = MEAL_LABELS[bento.meal] ?? '午餐'
  return (
    <Box sx={{ mt: initiallyOpen ? 0 : 2, pt: initiallyOpen ? 0 : 2, borderTop: initiallyOpen ? 'none' : `1px dashed ${tokens.line}` }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: open ? 1.5 : 0 }}>
        <Chip size="small" label={`便當・${meal}`} variant="outlined" />
        <Typography variant="caption">食量 {Number(bento.n)}n・不計入這一餐</Typography>
        {!initiallyOpen && (
          <Button size="small" color="inherit" onClick={() => setOpen((v) => !v)} sx={{ ml: 'auto', color: tokens.inkSoft }}>
            {open ? '收起' : '查看便當營養'}
          </Button>
        )}
      </Box>
      <Box sx={{ mt: open ? 0 : 0.75 }}><EnergyLine portion={bento} daily={person.daily} label={`便當（${meal}）`} /></Box>
      {open && <Box sx={{ mt: 1.5 }}><NutrientRows portion={bento} daily={person.daily} /></Box>}
    </Box>
  )
}

function PersonCard({ person }: { person: Person }) {
  // 舊結果沒有 dining 欄：用餐與便當當時已合併計算，照舊顯示
  const dining = person.dining ?? true
  return (
    <Box sx={{ border: `1px solid ${tokens.line}`, borderRadius: 1, bgcolor: '#FFFDF9', p: { xs: 2, sm: 2.5 } }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
        <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>
          {person.name ?? '已刪除成員'}
        </Typography>
        {person.kinds.map((k) => <Chip key={k} size="small" label={KIND_LABELS[k]} />)}
        {dining && <Typography variant="caption">食量 {Number(person.n)}n</Typography>}
      </Box>
      {dining ? (
        <>
          <Box sx={{ mb: 2 }}><EnergyLine portion={person} daily={person.daily} label="這一餐" /></Box>
          <NutrientRows portion={person} daily={person.daily} />
          <BentoSection person={person} open={false} />
        </>
      ) : (
        <>
          <Typography variant="body2" sx={{ mb: 1.5, color: tokens.inkSoft }}>這一餐不在家吃，只帶便當。</Typography>
          <BentoSection person={person} open />
        </>
      )}
    </Box>
  )
}

/** 三大營養素占熱量比例的參考範圍（國健署：碳水 50–60%、脂肪 20–30%、蛋白質 10–20%）。 */
const MACROS = [
  { key: 'carbohydrate_g', label: '碳水', kcal: 4, range: [50, 60], tone: 'amber' as const },
  { key: 'protein_g', label: '蛋白質', kcal: 4, range: [10, 20], tone: 'indigo' as const },
  { key: 'fat_g', label: '脂肪', kcal: 9, range: [20, 30], tone: 'matcha' as const },
]

/** 整餐三大營養素的熱量比例：一條堆疊長條＋每項的百分比、克數與參考範圍（文字並列，不只靠顏色）。 */
export function MacroBar({ nutrition }: { nutrition: Nutrition }) {
  const grams = (key: string) => {
    const v = key === 'protein_g' ? nutrition.protein_g : nutrition.other_nutrients?.[key]
    return v === null || v === undefined ? null : Number(v)
  }
  const parts = MACROS.map((m) => ({ ...m, g: grams(m.key) }))
  if (parts.some((p) => p.g === null)) return null
  const total = parts.reduce((sum, p) => sum + (p.g as number) * p.kcal, 0)
  if (!total) return null
  const rows = parts.map((p) => ({ ...p, pct: ((p.g as number) * p.kcal / total) * 100 }))
  return (
    <Box>
      <Typography variant="caption" component="p" sx={{ letterSpacing: '0.16em', color: tokens.inkMuted, mb: 1 }}>
        三大營養素熱量比例
      </Typography>
      <Box role="img" aria-label={rows.map((r) => `${r.label} ${Math.round(r.pct)}%`).join('、')}
        sx={{ display: 'flex', height: 10, borderRadius: 5, overflow: 'hidden', gap: '2px', bgcolor: tokens.sand }}>
        {rows.map((r) => <Box key={r.key} sx={{ width: `${r.pct}%`, bgcolor: TONES[r.tone].fg, opacity: 0.8 }} />)}
      </Box>
      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 1.5, mt: 1.25 }}>
        {rows.map((r) => {
          const inRange = r.pct >= r.range[0] && r.pct <= r.range[1]
          return (
            <Box key={r.key}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <Box aria-hidden sx={{ width: 8, height: 8, borderRadius: '2px', bgcolor: TONES[r.tone].fg }} />
                <Typography variant="body2" sx={{ fontWeight: 500 }}>{r.label} {Math.round(r.pct)}%</Typography>
              </Box>
              <Typography variant="caption" component="p" sx={{ color: inRange ? '#5E7B5A' : tokens.ochre }}>
                {Math.round(r.g as number)} g・建議 {r.range[0]}–{r.range[1]}%{inRange ? '' : (r.pct < r.range[0] ? '・偏低' : '・偏高')}
              </Typography>
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
        每人應吃的量依各自的當餐目標分配整餐（盛菜時大人、小孩份量不同）；帶便當的份另外列出，不計入這一餐；長條上的刻度是這一餐建議占每日的比例
        （早餐 30%、午餐 30%、晚餐 40%，再乘上食量 n）。熱量與蛋白質的每日建議：成人依國健署公式，未滿 19 歲依國健署分齡表；
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
