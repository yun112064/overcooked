/** 每人營養（版面取自 t11-t20-demo 的 PersonNutrition.tsx）：每人這一餐吃到多少，對照每人每日建議攝取量。
 *  另加每人份量列（各道菜分到的份數、主食克數與碗數）。 */
import { Box, Button, Chip, Link, Stack, Tooltip, Typography } from '@mui/material'
import { useState } from 'react'
import { MEAL_LABELS, type DailyReference, type NutritionPreview, type PersonNutrition as Person, type PersonPortion } from '../api/types'
import { NUTRIENTS, rice, toNumber, withinTarget } from '../api/nutrition'
import { tokens } from '../theme'
import { IconTile } from './ui'
import { NUTRIENT_VISUALS } from './visuals'

const KIND_LABELS = { dining: '用餐', bento: '便當' } as const

/** 顯示用取整：kcal、mg 到整數，其餘到 0.1 */
function fmt(v: number, unit: string) {
  return unit === 'kcal' || unit === 'mg' ? `${Math.round(v)}` : v.toFixed(1)
}

/** 每日建議的文字與比較基準（範圍用上下限、上限用上限、建議值用建議值）。 */
function reference(d: DailyReference | undefined, unit: string) {
  if (!d) return null
  const min = d.min !== undefined ? toNumber(d.min) : null
  const max = d.max !== undefined ? toNumber(d.max) : null
  const value = d.value !== undefined ? toNumber(d.value) : null
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

/** 長條：這一餐占每日建議的比例；刻度線是這一餐應占的比例（例如午餐 30%）。上限類超過該餐份額時轉為警示色。 */
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
  const mealRatio = toNumber(portion.meal_ratio) ?? 0
  const energy = toNumber(portion.intake.energy_kcal)
  const energyDaily = daily.energy_kcal?.value ? toNumber(daily.energy_kcal.value) : null
  if (energy === null || !energyDaily) return null
  const energyShare = energy / energyDaily
  return (
    <Typography variant="body2" sx={{ color: mealRatio && withinTarget('energy_kcal', energyShare / mealRatio) ? '#3E5539' : tokens.inkSoft }}>
      {label}約占每日熱量 <b>{Math.round(energyShare * 100)}%</b>
      <Typography component="span" variant="caption" sx={{ ml: 1 }}>建議約 {Math.round(mealRatio * 100)}%</Typography>
    </Typography>
  )
}

/** 這一份分到的量：每道菜的份數（整份＝1，全家加總為 1）與主食克數／碗數。 */
function PortionLine({ portion, dishName, staple }: { portion: PersonPortion; dishName: Map<string, string>; staple: string | null }) {
  const share = toNumber(portion.dish_share)
  return (
    <Typography variant="body2" sx={{ mb: 1.5 }}>
      {portion.dish_ids.map((rid) => `${dishName.get(rid) ?? rid} ${share?.toFixed(2)} 份`).join('、')}
      {portion.staple_g && `、${staple ?? '主食'} ${rice(portion.staple_g)}`}
    </Typography>
  )
}

