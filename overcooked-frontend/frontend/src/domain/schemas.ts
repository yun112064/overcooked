import { z } from 'zod'
import { portionOptions } from '../data/catalog.ts'

const meal = z.enum(['breakfast', 'lunch', 'dinner'])
export const memberFields = z.object({
  name: z.string().trim().min(1, '請輸入名稱。').max(40, '名稱最多 40 字。'),
  age: z.number({ error: '請輸入年齡。' }).int('年齡須為整數。').min(0).max(120),
  sex: z.enum(['M', 'F'], { error: '請選擇計算用性別。' }),
  weight: z.number({ error: '請輸入體重。' }).positive('體重須大於 0。').max(500),
  activity: z.enum(['sedentary', 'low_active', 'active', 'highly_active'], { error: '請選擇活動程度。' }),
  portion: z.number().refine(value => portionOptions.includes(value), '請選擇有效的食量。'),
  goal: z.enum(['general', 'maintain', 'muscle_gain', 'fat_loss'], { error: '請選擇飲食目標。' }),
  allergies: z.array(z.string()), forbidden: z.array(z.string()),
})
export const memberSchema = memberFields.refine(member => member.age >= 19 || member.goal === 'general', { message: '0–18 歲僅提供一般餐食。', path: ['goal'] })
export type MemberFields = z.infer<typeof memberFields>
const member = memberFields.extend({ id: z.string(), householdId: z.string() })
const draft = z.object({
  id: z.string(), householdId: z.string(), date: z.string(), meal,
  participation: z.array(z.object({ memberId: z.string(), dining: z.boolean(), bento: z.boolean(), bentoMeal: meal })),
  budget: z.number().finite().nonnegative().nullable(), staple: z.enum(['', 'rice', 'noodles', 'bread', 'none']), dishCount: z.number().int(),
  tastes: z.array(z.string()), requested: z.array(z.string()), available: z.array(z.string()), revision: z.number().int(), updatedAt: z.string(),
})
const servingAmount = z.object({ label: z.string(), amount: z.number().finite(), unit: z.enum(['g', 'ml', '顆']) })
const recipe = z.object({
  id: z.string(), name: z.string(), kind: z.enum(['dish', 'staple']), source: z.enum(['database_menu', 'ai_generated_menu']),
  ingredients: z.array(z.object({ ingredientId: z.string(), amount: z.number().finite() })), steps: z.array(z.string()),
  energyPerN: z.number().finite(), proteinPerN: z.number().finite(), servingPerN: z.array(servingAmount),
})
const price = z.number().finite().nonnegative().nullable()
const plan = z.object({
  id: z.string(), draftId: z.string(), fingerprint: z.string(), memberFingerprint: z.string(), householdId: z.string(), date: z.string(), meal,
  source: z.enum(['database_menu', 'ai_generated_menu']), totalN: z.number().finite().positive(), recipes: z.array(recipe),
  requested: z.array(z.string()), available: z.array(z.string()), budget: z.number().finite().nonnegative(),
  purchases: z.array(z.object({ ingredientId: z.string(), name: z.string(), amount: z.number().finite(), unit: z.string(), packageAmount: z.number().positive(), packagePrice: price, packages: z.number().int().nonnegative(), purchaseCost: price, mealCost: price })),
  mealCost: z.number().finite(), purchaseCost: z.number().finite(), priceComplete: z.boolean(),
  nutrition: z.object({ energy: price, protein: price, energyTarget: z.number().finite().positive(), proteinTarget: z.number().finite().positive() }),
  servings: z.array(z.object({ memberId: z.string(), memberName: z.string(), meal, bento: z.boolean(), dishes: z.array(z.object({ recipeId: z.string(), name: z.string(), amounts: z.array(servingAmount) })) })),
  createdAt: z.string(), scope: z.literal('demo'),
})
export const appDataSchema = z.object({ version: z.literal(1), households: z.array(z.object({ id: z.string(), name: z.string() })).min(1), activeHouseholdId: z.string(), members: z.array(member), drafts: z.array(draft), records: z.array(z.object({ id: z.string(), adoptedAt: z.string(), acceptedMissingPrice: z.boolean(), plan })) })
export const authSchema = z.object({ email: z.email('請輸入有效的 Email。'), password: z.string().min(8, '密碼至少需要 8 個字元。'), confirmation: z.string() })
export type AuthFields = z.infer<typeof authSchema>
