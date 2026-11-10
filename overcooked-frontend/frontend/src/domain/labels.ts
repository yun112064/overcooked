import type { Plan } from './types.ts'

export const sourceLabel = (plan: Pick<Plan, 'source'>) => plan.source === 'database_menu' ? '精選食譜' : 'AI食譜'
