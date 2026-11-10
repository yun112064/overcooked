import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { createServer } from 'vite'

test('admin proxies only authentication and management; consumer does not proxy management', async () => {
  const requests = []
  const backend = http.createServer((request, response) => {
    requests.push({ path: request.url, origin: request.headers.origin, cookie: request.headers.cookie })
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end('{"fixture":true}')
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const previous = process.env.VITE_API_PROXY_TARGET
  process.env.VITE_API_PROXY_TARGET = `http://127.0.0.1:${backend.address().port}`
  const servers = []
  try {
    for (const [relativeRoot, admin] of [['../', true], ['../../frontend/', false]]) {
      const root = fileURLToPath(new URL(relativeRoot, import.meta.url))
      const vite = await createServer({ root, configFile: root + '/vite.config.ts',
        server: { port: 0, host: '127.0.0.1', strictPort: false }, logLevel: 'silent' })
      servers.push(vite)
      await vite.listen()
      const base = `http://127.0.0.1:${vite.httpServer.address().port}`
      for (const path of admin ? ['/api/auth/register', '/api/households', '/api/internal/knowledge', '/api/auth/me/extra']
        : ['/api/admin', '/api/admin/v1/capabilities', '/api/admin/v1/proposals?domain=recipe']) {
        const before = requests.length
        assert.equal((await fetch(base + path)).status, 404, path)
        assert.equal(requests.length, before, 'Denied path must not reach backend')
      }
      for (const path of admin ? ['/api/auth/me', '/api/auth/login', '/api/auth/logout', '/api/admin/v1/capabilities?include_scopes=true', '/api/admin/v1/artifacts'] : ['/api/auth/me']) {
        const origin = 'http://admin.localhost:5175'
        const result = await fetch(base + path, { headers: { Origin: origin, Cookie: 'mp_session=synthetic' } })
        assert.deepEqual(await result.json(), { fixture: true })
        assert.equal(requests.at(-1).path, path.slice(4))
        assert.equal(requests.at(-1).origin, origin, 'CSRF origin must survive proxying')
        assert.equal(requests.at(-1).cookie, 'mp_session=synthetic')
      }
    }
  } finally {
    await Promise.all(servers.map(server => server.close()))
    await new Promise(resolve => backend.close(resolve))
    if (previous === undefined) delete process.env.VITE_API_PROXY_TARGET
    else process.env.VITE_API_PROXY_TARGET = previous
  }
})
