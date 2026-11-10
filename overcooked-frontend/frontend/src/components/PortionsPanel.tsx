import { useId, useState } from 'react'
import type { Plan } from '../domain/types.ts'
import { mealLabels } from '../data/catalog.ts'
import { formulae } from '../data/formulae.ts'
import { Help } from './Common.tsx'
import { PieChartPreview } from './Charts.tsx'

export function PortionsPanel({ plan }: { plan: Plan }) {
  const id = useId()
  const [view, setView] = useState<'family' | 'person'>('family')
  const [recipeId, setRecipeId] = useState(plan.recipes[0]?.id ?? '')
  const [personIndex, setPersonIndex] = useState(0)
  const recipe = plan.recipes.find(item => item.id === recipeId) ?? plan.recipes[0]
  const person = plan.servings[personIndex] ?? plan.servings[0]

  return <>
    <div className="section-title compact"><h2>分餐建議</h2><span className="muted small">熟食份量 <Help label="分餐重量計算方法" text={formulae.serving} /></span></div>
    <div className="portion-table-scroll"><table className="data-table portions-table">
      <caption className="visually-hidden">每位成員與便當的分餐份量</caption>
      <thead><tr><th>成員／餐份</th>{plan.recipes.map(item => <th key={item.id}>{item.name}</th>)}</tr></thead>
      <tbody>{plan.servings.map((row, index) => <tr key={`${row.memberId}-${index}`}>
        <td data-label="成員／餐份"><span>{row.memberName}<span className={`subline ${row.bento ? 'purple' : ''}`}>{mealLabels[row.meal]}{row.bento ? '便當' : ''}</span></span></td>
        {plan.recipes.map(item => <td data-label={item.name} key={item.id}>{row.dishes.find(dish => dish.recipeId === item.id)?.amounts.map((amount, amountIndex) =>
          <span className="serving-amount" key={amountIndex}>{amount.label && `${amount.label} `}{amount.amount} {amount.unit}</span>
        )}</td>)}
      </tr>)}</tbody>
    </table></div>
    <section className="portion-visual-panel">
      <div className="view-switch" role="group" aria-label="分餐閱讀角度">
        <button type="button" aria-pressed={view === 'family'} onClick={() => setView('family')}>全家分配</button>
        <button type="button" aria-pressed={view === 'person'} onClick={() => setView('person')}>個人餐盤</button>
      </div>
      <div className="portion-visual-content">
        <label className="field" htmlFor={`${id}-subject`}>{view === 'family' ? '菜色' : '成員／餐份'}
          {view === 'family' ? <select id={`${id}-subject`} value={recipe?.id ?? ''} onChange={event => setRecipeId(event.target.value)}>{plan.recipes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
            : <select id={`${id}-subject`} value={personIndex} onChange={event => setPersonIndex(Number(event.target.value))}>{plan.servings.map((row, index) => <option key={`${row.memberId}-${index}`} value={index}>{row.memberName} · {mealLabels[row.meal]}{row.bento ? '便當' : ''}</option>)}</select>}
        </label>
        <PieChartPreview title={view === 'family' ? recipe?.name ?? '全家分配' : person ? `${person.memberName} · ${mealLabels[person.meal]}${person.bento ? '便當' : ''}` : '個人餐盤'} />
      </div>
    </section>
    <p className="provenance">分餐份量為示範；正式分餐需由後端計算並驗證。</p>
  </>
}
