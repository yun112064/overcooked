import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import { withinTarget, toNumber } from '../src/live/api/nutrition.ts'
import { parseSse } from '../src/live/api/assistant.ts'
import { historyDate, shiftMonth } from '../src/live/history/calendar.ts'
import { savedPrices } from '../src/live/history/snapshots.ts'
import { MAX_DISHES, randomDish } from '../src/live/selection/randomDish.ts'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = process.env.CONTRACT_REPO_ROOT || resolve(frontend, '../..')
const source = join(repo, 'frontend/src')
const live = join(frontend, 'src/live')
const liveName = name => name === 'components/MemberFormDialog.tsx' ? 'components/MemberForm.tsx' : name
function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
    ? files(join(dir, entry.name)) : [join(dir, entry.name)])
}
function calls(path, full = false, argumentsOnly = false, sourceText = readFileSync(path, 'utf8')) {
  const ast = ts.createSourceFile(path, sourceText, ts.ScriptTarget.Latest, true)
  const found = []
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && (node.expression.expression.getText(ast) === 'api'
        || (full && node.expression.expression.getText(ast) === 'form' && node.expression.name.text === 'append'))) {
      const request = argumentsOnly ? `${node.expression.getText(ast)}(${node.arguments.map(arg => arg.getText(ast)).join(',')})` : node.getText(ast)
      found.push(full ? request.replace(/\s/g, '') : [node.expression.name.text, node.arguments[0]?.getText(ast).replace(/\s/g, '')])
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
  return found.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
}

function baselineCalls(name, full = false) {
  // Result pages intentionally removed knowledge tips and their retry action.
  // Compare every remaining request, including full bodies and idempotency keys.
  return calls(join(source, name), full).filter(call => {
    const request = full ? call : call[1]
    return !(name === 'components/MenuPreviewPanel.tsx' && request.includes('/knowledge-tips'))
      && !(name === 'components/MealResultView.tsx' && request.includes('/knowledge-retry'))
  })
}

