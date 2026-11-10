import { useState } from 'react'
import type { CSSProperties } from 'react'
import type { Plan } from '../domain/types.ts'
import { nutritionErrors } from '../domain/rules.ts'
import { Modal } from './Common.tsx'

export function NutritionFeedback({ plan, modify }: { plan: Plan; modify: () => void }) {
  const errors = nutritionErrors(plan)
  const [open, setOpen] = useState(errors.length > 0)

  if(errors.length === 0) {
    return <div className="celebration" aria-hidden="true">{Array.from({ length: 24 }, (_, index) =>
      <i key={index} style={{ '--particle-x': `${4 + index * 4}%`, '--particle-delay': `${index % 6 * 0.07}s`, '--particle-turn': `${index % 2 ? '-' : ''}${240 + index * 13}deg`, '--particle-shift': `${index % 2 ? '-' : ''}${20 + index % 5 * 9}px` } as CSSProperties} />
    )}</div>
  }

  return open && <Modal title="營養未達標" onClose={() => setOpen(false)}>
    <ul className="goal-reasons">{errors.map(error => <li key={error}>{error}</li>)}</ul>
    <div className="modal-actions">
      <button className="button" type="button" onClick={() => setOpen(false)}>維持原畫面</button>
      <button className="button primary" type="button" onClick={modify}>調整餐點</button>
    </div>
  </Modal>
}
