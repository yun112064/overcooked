export type Meal = 'breakfast' | 'lunch' | 'dinner'
export type Goal = 'general' | 'maintain' | 'muscle_gain' | 'fat_loss'
export type Activity = 'sedentary' | 'low_active' | 'active' | 'highly_active'
export type Staple = 'rice' | 'noodles' | 'bread' | 'none'
export type Scenario = 'normal' | 'service-failure' | 'no-solution' | 'missing-price' | 'missing-nutrition' | 'nutrition-range' | 'save-failure' | 'recognition-failure'
export type SaveStatus = 'saving' | 'saved' | 'failed'
export type ResultTab = 'recipes' | 'shopping' | 'nutrition' | 'portions'
export interface Household { id: string; name: string }
export interface Member {
  id: string; householdId: string; name: string; age: number; sex: 'M' | 'F'; weight: number
  activity: Activity; portion: number; goal: Goal; allergies: string[]; forbidden: string[]
}
export interface Participation { memberId: string; dining: boolean; bento: boolean; bentoMeal: Meal }
export interface Draft {
  id: string; householdId: string; date: string; meal: Meal; participation: Participation[]
  budget: number | null; staple: Staple | ''; dishCount: number; tastes: string[]
  requested: string[]; available: string[]; revision: number; updatedAt: string
}
export interface Ingredient { id: string; name: string; aliases?: string[]; unit: 'g' | 'ml'; packageAmount: number; packagePrice: number }
export interface Quantity { ingredientId: string; amount: number }
export interface ServingAmount { label: string; amount: number; unit: 'g' | 'ml' | '顆' }
export interface Recipe {
  id: string; name: string; kind: 'dish' | 'staple'; ingredients: Quantity[]; steps: string[]
  energyPerN: number; proteinPerN: number; servingPerN: ServingAmount[]; source: 'database_menu' | 'ai_generated_menu'
}
export interface PurchaseRow {
  ingredientId: string; name: string; amount: number; unit: string; packageAmount: number
  packagePrice: number | null; packages: number; purchaseCost: number | null; mealCost: number | null
}
export interface ServingRow { memberId: string; memberName: string; meal: Meal; bento: boolean; dishes: { recipeId: string; name: string; amounts: ServingAmount[] }[] }
export interface Plan {
  id: string; draftId: string; fingerprint: string; memberFingerprint: string; householdId: string; date: string; meal: Meal
  source: Recipe['source']; totalN: number; recipes: Recipe[]; requested: string[]; available: string[]
  budget: number; purchases: PurchaseRow[]; mealCost: number; purchaseCost: number; priceComplete: boolean
  nutrition: { energy: number | null; protein: number | null; energyTarget: number; proteinTarget: number }
  servings: ServingRow[]; createdAt: string; scope: 'demo'
}
export interface AdoptedMeal { id: string; adoptedAt: string; acceptedMissingPrice: boolean; plan: Plan }
export interface AppData { version: 1; households: Household[]; activeHouseholdId: string; members: Member[]; drafts: Draft[]; records: AdoptedMeal[] }
export interface Session { email: string }
