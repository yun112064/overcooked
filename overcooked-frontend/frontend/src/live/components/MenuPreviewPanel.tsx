import ResultSections from './ResultSections'
import { PreviewPortions } from './PortionsTable'
import CheckCircleOutlineOutlined from '@mui/icons-material/CheckCircleOutlineOutlined'
import { Alert, Box, Button, Chip, CircularProgress, Link, Stack, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import { Link as RouterLink } from 'react-router'
import { api, errorMessage } from '../api/client'
import MarketCheckoutButton from './MarketCheckoutButton'
import type { KnowledgeTips, MealPlanSummary, MealRequest, Member, NutritionPreview, RecipeRetrieval } from '../api/types'
import { tokens } from '../theme'
import { NUTRIENTS, toNumber, withinTarget } from '../api/nutrition'
import DishList from './DishList'
import PersonNutrition from './PersonNutrition'
import { Section, Stat, StatRow } from './ui'

const TIP_UNAVAILABLE: Record<string, string> = {
  EXPLANATION_NOT_CONFIGURED: '尚未設定雲端小知識服務（OPENAI_API_KEY）。',
  NOT_CONFIGURED: '食材知識服務未啟動。',
  KNOWLEDGE_UNAVAILABLE: '食材知識服務暫時無法使用。',
  EMBEDDING_UNAVAILABLE: '本機知識檢索模型無法使用。',
}

/** 資料庫排不出菜單的原因 → 說明與做法；有菜名時直接寫出來。 */
function retrievalMessage(menu: RecipeRetrieval, staple: string | null): string {
  const names = (menu.details?.specified_dishes ?? []).map((n) => `「${n}」`).join('、')
  switch (menu.reason) {
    case 'SPECIFIED_DISH_STAPLE_CONFLICT':
      return `指定的${names || '菜'}本身就是主食，和你選的主食「${staple ?? ''}」重複。請把主食改成「不需要主食」，或拿掉這道菜。`
    case 'SPECIFIED_DISH_DIETARY_CONFLICT':
      return `指定的${names || '菜'}含有成員過敏或禁吃的食材。請拿掉這道菜，或確認成員的禁忌設定。`
    case 'SPECIFIED_DISH_NOT_IN_DATABASE':
      return `食譜庫裡找不到指定的${names || '菜'}。請改寫菜名或拿掉，其他菜會由系統搭配。`
    case 'SPECIFIED_DISH_AMBIGUOUS':
      return '指定的菜名對應到不只一道菜，請把菜名寫得更完整。'
    case 'ALL_EXCLUDED_BY_PREVIOUS_DAY':
      return '符合條件的菜昨天都吃過了。可以減少道數，或直接指定想吃的菜。'
    case 'NO_MATCHING_RECIPES':
      return '食譜庫裡符合條件的菜不夠這麼多道。可以減少道數，或確認成員的禁忌設定。'
    case 'ALL_MENUS_REJECTED':
      return '資料庫裡符合條件的組合都已經換過了。可以調整道數、指定菜色或主食後再查詢。'
    case 'MENU_STRUCTURE_UNMET':
      return '湊不出符合規則的主菜與湯品組合。可以調整道數或指定菜色。'
    default:
      return '資料庫沒有可用的菜單，可以調整問卷後再試。'
  }
}

const fmt = (v: number | null, digits = 0) => (v === null ? '未知' : v.toFixed(digits))

/** 整餐七種營養素；缺值顯示「未知（已知 X，缺：A、B）」，不以 0 代替。 */
function NutrientTable({ all }: { all: NonNullable<NutritionPreview['nutrition_all']> }) {
  return (
    <Box component="dl" sx={{ m: 0, mt: 3, display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))' },
      columnGap: 3, rowGap: 0.75 }}>
      {NUTRIENTS.map(({ key, label, unit }) => {
        const digits = unit === 'g' ? 1 : 0
        const value = toNumber(all.totals[key])
        const known = toNumber(all.known_partial[key])
        const lacking = all.missing_by_nutrient[key] ?? []
        return (
          <Box key={key} sx={{ display: 'flex', gap: 1.5, alignItems: 'baseline', borderBottom: `1px dashed ${tokens.line}`, pb: 0.5 }}>
            <Typography component="dt" variant="body2" sx={{ minWidth: 72, color: tokens.inkSoft }}>{label}</Typography>
            <Typography component="dd" variant="body2" sx={{ m: 0, fontWeight: value === null ? 400 : 500 }}>
              {value !== null ? `${fmt(value, digits)} ${unit}`
                : `未知（${[known !== null ? `已知 ${fmt(known, digits)} ${unit}` : null,
                  lacking.length ? `缺：${lacking.join('、')}` : null].filter(Boolean).join('，')}）`}
            </Typography>
          </Box>
        )
      })}
    </Box>
  )
}

