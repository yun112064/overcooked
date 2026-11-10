import { useId, useState } from 'react'
import { BookOpen, ChartNoAxesColumn, ShoppingBasket, Utensils } from 'lucide-react'
import type { Plan, ResultTab } from '../domain/types.ts'
import { mealLabels } from '../data/catalog.ts'
import { money, round } from '../domain/rules.ts'
import { NutritionBar } from './Charts.tsx'
import { RecipeDetail } from './RecipeDetail.tsx'
import { PortionsPanel } from './PortionsPanel.tsx'
import { Help } from './Common.tsx'
import { formulae } from '../data/formulae.ts'

export function PlanView({ plan }: { plan: Plan }) {
  const id = useId(), [tab, setTab] = useState<ResultTab>('recipes'), [selected, setSelected] = useState(plan.recipes[0]?.id)
  const recipe = plan.recipes.find(item => item.id === selected) ?? plan.recipes[0]
  const tabs = [{ key: 'recipes', label: '食譜', icon: BookOpen }, { key: 'shopping', label: '採購', icon: ShoppingBasket }, { key: 'nutrition', label: '營養', icon: ChartNoAxesColumn }, { key: 'portions', label: '分餐', icon: Utensils }] as const
  return <><dl className="plan-summary"><div><dt>餐食日期</dt><dd>{plan.date.replaceAll('-', ' / ')} · {mealLabels[plan.meal]}</dd></div><div><dt>用餐成員</dt><dd>{plan.servings.filter(row => !row.bento).map(row => row.memberName).join('、') || '僅準備便當'}{plan.servings.some(row => row.bento) && <span className="subline">另帶便當：{plan.servings.filter(row => row.bento).map(row => `${row.memberName} · ${mealLabels[row.meal]}`).join('、')}</span>}</dd></div><div><dt>合計份量</dt><dd>{plan.totalN}n<Help label="合計份量計算方法" text={formulae.portions} /><span className="subline">主食：{plan.recipes.find(item => item.kind === 'staple')?.name ?? '不需要主食'}</span></dd></div></dl><div className="result-tabs" role="tablist" aria-label="菜單內容">{tabs.map(({ key, label, icon: Icon }, index) => <button key={key} type="button" role="tab" id={`${id}-${key}-tab`} aria-controls={`${id}-${key}`} aria-selected={tab === key} onClick={() => setTab(key)} onKeyDown={event => { let next: number | undefined; if(event.key === 'ArrowRight') next = (index + 1) % tabs.length; if(event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length; if(event.key === 'Home') next = 0; if(event.key === 'End') next = tabs.length - 1; if(next !== undefined) { event.preventDefault(); setTab(tabs[next].key); document.getElementById(`${id}-${tabs[next].key}-tab`)?.focus() } }}><Icon size={18} />{label}</button>)}</div>
    <section className="panel" id={`${id}-recipes`} role="tabpanel" aria-labelledby={`${id}-recipes-tab`} hidden={tab !== 'recipes'}><div className="recipe-layout"><nav className="recipe-list" aria-label="本次菜色">{plan.recipes.map(item => <button type="button" key={item.id} aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}><Utensils size={19} /><span>{item.name}{item.kind === 'staple' && <small>主食</small>}{plan.requested.includes(item.id) && <small>指定菜色</small>}</span></button>)}</nav>{recipe && <RecipeDetail key={recipe.id} recipe={recipe} plan={plan} />}</div></section>
    <section className="panel" id={`${id}-shopping`} role="tabpanel" aria-labelledby={`${id}-shopping-tab`} hidden={tab !== 'shopping'}><div className="section-title"><h2>所需食材</h2><span className="muted small">預算 {money(plan.budget)}</span></div>{plan.purchases.length ? <table className="data-table"><caption className="visually-hidden">所需食材與價格</caption><thead><tr>{['食材', '需求量', '包裝規格', '需購數量', '採購金額', '本餐成本'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{plan.purchases.map(row => <tr key={row.ingredientId}><td data-label="食材">{row.name}</td><td data-label="需求量">{row.amount} {row.unit}</td><td data-label="包裝規格">{row.packageAmount} {row.unit}／包</td><td data-label="需購數量">{row.packages} 包</td><td data-label="採購金額">{money(row.purchaseCost)}</td><td data-label="本餐成本">{money(row.mealCost == null ? null : round(row.mealCost))}</td></tr>)}</tbody></table> : <div className="empty-state"><ShoppingBasket size={30} /><p>無需採購</p></div>}<dl className="cost-totals"><div className="meal-cost-primary"><dt>{plan.priceComplete ? '本餐成本' : '已知本餐成本'}</dt><dd>{money(plan.mealCost)}<Help label="本餐成本計算方法" text={formulae.mealCost} /></dd></div><div className="purchase-cost-secondary"><dt>{plan.priceComplete ? '實際購買支出' : '已知實際購買支出'}</dt><dd>{money(plan.purchaseCost)}<Help label="實際購買支出計算方法" text={formulae.purchaseCost} /></dd></div></dl><p className="provenance">價格來源：示範資料 · 計算時間：{new Date(plan.createdAt).toLocaleString('zh-TW')}</p></section>
    <section className="panel" id={`${id}-nutrition`} role="tabpanel" aria-labelledby={`${id}-nutrition-tab`} hidden={tab !== 'nutrition'}>
      <div className="section-title compact"><h2>營養估算</h2><span className="muted small">全家合計{plan.servings.some(row => row.bento) ? '，包含便當' : ''}</span></div>
      <div className="nutrition-charts">
        <NutritionBar name="熱量" value={plan.nutrition.energy} target={plan.nutrition.energyTarget} unit="大卡" />
        <NutritionBar name="蛋白質" value={plan.nutrition.protein} target={plan.nutrition.proteinTarget} unit="g" />
      </div>
      <p className="muted small">熱量與蛋白質各自需在目標的 ±10% 內。</p>
      <p className="provenance">營養與目標數值為示範，未計算個人營養需求。</p>
    </section>
    <section className="panel" id={`${id}-portions`} role="tabpanel" aria-labelledby={`${id}-portions-tab`} hidden={tab !== 'portions'}><PortionsPanel plan={plan} /></section></>
}
