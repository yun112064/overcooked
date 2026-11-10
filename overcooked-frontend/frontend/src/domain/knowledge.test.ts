import { test } from 'node:test'
import assert from 'node:assert/strict'
import { knowledgeForRecipe } from './knowledge.ts'

const recipe = (id: string, ingredientIds: string[]) => ({ id, ingredients: ingredientIds.map(ingredientId => ({ ingredientId, amount: 100 })) })

test('knowledge only follows the ingredients in the selected recipe, without fabricated fallback', () => {
  const tomato = knowledgeForRecipe(recipe('eggs', ['tomato']))
  assert.equal(tomato.length, 1)
  assert.equal(tomato[0].foodId, 'FOOD001016')
  assert.ok(tomato[0].sourceUrl.startsWith('https://'))
  assert.deepEqual(knowledgeForRecipe(recipe('staple-rice', ['rice'])), [])
  assert.deepEqual(knowledgeForRecipe(recipe('eggs', [])), [])
})

test('chicken breast knowledge is not generalized to other chicken recipes', () => {
  assert.equal(knowledgeForRecipe(recipe('chicken-steak', ['chicken'])).length, 1)
  assert.deepEqual(knowledgeForRecipe(recipe('mushroom-soup', ['chicken'])), [])
})

test('specific-population research retains its population and evidence limits', () => {
  const [broccoli] = knowledgeForRecipe(recipe('broccoli', ['broccoli']))
  assert.equal(broccoli.foodId, 'FOOD000588')
  assert.match(broccoli.excerpt, /吸菸者/)
  assert.match(broccoli.conditions, /250 g\/日、10天/)
  assert.match(broccoli.conditions, /不能外推/)
})
