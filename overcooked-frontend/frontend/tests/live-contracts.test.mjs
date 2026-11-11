import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import { withinTarget, toNumber } from '../src/live/api/nutrition.ts'
import { parseSse } from '../src/live/api/assistant.ts'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = process.env.CONTRACT_REPO_ROOT || resolve(frontend, '../..')
const source = join(repo, 'frontend/src')
const live = join(frontend, 'src/live')
const baseline = JSON.parse(readFileSync(join(repo, 'overcooked-frontend/docs/frontend-integration-baseline.json'), 'utf8'))
function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
    ? files(join(dir, entry.name)) : [join(dir, entry.name)])
}
function calls(path, full = false) {
  const ast = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true)
  const found = []
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.expression.getText(ast) === 'api') {
      found.push(full ? node.getText(ast).replace(/\s/g, '') : [node.expression.name.text, node.arguments[0]?.getText(ast).replace(/\s/g, '')])
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
  return found.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
}

test('the original frontend remains unchanged apart from checkout line endings', () => {
  // The old snapshot froze the entire repository before integration. Backend,
  // Admin and Sim are now explicitly being integrated; protect the legacy
  // frontend here, while the following tests protect the live API contracts.
  for (const [path, expected] of Object.entries(baseline.sha256)) {
    if (!path.startsWith('frontend/')) continue
    const bytes = readFileSync(join(repo, path))
    const exact = createHash('sha256').update(bytes).digest('hex')
    const checkout = bytes.includes(0) ? exact
      : createHash('sha256').update(bytes.toString('utf8').replace(/\r\n/g, '\n')).digest('hex')
    assert.ok(exact === expected || checkout === expected, path)
  }
})
test('every existing API method and URL is retained in the isolated frontend', () => {
  for (const file of files(source).filter(path => /\.tsx?$/.test(path) && !path.endsWith('main.tsx'))) {
    const target = join(live, relative(source, file))
    assert.deepEqual(calls(target), calls(file), relative(source, file))
  }
})
test('API models, decimal handling, credentials, SSE and polling stay on the existing contracts', () => {
  for (const name of ['api/client.ts', 'api/types.ts', 'api/nutrition.ts', 'api/resultNutrition.ts',
    'api/assistant.ts', 'components/RecognitionCapture.tsx', 'components/MemberFormDialog.tsx']) {
    assert.equal(readFileSync(join(live, name), 'utf8'), readFileSync(join(source, name), 'utf8'), name)
  }
  for (const name of ['components/MenuPreviewPanel.tsx', 'components/MealResultView.tsx',
    'components/GenerationPanel.tsx', 'pages/MealRequestEditor.tsx']) {
    assert.deepEqual(calls(join(live, name), true), calls(join(source, name), true), `${name}: request bodies, versions and idempotency keys`)
  }
  for (const name of ['vite.config.ts', 'Caddyfile']) {
    assert.equal(readFileSync(join(frontend, name), 'utf8'), readFileSync(join(repo, 'frontend', name), 'utf8'), name)
  }
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
