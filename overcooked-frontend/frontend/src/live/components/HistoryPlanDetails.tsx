import { Alert, Box, Link, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material'
import type { MealResult } from '../api/types'
import { toNumber } from '../api/nutrition'
import type { SavedGenerationPlan, SavedLegacyPlan, SavedPlan } from '../history/snapshots'
import { savedPrices } from '../history/snapshots'
import DishList from './DishList'
import { DishIngredients, DishSteps, HighSodiumTag } from './DishDetail'
import PersonNutrition from './PersonNutrition'
import GeneratedNutrition, { MacroBar } from './ResultPersonNutrition'
import { GeneratedPortions, SavedPortions } from './PortionsTable'
import RecipeBook from './RecipeBook'
import ResultSections from './ResultSections'
import { Section, Stat, StatRow } from './ui'

const money = (v: string | null | undefined) => {
  const n = toNumber(v)
  return n === null ? '未知' : `${Math.round(n)} 元`
}

function SavedShopping({ prices }: { prices?: MealResult['prices'] }) {
  if (!prices || prices.status !== 'computed') return <Section title="需補買食材">
    <Alert severity="info">此紀錄未保存可用的採購明細。</Alert>
  </Section>
  const incompleteQuantity = prices.unquantified_items ?? []
  return <Section title="需補買食材" aside="採用當時的價格快照">
    <Stack spacing={2}>
      <Typography variant="caption">調味料與家中已有的不列入；以下為歷史報價。</Typography>
      {incompleteQuantity.length > 0 && <Alert severity="warning">用量未定：{incompleteQuantity.map(i => i.name).join('、')}。整餐成本未知，已知小計只包含有用量與報價的食材。</Alert>}
      {prices.items.length ? <Box sx={{ overflowX: 'auto' }}><Table size="small">
        <TableHead><TableRow><TableCell>食材</TableCell><TableCell>缺量</TableCell><TableCell>包裝</TableCell>
          <TableCell>整包採購價</TableCell><TableCell>用量成本</TableCell><TableCell>來源</TableCell></TableRow></TableHead>
        <TableBody>{prices.items.map((item, index) => <TableRow key={`${item.food_id}-${index}`}>
          <TableCell sx={{ whiteSpace: 'nowrap' }}>{item.name}</TableCell>
          <TableCell sx={{ whiteSpace: 'nowrap' }}>{item.shortage_ml != null ? `${toNumber(item.shortage_ml) ?? item.shortage_ml} mL`
            : item.shortage_grams != null ? `${toNumber(item.shortage_grams) ?? item.shortage_grams} g` : '未知'}</TableCell>
          <TableCell>{item.price_status === 'known' && item.packs != null ? `${item.package_description ?? ''} × ${item.packs}` : '待確認'}</TableCell>
          <TableCell sx={{ whiteSpace: 'nowrap' }}>{money(item.purchase_price)}</TableCell><TableCell sx={{ whiteSpace: 'nowrap' }}>{money(item.cost)}</TableCell>
          <TableCell sx={{ minWidth: 150 }}>{item.price_status === 'unknown' ? '查無價格' : item.product_url
            ? <Link href={item.product_url} target="_blank" rel="noreferrer">{item.source_name ?? item.retailer ?? item.source ?? '來源'}</Link>
            : item.source_name ?? item.retailer ?? item.source ?? '未保存來源'}
            {item.queried_at && <Typography component="p" variant="caption">{new Date(item.queried_at).toLocaleString()} 查詢</Typography>}
            {item.missing_reason && <Typography component="p" variant="caption">{item.missing_reason}</Typography>}
          </TableCell>
        </TableRow>)}</TableBody>
      </Table></Box> : <Typography variant="body2">{toNumber(prices.cost_total) === 0 && !incompleteQuantity.length ? '已知用量的食材無需補買。' : '此紀錄沒有可列出的採購品項。'}</Typography>}
      <StatRow>
        <Stat label="整包採購價" value={money(prices.purchase_total)} note={prices.purchase_total === null ? `已知 ${money(prices.known_purchase_total)}` : undefined} />
        <Stat label="用量成本" value={money(prices.cost_total)} note={prices.cost_total === null ? `已知 ${money(prices.known_cost_total)}` : undefined} />
        <Stat label="預算" value={money(prices.budget)} />
      </StatRow>
    </Stack>
  </Section>
}

function GeneratedDetails({ plan }: { plan: SavedGenerationPlan }) {
  const nutrition = plan.nutrition
  return <Stack spacing={3}>
    {plan.fallback_message && <Alert severity="info">{plan.fallback_message}</Alert>}
    <ResultSections recipes={<>
      <Section title="全家配方" aside="採用當時保存的用量">
        <RecipeBook recipes={[
          ...plan.dishes.map((dish, index) => ({ id: `${dish.standard_dish_id}-${index}`, name: dish.dish_name, category: dish.cooking_method,
            badges: <HighSodiumTag high={dish.high_sodium} sodium={dish.sodium_mg_per_serving} />,
            ingredients: dish.ingredients ? <DishIngredients ingredients={dish.ingredients} /> : <Typography>此紀錄未保存食材用量。</Typography>,
            steps: dish.steps?.length ? <DishSteps steps={dish.steps} display={dish.steps_display} /> : undefined,
            note: <>{dish.aliases.length > 0 && <Typography variant="caption">又名 {dish.aliases.join('、')}</Typography>}
              {dish.recipe_source && <Typography variant="caption" component="p">食譜來源：{dish.recipe_source.source_url
                ? <Link href={dish.recipe_source.source_url} target="_blank" rel="noreferrer">{dish.recipe_source.source_name}</Link> : dish.recipe_source.source_name}</Typography>}</>,
          })),
          ...(plan.staple ? [{ id: 'staple', name: plan.staple.label, category: '主食', ingredients: <>
            <Typography>{plan.staple.display_amount == null ? '此紀錄未保存主食用量。' : `${plan.staple.tfda_name ?? plan.staple.label} ${plan.staple.display_amount} ${plan.staple.display_unit ?? ''}`}</Typography>
            <Typography variant="caption">主食計入營養，不計入價格。</Typography>
          </> }] : []),
        ]} />
      </Section>
      {plan.previous_day && <Section title="避免重複的菜"><Typography variant="body2">採用當時的昨日比對（{plan.previous_day.previous_date ?? '未保存日期'}）：
        {plan.previous_day.excluded_dishes.length ? `排除 ${plan.previous_day.excluded_dishes.map(d => d.dish_name).join('、')}` : '沒有排除的菜'}
        {plan.previous_day.specified_repeats.length > 0 && `；指定保留 ${plan.previous_day.specified_repeats.join('、')}`}</Typography></Section>}
      {plan.knowledge?.status === 'available' && plan.knowledge.items.length > 0 && <Section title="食材小知識"><Stack spacing={2}>
        {plan.knowledge.items.map(item => <Box key={item.ingredient_id}>
          <Typography variant="subtitle2">{item.ingredient_name ?? item.ingredient_id}</Typography><Typography variant="body2">{item.explanation}</Typography>
          <Typography variant="caption">{item.citations.map((c, index) => <span key={`${c.knowledge_id}-${index}`}>{index > 0 && '、'}{c.attribution ?? `來源：${c.knowledge_id} ${c.source_version}`}
            {c.source_url && <> <Link href={c.source_url} target="_blank" rel="noreferrer">原文</Link></>}</span>)}</Typography>
        </Box>)}
      </Stack></Section>}
    </>} shopping={<SavedShopping prices={plan.prices} />}
      nutrition={nutrition ? <><MacroBar nutrition={nutrition} /><Section title="每人營養（這一餐／每日建議）"><GeneratedNutrition nutrition={nutrition} /></Section></> : <Alert severity="info">此紀錄未保存營養資料。</Alert>}
      portions={nutrition ? <GeneratedPortions result={{ nutrition }} /> : <Alert severity="info">此紀錄未保存個人分餐資料。</Alert>} />
  </Stack>
}

function LegacyDetails({ plan }: { plan: SavedLegacyPlan }) {
  const s = plan.snapshot
  const selected = s.selected ?? []
  const names = new Map((s.people ?? []).map(p => [p.member_id, p.name ?? '已刪除成員']))
  const dishNames = new Map(selected.map(r => [r.recipe_id, r.recipe_name]))
  return <ResultSections recipes={selected.length ? <Section title="全家配方" aside="採用當時保存的用量">
    <DishList selected={selected} dishes={s.dishes} staple={s.staple ?? null} stapleName={s.staple_choice ?? null} />
  </Section> : <Alert severity="info">此紀錄未保存完整食譜。</Alert>}
    shopping={<SavedShopping prices={savedPrices(s)} />}
    nutrition={<Section title="每人營養（這一餐／每日建議）">
      <StatRow><Stat label="整餐熱量" value={s.nutrition?.totals?.energy_kcal ?? '未知'} unit="kcal" />
        <Stat label="整餐蛋白質" value={s.nutrition?.totals?.protein_g ?? '未知'} unit="g" /></StatRow>
      {s.people?.length ? <PersonNutrition people={s.people} meta={s.daily_reference_meta} names={names} dishName={dishNames} staple={s.staple_choice ?? null} />
        : <Alert severity="info">此紀錄未保存個人營養資料。</Alert>}
    </Section>}
    portions={<SavedPortions people={s.people} />} />
}

/** 歷史檢視只讀採用快照，頁籤不送出生成、查價或採用請求。 */
export default function HistoryPlanDetails({ plan }: { plan: SavedPlan }) {
  return plan.result_version != null ? <GeneratedDetails plan={plan as SavedGenerationPlan} /> : <LegacyDetails plan={plan as SavedLegacyPlan} />
}
