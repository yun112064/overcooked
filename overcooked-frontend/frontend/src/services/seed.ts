import type { AppData, Member } from '../domain/types.ts'
import { createDraft, generateDemoPlan, localDate, previousDate } from '../domain/rules.ts'
import { ingredients, recipes, staples } from '../data/catalog.ts'

export function seedData(): AppData {
  const householdId = 'household-demo'
  const members: Member[] = [
    { id: 'member-mei', householdId, name: '小美', age: 35, sex: 'F', weight: 55, activity: 'low_active', portion: 1, goal: 'maintain', allergies: ['shrimp'], forbidden: [] },
    { id: 'member-ming', householdId, name: '阿明', age: 38, sex: 'M', weight: 72, activity: 'active', portion: 1.15, goal: 'muscle_gain', allergies: [], forbidden: ['cilantro'] },
    { id: 'member-an', householdId, name: '小安', age: 10, sex: 'M', weight: 30, activity: 'active', portion: 0.7, goal: 'general', allergies: [], forbidden: [] },
  ]
  const yesterday = createDraft(householdId, members, previousDate(localDate()))
  Object.assign(yesterday, { budget: 500, staple: 'rice', requested: ['chicken-steak', 'broccoli', 'clear-soup'] })
  const first = generateDemoPlan(yesterday, members, [], recipes, staples.rice, ingredients)
  const records: AppData['records'] = [{ id: first.id, plan: first, acceptedMissingPrice: false, adoptedAt: `${first.date}T10:00:00.000Z` }]
  const today = createDraft(householdId, members)
  Object.assign(today, { budget: 300, staple: 'rice', requested: ['eggs', 'cabbage', 'mushroom-soup'], available: ['egg', 'tomato', 'cabbage', 'oil', 'salt', 'garlic'] })
  today.participation[1].bento = true
  const second = generateDemoPlan(today, members, records, recipes, staples.rice, ingredients)
  records.push({ id: second.id, plan: second, acceptedMissingPrice: false, adoptedAt: `${second.date}T10:00:00.000Z` })
  return { version: 1, households: [{ id: householdId, name: '我的家庭' }], activeHouseholdId: householdId, members, drafts: [], records }
}
