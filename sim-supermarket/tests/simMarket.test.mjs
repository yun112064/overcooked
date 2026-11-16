import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = fs.readFileSync(new URL('../src/api/simMarket.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } })
const { prepareCart, packCount, cartStorageKey } = await import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString('base64')}`)
const product = { food_id: 'milk', name: '牛奶', category: '豆蛋乳品', package_grams: null, package_ml: 500, price_cents: 15000 }
const catalog = { recipe_release_id: 'test-release', products: [product] }
const line = (grams, ml) => ({ food_id: 'milk', name: '牛奶', shortage_grams: grams, shortage_ml: ml })
const plan = items => ({ snapshot: { market_shortages: { recipe_release_id: 'test-release', items } } })

test('backend long decimal shortages remain exact and enter the cart', () => {
  const mushroom = { food_id: 'mushroom', name: '鴻喜菇', package_grams: 100, price_cents: 2700 }
  const source = plan([{ food_id: 'mushroom', name: '鴻喜菇', shortage_grams: '25.00000000000000000000000000' }])
  const cart = prepareCart(source, { recipe_release_id: 'test-release', products: [mushroom] })
  assert.deepEqual(cart.quantities, { mushroom: 1 })
  assert.deepEqual(cart.unavailable, [])
  assert.equal(packCount('100.00000000000000000000000001', 100), 2)
  assert.equal(packCount('0.' + '0'.repeat(128) + '1', 100), null)
})

test('volume requirements sum exactly before whole-package rounding', () => {
  const cart = prepareCart(plan([line(null, '100'), line(null, '400')]), catalog)
  assert.deepEqual(cart.quantities, { milk: 1 })
  assert.equal(cart.shortages.get('milk'), '500 mL')
  assert.equal(prepareCart(plan([line(null, '500.000001')]), catalog).quantities.milk, 2)
  assert.equal(prepareCart(plan([line(null, '1/3'), line(null, '1499/3')]), catalog).quantities.milk, 1)
})

test('paired gram and volume values represent one requirement', () => {
  assert.equal(prepareCart(plan([line('110', '100')]), catalog).quantities.milk, 1)
})

test('unconvertible mixed demands block that food instead of silently underbuying', () => {
  for (const items of [[line(null, '100'), line('50', null)], [line('50', null), line(null, '100')]]) {
    const cart = prepareCart(plan(items), catalog)
    assert.deepEqual(cart.quantities, {})
    assert.equal(cart.unavailable.length, 1)
    assert.match(cart.unavailable[0].reason, /單位無法換算/)
  }
})

test('legacy gram-only data remains supported and duplicate rows are summed', () => {
  const legacyCatalog = { ...catalog, products: [{ ...product, package_grams: 500, package_ml: undefined }] }
  const cart = prepareCart(plan([line('100', null), line('400', null)]), legacyCatalog)
  assert.deepEqual(cart.quantities, { milk: 1 })
  assert.equal(cart.shortages.get('milk'), '500 g')
  assert.equal(packCount('1501/3', 500), 2)
})

test('unknown amounts, invalid packages and changed recipe releases stay unavailable', () => {
  const unknown = plan([line(null, '100')])
  unknown.snapshot.market_shortages.unquantified_items = [{ food_id: 'milk', name: '牛奶' }]
  assert.deepEqual(prepareCart(unknown, catalog).quantities, {})
  assert.deepEqual(prepareCart(plan([line(null, '100')]), { ...catalog, recipe_release_id: 'other' }).quantities, {})
  assert.deepEqual(prepareCart(plan([line('100', '100')]), { ...catalog, products: [{ ...product, package_grams: 100 }] }).quantities, {})
  assert.equal(packCount('-100', 500), null)
  assert.equal(packCount('1/0', 500), null)
})

test('cart persistence is scoped by authorized handoff, release, plan and package specification', () => {
  const base = cartStorageKey(['handoff-a', 'plan'], catalog)
  assert.match(base, /^sim-market:v2:/)
  assert.notEqual(base, cartStorageKey(['handoff-b', 'plan'], catalog))
  assert.notEqual(base, cartStorageKey(['handoff-a', 'other-plan'], catalog))
  assert.notEqual(base, cartStorageKey(['handoff-a', 'plan'], { ...catalog, products: [{ ...product, package_ml: 1000 }] }))
  assert.notEqual(base, cartStorageKey(['handoff-a', 'plan'], { ...catalog, products: [{ ...product, package_ml: null, package_grams: 500 }] }))
})