test('the legacy frontend has no local changes relative to the current checkout', () => {
  // The original 835b4e7 snapshot is historical evidence. Later main commits
  // intentionally updated that frontend; compare with this checkout instead.
  execFileSync('git', ['diff', '--exit-code', 'HEAD', '--', 'frontend'], { cwd: repo, stdio: 'pipe' })
})
test('every existing API method and URL is retained in the isolated frontend', () => {
  for (const file of files(source).filter(path => /\.tsx?$/.test(path) && !path.endsWith('main.tsx'))) {
    if (relative(source, file) === 'pages/MealHistoryPage.tsx') continue // trend reads are shared with GenerationHistoryPage
    const target = join(live, liveName(relative(source, file)))
    assert.deepEqual(calls(target), baselineCalls(relative(source, file)), relative(source, file))
  }
})
test('API models, decimal handling, credentials, SSE and polling stay on the existing contracts', () => {
  for (const name of ['api/client.ts', 'api/types.ts', 'api/nutrition.ts', 'api/resultNutrition.ts',
    'api/assistant.ts']) {
    assert.equal(readFileSync(join(live, name), 'utf8'), readFileSync(join(source, name), 'utf8'), name)
  }
  for (const name of ['components/MenuPreviewPanel.tsx', 'components/MealResultView.tsx',
    'components/GenerationPanel.tsx', 'components/RecognitionCapture.tsx', 'pages/MealRequestEditor.tsx',
    'pages/HouseholdPage.tsx', 'pages/MembersPage.tsx', 'components/MemberFormDialog.tsx', 'components/IngredientPicker.tsx', 'components/RecipeCatalogPicker.tsx', 'pages/RecipePickerPage.tsx', 'pages/MealRequestsPage.tsx']) {
    assert.deepEqual(calls(join(live, liveName(name)), true), baselineCalls(name, true), `${name}: request bodies, versions and idempotency keys`)
  }
  // HomePage was added by the isolated frontend and has no legacy counterpart.
  const home = join(live, 'pages/HomePage.tsx')
  const committedHome = execFileSync('git', ['show', 'HEAD:overcooked-frontend/frontend/src/live/pages/HomePage.tsx'], { cwd: repo, encoding: 'utf8' })
  assert.deepEqual(calls(home, true), calls(home, true, false, committedHome), 'HomePage: existing creation and read contracts')
  for (const name of ['vite.config.ts', 'Caddyfile']) {
    assert.equal(readFileSync(join(frontend, name), 'utf8'), readFileSync(join(repo, 'frontend', name), 'utf8'), name)
  }
})
test('member editor retains validation, payload mapping and the existing read contracts', () => {
  function declaration(path, name) {
    const ast = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true)
    let code
    function visit(node) {
      if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(ast) === name) {
        code = node.getText(ast).replace(/\s/g, '')
      }
      ts.forEachChild(node, visit)
    }
    visit(ast)
    assert.ok(code, `${path}: ${name} must exist`)
    return code
  }
  for (const name of ['today', 'ageOn', 'buildSchema', 'toForm', 'FIELD_MAP', 'body']) {
    assert.equal(declaration(join(live, 'components/MemberForm.tsx'), name),
      declaration(join(source, 'components/MemberFormDialog.tsx'), name), name)
  }
  assert.deepEqual(calls(join(live, 'pages/MemberEditorPage.tsx'), true),
    calls(join(source, 'pages/MembersPage.tsx'), true).filter(call => call.startsWith('api.get')))
})
test('one-sided nutrition thresholds and unknown/exact fraction values are preserved', () => {
  assert.equal(withinTarget('energy_kcal', 0.5), true)
  assert.equal(withinTarget('energy_kcal', 1.1), true)
  assert.equal(withinTarget('energy_kcal', 1.1001), false)
  assert.equal(withinTarget('protein_g', 1.8), true)
  assert.equal(withinTarget('protein_g', 0.9), true)
  assert.equal(withinTarget('protein_g', 0.899), false)
  assert.equal(toNumber(null), null)
  assert.equal(toNumber('3/2'), 1.5)
})
test('SSE chunks retain incomplete events and deliver references and done events', () => {
  const first = parseSse('event: delta\ndata: {"text":"你好"}\n\nevent: done\ndata: {"type":')
  assert.deepEqual(first.events, [{ event: 'delta', data: { text: '你好' } }])
  const next = parseSse(first.rest + '"answer","citations":[1]}\n\n')
  assert.deepEqual(next.events, [{ event: 'done', data: { type: 'answer', citations: [1] } }])
  assert.equal(next.rest, '')
})
test('official mode does not import demo persistence or simulated generation', () => {
  for (const file of files(live).filter(path => /\.tsx?$/.test(path))) {
    const text = readFileSync(file, 'utf8')
    assert.doesNotMatch(text, /today-eat-demo|demoPlanner|generateDemoPlan/, relative(live, file))
    assert.doesNotMatch(text, /from\s+['"][^'"]*(?:\.\.\/){3,}/, relative(live, file))
  }
})


test('history keeps the date, day-detail and formal trend request contracts after sharing the trend component', () => {
  const names = ['pages/MealHistoryPage.tsx', 'pages/GenerationHistoryPage.tsx']
  const requests = root => [...new Set(names.flatMap(name => calls(join(root, name), true, true)))].sort()
  assert.deepEqual(requests(live), requests(source))
  assert.deepEqual(calls(join(live, 'components/HistoryPlanDetails.tsx')), [])
})
test('history calendar clamps month ends and rejects invalid URL dates', () => {
  assert.equal(shiftMonth('2026-01-31', 1), '2026-02-28')
  assert.equal(shiftMonth('2024-01-31', 1), '2024-02-29')
  assert.equal(shiftMonth('2026-12-31', 1), '2027-01-31')
  assert.equal(shiftMonth('2026-01-31', -1), '2025-12-31')
  const now = new Date(2026, 10, 15, 12)
  for (const invalid of [null, '', 'bad', '2026-02-29', '2026-13-01', '2026-1-01']) assert.equal(historyDate(invalid, now), '2026-11-15')
  assert.equal(historyDate('2024-02-29', now), '2024-02-29')
  assert.equal(historyDate('2027-01-01', now), '2027-01-01')
})
test('legacy saved shopping preserves zero, unknowns, package spend and original quote timestamps', () => {
  assert.equal(savedPrices({}), undefined)
  const snapshot = { dishes: [{ ingredients: [{ food_id: 'tomato', name: '番茄' }] }], cost: {
    status: 'computed', cost_total: '0', purchase_total: '20', known_cost_total: '0', known_purchase_total: '20',
    price_complete: true, budget: '100', budget_status: 'OK', lines: [{ food_id: 'tomato', price_status: 'known', shortage_grams: '3/2',
      packs: 1, cost: '0', purchase_price: '20', quote: { source_name: '原始來源', queried_at: '2026-01-01T00:00:00Z' } },
      { food_id: 'water', price_status: 'free', cost: '0' }], unquantified_food_ids: [],
  } }
  const priced = savedPrices(snapshot)
  assert.equal(priced.items.length, 1)
  assert.equal(priced.items[0].name, '番茄')
  assert.equal(priced.items[0].shortage_grams, '3/2')
  assert.equal(priced.items[0].queried_at, '2026-01-01T00:00:00Z')
  assert.equal(priced.cost_total, '0')
  assert.equal(priced.purchase_total, '20')
  snapshot.cost.unquantified_food_ids = ['tomato']
  const partial = savedPrices(snapshot)
  assert.equal(partial.cost_total, null)
  assert.equal(partial.purchase_total, null)
  assert.equal(partial.known_cost_total, '0')
  assert.equal(partial.price_complete, false)
  assert.deepEqual(partial.unquantified_items, [{ food_id: 'tomato', name: '番茄' }])
})


test('random dish excludes chosen names and backend conflicts and deduplicates the candidate pool', () => {
  const shown = [
    { dish_name: '已選', conflicts: [] }, { dish_name: '衝突', conflicts: ['花生'] },
    { dish_name: '青菜', conflicts: [] }, { dish_name: '青菜', conflicts: [] }, { dish_name: '豆腐', conflicts: [] },
  ]
  assert.equal(randomDish(shown, ['已選'], () => 0), '青菜')
  assert.equal(randomDish(shown, ['已選'], () => 0.5), '豆腐')
  assert.equal(randomDish(shown, ['已選'], () => 0.999999), '豆腐')
})
test('random dish stops at ten or an exhausted pool without drawing or changing selected dishes', () => {
  let draws = 0
  const random = () => { draws++; return 0 }
  assert.equal(randomDish([], [], random), undefined)
  assert.equal(randomDish([{ dish_name: '過敏菜', conflicts: ['蛋'] }], [], random), undefined)
  assert.equal(randomDish([{ dish_name: '已選', conflicts: [] }], ['已選'], random), undefined)
  const chosen = Array.from({ length: MAX_DISHES }, (_, i) => `菜${i}`)
  assert.equal(randomDish([{ dish_name: '第十一道', conflicts: [] }], chosen, random), undefined)
  assert.equal(chosen.length, MAX_DISHES)
  assert.equal(draws, 0)
})
test('random dish only uses the currently filtered list and preserves existing selections', () => {
  const shown = [{ dish_name: '篩選結果', conflicts: [] }]
  const chosen = ['已選但目前被篩掉的菜']
  const before = structuredClone({ shown, chosen })
  const next = randomDish(shown, chosen, () => 0)
  assert.equal(next, '篩選結果')
  assert.deepEqual({ shown, chosen }, before)
  assert.equal(randomDish(shown, [...chosen, next], () => 0), undefined)
})