/** 七項營養素：攝取量、每日建議與占比長條。 */
function NutrientRows({ portion, daily }: { portion: PersonPortion; daily: Person['daily'] }) {
  const mealRatio = toNumber(portion.meal_ratio) ?? 0
  return (
    <Box component="dl" sx={{ m: 0, display: 'grid', gridTemplateColumns: 'minmax(96px, auto) 1fr', columnGap: 1.5, rowGap: 1.5, alignItems: 'start' }}>
      {NUTRIENTS.map(({ key, label, unit }) => {
        const value = toNumber(portion.intake[key])
        const ref = reference(daily[key], unit)
        const partial = toNumber(portion.known_partial[key])
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
                {value === null && partial !== null && (
                  <Typography variant="caption">已知 {fmt(partial, unit)} {unit}</Typography>
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

/** 便當另列，預設收合；只帶便當不在家吃時直接展開。 */
function BentoSection({ person, open: initiallyOpen, dishName, staple }: {
  person: Person; open: boolean; dishName: Map<string, string>; staple: string | null
}) {
  const [open, setOpen] = useState(initiallyOpen)
  const bento = person.bento
  if (!bento) return null
  const meal = MEAL_LABELS[bento.meal] ?? '午餐'
  return (
    <Box sx={{ mt: initiallyOpen ? 0 : 2, pt: initiallyOpen ? 0 : 2, borderTop: initiallyOpen ? 'none' : `1px dashed ${tokens.line}` }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: open ? 1.5 : 0 }}>
        <Chip size="small" label={`便當・${meal}`} variant="outlined" />
        <Typography variant="caption">食量 {toNumber(bento.n)}n・不計入這一餐</Typography>
        {!initiallyOpen && (
          <Button size="small" color="inherit" onClick={() => setOpen((v) => !v)} sx={{ ml: 'auto', color: tokens.inkSoft }}>
            {open ? '收起' : '查看便當營養'}
          </Button>
        )}
      </Box>
      <Box sx={{ mt: open ? 0 : 0.75 }}><EnergyLine portion={bento} daily={person.daily} label={`便當（${meal}）`} /></Box>
      {open && (
        <Box sx={{ mt: 1.5 }}>
          <PortionLine portion={bento} dishName={dishName} staple={staple} />
          <NutrientRows portion={bento} daily={person.daily} />
        </Box>
      )}
    </Box>
  )
}

function PersonCard({ person, name, dishName, staple }: {
  person: Person; name: string; dishName: Map<string, string>; staple: string | null
}) {
  const dining = person.dining
  return (
    <Box sx={{ border: `1px solid ${tokens.line}`, borderRadius: 1, bgcolor: '#FFFDF9', p: { xs: 2, sm: 2.5 } }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
        <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.15rem', letterSpacing: '0.06em' }}>{name}</Typography>
        {person.kinds.map((k) => <Chip key={k} size="small" label={KIND_LABELS[k]} />)}
        {dining && <Typography variant="caption">食量 {toNumber(dining.n)}n</Typography>}
      </Box>
      {dining ? (
        <>
          <Box sx={{ mb: 1.5 }}><EnergyLine portion={dining} daily={person.daily} label="這一餐" /></Box>
          <PortionLine portion={dining} dishName={dishName} staple={staple} />
          <NutrientRows portion={dining} daily={person.daily} />
          <BentoSection person={person} open={false} dishName={dishName} staple={staple} />
        </>
      ) : (
        <>
          <Typography variant="body2" sx={{ mb: 1.5, color: tokens.inkSoft }}>這一餐不在家吃，只帶便當。</Typography>
          <BentoSection person={person} open dishName={dishName} staple={staple} />
        </>
      )}
    </Box>
  )
}

/** 每人營養：每人這一餐吃到多少，對照每人每日建議。 */
export default function PersonNutrition({ people, meta, names, dishName, staple }: {
  people: Person[]; meta: NutritionPreview['daily_reference_meta']; names: Map<string, string>
  dishName: Map<string, string>; staple: string | null
}) {
  const src = meta?.source
  return (
    <Stack spacing={2}>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: people.length > 1 ? 'repeat(2, minmax(0, 1fr))' : '1fr' } }}>
        {people.map((p) => (
          <PersonCard key={p.member_id} person={p} name={names.get(p.member_id) ?? p.name ?? '成員'} dishName={dishName} staple={staple} />
        ))}
      </Box>
      <Typography variant="caption" component="p">
        每人應吃的量依各自的當餐目標分配整餐（盛菜時大人、小孩份量不同；每道菜整份＝1，所有人與便當合計為 1）；
        帶便當的份另外列出，不計入這一餐；長條上的刻度是這一餐建議占每日的比例（早餐 30%、午餐 30%、晚餐 40%，再乘上食量 n）。
        熱量與蛋白質的每日建議：成人依國健署公式，未滿 19 歲依國健署分齡表；
        其餘依{src ? <Link href={src.url} target="_blank" rel="noreferrer">{src.publisher}建議</Link> : '國健署建議'}由每日熱量推算；
        糖為 TFDA 總糖，對照的是添加糖上限，僅供參考。
      </Typography>
    </Stack>
  )
}
