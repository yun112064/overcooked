import {
  Alert, Box, Card, CardContent, Chip, Link, Stack, TextField, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealPlanDetail } from '../api/types'
import { DishIngredients, DishSteps } from '../components/DishDetail'
import NutritionTable from '../components/NutritionTable'

const money = (v: string | null | undefined) => (v === null || v === undefined ? '未知' : `${Math.round(Number(v))} 元`)

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 單筆已採用餐食：顯示的都是採用當下保存的快照，來源之後更新不會改變。 */
function PlanCard({ plan }: { plan: MealPlanDetail }) {
  return (
    <Card variant="outlined">
      <CardContent>
        <Stack spacing={1}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography variant="h6">{MEAL_LABELS[plan.meal_type]}</Typography>
            <Chip size="small" color={plan.ai_generated ? 'secondary' : 'default'} label={plan.mode_label} />
            {plan.price_exception_accepted && <Chip size="small" color="warning" label="已接受缺價例外" />}
            <Typography variant="caption" color="text.secondary">
              採用於 {new Date(plan.adopted_at).toLocaleString()}
              {plan.result_version !== null && `・結果第 ${plan.result_version} 版`}
            </Typography>
          </Box>
          {plan.fallback_message && <Alert severity="info">{plan.fallback_message}</Alert>}
          {plan.dishes.map((d) => (
            <Box key={d.standard_dish_id}>
              <Typography sx={{ fontWeight: 'bold' }}>
                {d.dish_name}{d.cooking_method && ` · ${d.cooking_method}`}
                {d.aliases.length > 0 && <Typography component="span" variant="caption" color="text.secondary">（又名 {d.aliases.join('、')}）</Typography>}
              </Typography>
              {d.ingredients && <DishIngredients ingredients={d.ingredients} />}
              {d.steps && <DishSteps steps={d.steps} display={d.steps_display} />}
              {d.recipe_source && (
                <Typography variant="caption" color="text.secondary">
                  食譜來源：<Link href={d.recipe_source.source_url} target="_blank" rel="noreferrer">{d.recipe_source.source_name}</Link>
                </Typography>
              )}
            </Box>
          ))}
          {/* 決策 #53：與結果頁相同的 7 種營養素與未知顯示（以 null 判斷，0 照常顯示） */}
          {plan.nutrition && <NutritionTable nutrition={plan.nutrition} title="營養" />}
          {plan.prices?.status === 'computed' && (
            <Typography variant="body2">
              採購價 {plan.prices.purchase_total !== null ? money(plan.prices.purchase_total) : `未知（已知 ${money(plan.prices.known_purchase_total)}）`}
              ；成本價 {plan.prices.cost_total !== null ? money(plan.prices.cost_total) : `未知（已知 ${money(plan.prices.known_cost_total)}）`}
              ；預算 {money(plan.prices.budget)}
            </Typography>
          )}
          {plan.previous_day && (
            <Typography variant="body2" color="text.secondary">
              昨日比對（{plan.previous_day.previous_date}）：
              {plan.previous_day.excluded_dishes.length > 0
                ? `排除 ${plan.previous_day.excluded_dishes.map((d) => d.dish_name).join('、')}`
                : '沒有排除的菜'}
              {plan.previous_day.specified_repeats.length > 0 && `；指定保留 ${plan.previous_day.specified_repeats.join('、')}`}
            </Typography>
          )}
          {plan.knowledge?.status === 'available' && (
            <Box>
              {plan.knowledge.items.map((k) => (
                <Typography key={k.ingredient_id} variant="body2">
                  <b>{k.ingredient_name ?? k.ingredient_id}</b>：{k.explanation}
                  <Typography component="span" variant="caption" color="text.secondary">
                    （{k.citations.map((c) => (c.attribution
                      ? `${c.attribution} ${c.source_url ?? ''}`.trim()
                      : `來源：${c.knowledge_id} ${c.source_version}`)).join('、')}）
                  </Typography>
                </Typography>
              ))}
            </Box>
          )}
        </Stack>
      </CardContent>
    </Card>
  )
}

/** T16 已採用餐食紀錄：依日期回看當日各餐別的完整快照。只有按「採用」的餐食會出現在這裡。 */
export default function MealHistoryPage() {
  const [params, setParams] = useSearchParams()
  const date = params.get('date') || today()

  const dates = useQuery({
    queryKey: ['meal-plan-dates'],
    queryFn: async () => (await api.get<{ dates: { meal_date: string; count: number }[] }>('/meal-plans')).data.dates,
  })
  const plans = useQuery({
    queryKey: ['meal-plans', date],
    queryFn: async () =>
      (await api.get<{ meal_plans: MealPlanDetail[] }>('/meal-plans', { params: { date } })).data.meal_plans,
  })

  return (
    <Stack spacing={2}>
      <Typography variant="h5">已採用餐食紀錄</Typography>
      <Typography variant="body2" color="text.secondary">
        這裡只列出按「採用」保存的餐食，不代表實際已食用；僅生成或查看的菜單不會出現。
      </Typography>
      <TextField type="date" label="餐食日期" value={date} sx={{ maxWidth: 220 }}
        onChange={(e) => e.target.value && setParams({ date: e.target.value })}
        slotProps={{ inputLabel: { shrink: true } }} />
      {dates.data && dates.data.length > 0 && (
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          {dates.data.map((d) => (
            <Chip key={d.meal_date} label={`${d.meal_date}（${d.count}）`} clickable
              color={d.meal_date === date ? 'primary' : 'default'} onClick={() => setParams({ date: d.meal_date })} />
          ))}
        </Box>
      )}
      {plans.isError && <Alert severity="error">{errorMessage(plans.error)}</Alert>}
      {plans.isPending && <Typography color="text.secondary">讀取中…</Typography>}
      {plans.data && plans.data.length === 0 && (
        <Typography color="text.secondary">{date} 沒有已採用餐食紀錄。</Typography>
      )}
      {plans.data?.map((p) => <PlanCard key={p.id} plan={p} />)}
    </Stack>
  )
}