/** 小知識來源的網站名稱：網頁取平台名（健康醫療網、Hello醫師），論文取期刊名；沒有時用網址主機名。 */
function sourceName(label: string | null | undefined, url: string) {
  const lines = (label ?? '').split('\n').map((s) => s.trim()).filter(Boolean)
  if (lines.length >= 2) return lines[1].replace(/\s+\d.*$/, '')
  if (lines.length === 1) return lines[0]
  try { return new URL(url).hostname } catch { return '原文' }
}

function targetNote(key: 'energy_kcal' | 'protein_g', value: number | null, target: number | null, unit: string) {
  if (value === null || !target) return { note: target ? `目標 ${Math.round(target)} ${unit}` : undefined, tone: 'default' as const }
  const diff = value / target - 1
  return { note: `目標 ${Math.round(target)} ${unit}・${diff >= 0 ? '+' : ''}${Math.round(diff * 100)}%`,
    tone: withinTarget(key, value / target) ? 'good' as const : 'warn' as const }
}

type Props = { row: MealRequest; members: Member[]; onAdopted?: () => void }

/** 問卷完成後自動顯示（版面取自 t11-t20-demo）：T11 資料庫菜單 → T12 營養計算（都在本機）；
 *  營養通過後自動產生營養小知識（雲端付費呼叫；同一頁面內同一版只產生一次）。 */
