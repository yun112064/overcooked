import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = fs.readFileSync(new URL('../src/api/handoff.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } })
const load = async id => import(`data:text/javascript;base64,${Buffer.from(compiled.outputText).toString('base64')}#${id}`)

test('grant is removed from URL and sent only in a credential-free POST', async t => {
  const storage = new Map()
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, '/market-api/checkout')
    assert.equal(options.credentials, 'omit')
    assert.equal(options.cache, 'no-store')
    assert.equal(options.method, 'POST')
    assert.deepEqual(JSON.parse(options.body), { token: 'scoped-grant' })
    return new Response(JSON.stringify({ handoff_id: 'example' }))
  })
  globalThis.window = { location: { hash: '#handoff=scoped-grant', pathname: '/checkout' } }
  globalThis.history = { replaceState: (_a, _b, path) => assert.equal(path, '/checkout') }
  globalThis.sessionStorage = { setItem: (key, val) => storage.set(key, val) }
  try {
    const api = await load('clean')
    assert.equal(api.checkoutToken(), 'scoped-grant')
    assert.equal(storage.get('sim-checkout-grant'), 'scoped-grant')
    assert.deepEqual(await api.loadCheckout(api.checkoutToken()), { handoff_id: 'example' })
    window.location.hash = '#handoff=next-grant'
    assert.equal(api.checkoutToken(), 'next-grant')
    assert.equal(storage.get('sim-checkout-grant'), 'next-grant')
  } finally { delete globalThis.window; delete globalThis.history; delete globalThis.sessionStorage }
})

test('expired grant is cleared and requires returning to the main application', async t => {
  let removed
  globalThis.sessionStorage = { removeItem: key => { removed = key } }
  t.mock.method(globalThis, 'fetch', async () => new Response('{}', { status: 410 }))
  try {
    const api = await load('expired')
    await assert.rejects(api.loadCheckout('expired'), /購物連結已失效/)
    assert.equal(removed, 'sim-checkout-grant')
  } finally { delete globalThis.sessionStorage }
})
