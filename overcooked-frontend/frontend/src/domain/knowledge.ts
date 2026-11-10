import snapshot from '../data/knowledge-snapshot.json' with { type: 'json' }
import type { Recipe } from './types.ts'

export interface IngredientKnowledge {
  localIngredientId: string
  recipeIds?: string[]
  knowledgeId: string
  foodId: string
  ingredientName: string
  excerpt: string
  conditions: string
  sourceUrl: string
  sourceLabel: string
  evidenceType: string
}

const records: IngredientKnowledge[] = snapshot.records

export function knowledgeForRecipe(recipe: Pick<Recipe, 'id' | 'ingredients'>): IngredientKnowledge[] {
  const ids = new Set(recipe.ingredients.map(item => item.ingredientId))
  return records.filter(record => ids.has(record.localIngredientId) && (!record.recipeIds || record.recipeIds.includes(recipe.id)))
}
