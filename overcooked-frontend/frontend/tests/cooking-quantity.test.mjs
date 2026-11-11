import assert from 'node:assert/strict'
import test from 'node:test'
import { cookingQuantity } from '../src/live/api/cookingQuantity.ts'

test('small spoons retain exact source detail without rounding up to a full spoon', () => {
  const source = { display_amount:'0.25', display_unit:'小匙', whole_meal_grams:'3/2' }
  assert.deepEqual(cookingQuantity(source), {label:'少於 1小匙', detail:'0.25 小匙；本餐計算量：3/2 g'})
  assert.equal(source.whole_meal_grams, '3/2')
  assert.equal(cookingQuantity({display_amount:'2',display_unit:'大匙'}).label,'2大匙')
  assert.equal(cookingQuantity({display_amount:'2.5',display_unit:'大匙'}).label,'2–3大匙')
})
test('metric volume is converted without inventing a density and unknown is not zero', () => {
  assert.equal(cookingQuantity({display_amount:'0.01',display_unit:'L'}).label,'10 mL')
  assert.equal(cookingQuantity({grams:'20/3'}).label,'約 7 g')
  assert.equal(cookingQuantity({grams:null}).label,'用量未知')
  assert.equal(cookingQuantity({display_text:'少許'}).label,'少許')
  assert.equal(cookingQuantity({grams:'0'}).label,'0 g')
  assert.equal(cookingQuantity({display_amount:0.25,display_unit:'小匙',whole_meal_grams:1.5}).label,'少於 1小匙')
  assert.equal(cookingQuantity({grams:300}).label,'300 g')
  assert.equal(cookingQuantity({display_text:'約 0.01 L',whole_meal_grams:'8.5'}).label,'約 10 mL')
})
