import { useId } from 'react'
import { Help } from './Common.tsx'
import { formulae } from '../data/formulae.ts'

export function PieChartPreview({ title }: { title: string }) {
  return <figure className="pie-figure">
    <figcaption>{title}</figcaption>
    <div className="pie-preview" role="img" aria-label={`${title}，比例待確認`}>
      <svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="84" fill="var(--lavender)" stroke="var(--purple)" strokeWidth="1.5" strokeDasharray="5 7" /><path d="M100 28V100L155 155" fill="none" stroke="var(--purple)" strokeWidth="1.5" opacity=".35" /></svg>
      <span>比例待確認</span>
    </div>
  </figure>
}

export function NutritionBar({ name, value, target, unit }: { name: string; value: number | null; target: number; unit: string }) {
  const id = useId()
  const validTarget = Number.isFinite(target) && target > 0
  const knownValue = value != null && Number.isFinite(value)
  const delta = knownValue && validTarget ? (value - target) / target * 100 : null
  const max = Math.max(validTarget ? target * 1.3 : 1, knownValue ? value * 1.08 : 1)
  const targetPosition = validTarget ? target / max * 100 : null
  const description = `${name}：估算 ${knownValue ? `${value} ${unit}` : '未取得'}，目標 ${target} ${unit}，差距 ${delta == null ? '無法計算' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`}`

  return <div className="nutrition-comparison" aria-labelledby={`${id}-label`}>
    <div className="nutrition-comparison-heading">
      <h3 id={`${id}-label`}>{name}</h3>
      <span className={`nutrition-state ${delta != null && Math.abs(delta) <= 10 + 1e-8 ? 'met' : 'unmet'}`}>{delta == null ? '資料不完整' : Math.abs(delta) <= 10 + 1e-8 ? '符合' : '未符合'}</span>
    </div>
    <div className="nutrition-values"><span>估算 <strong>{knownValue ? value.toLocaleString('zh-TW') : '未取得'}</strong>{knownValue && ` ${unit}`}<Help label={`${name}估算計算方法`} text={formulae.estimate} /></span><span>目標 <strong>{target.toLocaleString('zh-TW')}</strong> {unit}<Help label={`${name}目標計算方法`} text={formulae.target} /></span></div>
    <div className="nutrition-bar-space" role="img" aria-label={description}>
      <div className="nutrition-bar-track">{knownValue && <span className="nutrition-bar-fill" style={{ width: `${Math.max(0, value) / max * 100}%` }} />}</div>
      {targetPosition != null && <span className="nutrition-target-marker" style={{ left: `${targetPosition}%` }}><span>目標</span></span>}
    </div>
    <p className="nutrition-delta">差距 {delta == null ? '無法計算' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`}<Help label={`${name}差距計算方法`} text={formulae.difference} /></p>
  </div>
}