export default function MenuPreviewPanel({ row, members, onAdopted }: Props) {
  const menu = useQuery({
    queryKey: ['meal-request-menu', row.id, row.version],
    queryFn: async () => (await api.get<RecipeRetrieval>(`/meal-requests/${row.id}/recipes`)).data,
    enabled: row.complete || (row.draft.mode === 'picker' && row.status === 'confirmed'),
    retry: false,
  })
  const recipeIds = (menu.data?.selected ?? []).map((r) => r.recipe_id)
  const nutrition = useQuery({
    queryKey: ['meal-request-nutrition', row.id, row.version, recipeIds],
    queryFn: async () =>
      (await api.post<NutritionPreview>(`/meal-requests/${row.id}/nutrition-preview`, { recipe_ids: recipeIds })).data,
    enabled: recipeIds.length > 0,
    retry: false,
  })
  // 營養通過後自動產生；頁面開著時同一版問卷與菜單只呼叫一次（重新載入頁面會再產生一次）
  const tips = useQuery({
    queryKey: ['meal-request-tips', row.id, row.version, recipeIds],
    queryFn: async () =>
      (await api.post<KnowledgeTips>(`/meal-requests/${row.id}/knowledge-tips`, { recipe_ids: recipeIds })).data,
    enabled: nutrition.data?.status === 'nutrition_passed',
    retry: false,
    staleTime: Infinity,
    gcTime: Infinity,
  })

  const queryClient = useQueryClient()
  // 同一份菜單固定一把冪等鍵：重複點擊或網路重送只採用一次
  const adoptKeys = useRef(new Map<string, string>())
  const keyFor = (k: string) => {
    if (!adoptKeys.current.has(k)) adoptKeys.current.set(k, crypto.randomUUID())
    return adoptKeys.current.get(k)!
  }
  const adopt = useMutation({
    mutationFn: async () => (await api.post<{ meal_plan: MealPlanSummary }>('/meal-plans',
      { meal_request_id: row.id, request_version: row.version, recipe_ids: recipeIds },
      { headers: { 'Idempotency-Key': keyFor(`${row.version}:${recipeIds.join(',')}`) } })).data.meal_plan,
    onSuccess: async () => {
      onAdopted?.()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['meal-request-menu', row.id] }),
        queryClient.invalidateQueries({ queryKey: ['meal-plan-dates'] }),
      ])
    },
  })
  const reject = useMutation({
    mutationFn: async () => api.post(`/meal-requests/${row.id}/menu-rejections`, { version: row.version, recipe_ids: recipeIds }),
    onSuccess: () => menu.refetch(),
  })

  const adopted = adopt.data ?? menu.data?.adopted_plan ?? null
  if (!row.complete && row.status !== 'confirmed') {
    return <Typography color="text.secondary">問卷填寫完整後會自動顯示資料庫菜單。</Typography>
  }
  const selected = menu.data?.selected ?? []
  const n = nutrition.data
  const dishName = new Map(selected.map((r) => [r.recipe_id, r.recipe_name]))
  const memberName = new Map(members.map((m) => [m.id, m.display_name]))
  const energy = toNumber(n?.nutrition.totals?.energy_kcal)
  const protein = toNumber(n?.nutrition.totals?.protein_g)
  const energyInfo = targetNote('energy_kcal', energy, toNumber(n?.targets.energy_kcal), 'kcal')
  const proteinInfo = targetNote('protein_g', protein, toNumber(n?.targets.protein_g), 'g')
  const passed = n?.status === 'nutrition_passed'

  return (
    <Stack spacing={3}>
      <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h5" component="h2" sx={{ flexGrow: 1 }}>這一餐的菜單</Typography>
        <Chip size="small" label="資料庫菜單" />
        {(menu.data?.rejected_count ?? 0) > 0 && <Chip size="small" label={`已換 ${menu.data?.rejected_count} 次`} />}
        {!adopted && (
          <Button size="small" color="inherit" disabled={menu.isFetching} onClick={() => menu.refetch()}
            sx={{ color: tokens.inkSoft }}>重新查詢</Button>
        )}
      </Box>

      {menu.isFetching && <CircularProgress size={24} />}
      {menu.isError && <Alert severity="error">{errorMessage(menu.error)}</Alert>}
      {menu.data && !adopted && selected.length === 0 && <Alert severity="info">{retrievalMessage(menu.data, row.draft.staple)}</Alert>}
      {nutrition.isFetching && <Typography color="text.secondary">計算營養中…</Typography>}
      {nutrition.isError && <Alert severity="error">{errorMessage(nutrition.error)}</Alert>}

      {adopted && (
        <Alert icon={<CheckCircleOutlineOutlined />} severity="success">
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography variant="body2">已採用並保存為已採用餐食紀錄（不代表實際已食用）。</Typography>
            <Button variant="outlined" component={RouterLink} to={`/history?date=${adopted.meal_date}`}>查看已採用紀錄</Button>
            {adopted.status === 'adopted' && adopted.market_shortages_available && (
              <MarketCheckoutButton planId={adopted.id} />
            )}
          </Stack>
        </Alert>
      )}

      {n && (
        <Box sx={{ border: `1px solid ${tokens.line}`, borderRadius: 1, bgcolor: '#FFFDF9', p: { xs: 2, sm: 3 } }}>
          <StatRow>
            <Stat label="整餐熱量" value={fmt(energy)} unit={energy === null ? undefined : 'kcal'}
              note={energyInfo.note} tone={energyInfo.tone} />
            <Stat label="整餐蛋白質" value={fmt(protein, 1)} unit={protein === null ? undefined : 'g'}
              note={proteinInfo.note} tone={proteinInfo.tone} />
            <Stat label="菜數" value={selected.length} unit="道" note={row.draft.staple ? `主食：${row.draft.staple}` : undefined} />
            <Stat label="用餐份數" value={n.per_person?.length ?? row.items.length} unit="份"
              note="含便當；份量依各人營養目標分配" />
          </StatRow>
          {!adopted && (
            <Box sx={{ mt: 3, pt: 2.5, borderTop: `1px solid ${tokens.line}`, display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
              <Box sx={{ flexGrow: 1, minWidth: 220 }}>
                <Typography variant="body2" sx={{ color: passed ? '#3E5539' : n.status === 'outside_tolerance' ? tokens.ochre : tokens.brick }}>
                  {passed && !n.adoption_blocked ? '符合採用條件。' : n.status_message}
                </Typography>
                {n.input_issues.length > 0 && (
                  <Box component="ul" sx={{ m: 0, mt: 1, pl: 3 }}>
                    {n.input_issues.map((i, k) => <li key={`${i.code}-${i.recipe_id}-${k}`}><Typography variant="body2">{i.message}</Typography></li>)}
                  </Box>
                )}
              </Box>
              <Button variant="outlined" size="large" disabled={reject.isPending || adopt.isPending || selected.length === 0}
                onClick={() => reject.mutate()}>
                不採用，換一份
              </Button>
              <Button variant="contained" size="large" disabled={!passed || n.adoption_blocked || adopt.isPending || reject.isPending
                || row.status === 'queued' || row.status === 'running' || row.status === 'confirmed'}
                onClick={() => adopt.mutate()}>
                採用這份菜單
              </Button>
            </Box>
          )}
          {adopt.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(adopt.error)}</Alert>}
          {reject.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(reject.error)}</Alert>}
        </Box>
      )}

      {selected.length > 0 && <ResultSections
        recipes={<>
      {selected.length > 0 && (
        <Section title="全家配方" aside="整餐用量">
          <DishList selected={selected} dishes={n?.dishes} staple={n?.staple ?? null} stapleName={row.draft.staple} />
        </Section>
      )}

      {menu.data?.previous_day && <PreviousDay context={menu.data.previous_day} selected={selected} />}

      {n && (
        <Section title="食材小知識" aside={tipSites(tips.data) ? `資料來源：${tipSites(tips.data)}` : undefined}>
          {!passed ? (
            <Typography variant="body2" color="text.secondary">
              營養小知識只針對通過營養計算的菜單提供；可調整指定菜色、道數或主食後再試。
            </Typography>
          ) : tips.isError ? (
            <Alert severity="error">{errorMessage(tips.error)}</Alert>
          ) : !tips.data ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
              <CircularProgress size={20} />
              <Typography variant="body2" color="text.secondary">正在產生營養小知識…</Typography>
            </Box>
          ) : (
            <TipsResult data={tips.data} />
          )}
        </Section>
      )}
        </>}
        shopping={<Section title="採購資訊"><Typography variant="body2" color="text.secondary">
          此菜單預覽尚未提供採購報價。如需查看缺料、整包採購價與本餐成本，請使用「規劃餐食」的菜單生成功能；已採用的餐食也可從紀錄查看。
        </Typography></Section>}
        nutrition={<>
          {n?.nutrition_all && <Section title="整餐營養"><NutrientTable all={n.nutrition_all} /></Section>}
      {n && (n.people?.length ?? 0) > 0 && (
        <Section title="每人營養" aside="這一餐吃到的量／每日建議攝取量">
          <PersonNutrition people={n.people ?? []} meta={n.daily_reference_meta} names={memberName}
            dishName={dishName} staple={row.draft.staple} />
        </Section>
      )}

        </>}
        portions={<PreviewPortions nutrition={n} names={memberName} />}
      />}
    </Stack>
  )
}

