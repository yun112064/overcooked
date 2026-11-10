import type { AdoptedMeal, Draft, Member, Plan, Scenario } from '../domain/types.ts'
import { ingredients, recipes, staples } from '../data/catalog.ts'
import { adoptionErrors, fingerprint, generateDemoPlan, PlannerError } from '../domain/rules.ts'

export interface PlannerGateway {
  generate(draft: Draft, members: Member[], records: AdoptedMeal[], scenario: Scenario, signal?: AbortSignal): Promise<Plan>
  recognize(file: File, scenario: Scenario, signal?: AbortSignal): Promise<string>
  adopt(plan: Plan, draft: Draft, members: Member[], records: AdoptedMeal[], acceptMissingPrice: boolean): AdoptedMeal
}
function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if(signal?.aborted) { reject(new DOMException('Aborted', 'AbortError')); return }
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, ms)
    signal?.addEventListener('abort', abort, { once: true })
  })
}
export const demoPlanner: PlannerGateway = {
  async generate(draft, members, records, scenario, signal) {
    await delay(900, signal)
    return generateDemoPlan(draft, members, records, recipes, draft.staple && draft.staple !== 'none' ? staples[draft.staple] : undefined, ingredients, scenario)
  },
  async recognize(file, scenario, signal) {
    if(!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('請選擇 JPG、PNG 或 WebP 照片。')
    await delay(650, signal)
    if(scenario === 'recognition-failure' || scenario === 'service-failure') throw new Error('辨識未成功，請重試或手動加入食材。')
    return '紅蘿蔔'
  },
  adopt(plan, draft, members, records, acceptMissingPrice) {
    const existing = records.find(record => record.id === plan.id)
    if(existing) {
      if(existing.plan.fingerprint !== plan.fingerprint || existing.plan.fingerprint !== fingerprint(draft)) throw new PlannerError('validation', '條件已變更，請重新產生菜單。')
      return existing
    }
    const errors = adoptionErrors(plan, draft, members, records, acceptMissingPrice)
    if(errors.length) throw new PlannerError('validation', errors.join('\n'))
    return { id: plan.id, plan: structuredClone(plan), acceptedMissingPrice: !plan.priceComplete && acceptMissingPrice, adoptedAt: new Date().toISOString() }
  },
}