/** 避免重複的菜：昨天已採用的菜會排除；指定的菜照樣保留。 */
function PreviousDay({ context, selected }: {
  context: NonNullable<RecipeRetrieval['previous_day']>; selected: RecipeRetrieval['selected']
}) {
  const kept = selected.filter((r) => context.specified_exceptions.includes(r.standard_dish_id)).map((r) => r.recipe_name)
  return (
    <Section title="避免重複的菜">
      <Stack spacing={1.5}>
        {context.records.length === 0 ? (
          <Typography variant="body2">昨天（{context.previous_date}）沒有已採用紀錄，未排除任何菜。</Typography>
        ) : (
          <Typography variant="body2">
            已比對昨天（{context.previous_date}）的已採用紀錄；
            {context.excluded_dishes.length > 0
              ? `已排除：${context.excluded_dishes.map((d) => d.dish_name).join('、')}。`
              : '沒有需要排除的菜。'}
          </Typography>
        )}
        {kept.length > 0 && (
          <Alert severity="info">{kept.join('、')} 與昨天重複，因為是你指定的菜所以保留；其他限制（過敏、營養等）仍照常檢查。</Alert>
        )}
      </Stack>
    </Section>
  )
}

/** 小知識用到的網站名稱（去重），放在區塊右上角。 */
function tipSites(data: KnowledgeTips | undefined) {
  if (!data || data.status === 'unavailable') return ''
  return [...new Set(data.items.flatMap((k) => k.citations.map((c) => sourceName(c.source_label, c.source_url))))].join('、')
}

function TipsResult({ data }: { data: KnowledgeTips }) {
  if (data.status === 'unavailable') {
    return <Alert severity="warning">營養小知識暫時無法提供：{TIP_UNAVAILABLE[data.reason ?? ''] ?? '服務不可用'}</Alert>
  }
  if (data.items.length === 0) {
    return <Typography variant="body2" color="text.secondary">這份菜單的食材目前沒有審核過的營養知識。</Typography>
  }
  return (
    <Stack spacing={2.5}>
      {data.items.map((k) => (
        <Box key={k.ingredient_id} sx={{ pl: 2, borderLeft: `2px solid ${tokens.sand}` }}>
          <Typography sx={{ fontWeight: 500, letterSpacing: '0.08em', mb: 0.5 }}>{k.ingredient_name ?? k.ingredient_id}</Typography>
          <Typography variant="body2">{k.tip_zh}</Typography>
          <Typography variant="caption" component="p" sx={{ mt: 0.5 }}>
            {k.citations.map((c, idx) => (
              <span key={c.knowledge_id}>
                {idx > 0 && '、'}來源：{sourceName(c.source_label, c.source_url)}{' '}
                <Link href={c.source_url} target="_blank" rel="noopener noreferrer">原文</Link>
              </span>
            ))}
          </Typography>
        </Box>
      ))}
    </Stack>
  )
}
