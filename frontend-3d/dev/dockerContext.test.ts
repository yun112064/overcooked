import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, normalize, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { describe, expect, it } from 'vitest'

/**
 * Docker 建置跑的是 `tsc -b && vite build`：vite.config.ts 會載入 dev/，tsconfig 也包含 dev/。
 * .dockerignore 一旦排除了建置要用的檔案，映像就建不起來，而本機測試不會建映像，所以在這裡靜態檢查。
 */
const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function toPosix(path: string) {
  return normalize(path).split('\\').join('/').replace(/^\.\//, '').replace(/\/$/, '')
}

/** .dockerignore 的模式（不支援 ! 例外；有例外就讓測試失敗，請人重看） */
function ignorePatterns() {
  return readFileSync(join(root, '.dockerignore'), 'utf8')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
}

/** Docker 的比對：* 不跨目錄、** 跨目錄；符合目錄本身就等於排除整個目錄 */
function patternRegex(pattern: string) {
  const body = toPosix(pattern.replace(/^\//, ''))
    .split('/')
    .map((part) => part === '**'
      ? '.*'
      : part.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]'))
    .join('/')
  return new RegExp(`^${body}(/.*)?$`)
}

function ignoredBy(path: string, patterns: string[]) {
  const target = toPosix(path)
  return patterns.filter((pattern) => patternRegex(pattern).test(target))
}

function filesUnder(dir: string): string[] {
  return readdirSync(join(root, dir), { withFileTypes: true }).flatMap((entry) => {
    const path = `${dir}/${entry.name}`
    if (entry.name === 'node_modules') return []
    return entry.isDirectory() ? filesUnder(path) : [path]
  })
}

/** 建置一定要的路徑：vite.config.ts 的相對 import、tsconfig 的 include/references、清單檔、src、public */
function requiredPaths() {
  const config = readFileSync(join(root, 'vite.config.ts'), 'utf8')
  const imports = [...config.matchAll(/from\s+['"](\.{1,2}\/[^'"]+)['"]/g)].map((match) => match[1])
  const tsconfigs = readdirSync(root).filter((name) => /^tsconfig.*\.json$/.test(name))
  const tsPaths = tsconfigs.flatMap((name) => {
    // tsconfig 允許註解，用 TypeScript 自己的解析器讀
    const json = ts.parseConfigFileTextToJson(name, readFileSync(join(root, name), 'utf8')).config as {
      include?: string[]
      references?: { path: string }[]
    }
    return [...(json.include ?? []), ...(json.references ?? []).map((reference) => reference.path)]
  })
  const listed = ['vite.config.ts', ...imports, ...tsconfigs, ...tsPaths,
    'package.json', 'package-lock.json', 'index.html', 'src', 'public']
  const dirs = listed.filter((path) => ['dev', 'src', 'public'].includes(toPosix(path)))
  return [...new Set([...listed, ...dirs.flatMap((dir) => filesUnder(toPosix(dir)))].map(toPosix))]
}

describe('Docker 建置內容', () => {
  it('.dockerignore 沒有 ! 例外', () => {
    expect(ignorePatterns().filter((pattern) => pattern.startsWith('!'))).toEqual([])
  })

  it('建置要用的檔案都沒有被 .dockerignore 排除', () => {
    const patterns = ignorePatterns()
    const required = requiredPaths()
    expect(required).toContain('dev/fakeApi.ts')
    expect(required).toContain('tsconfig.node.json')
    const blocked = required
      .map((path) => ({ path, by: ignoredBy(relative(root, join(root, path)), patterns) }))
      .filter(({ by }) => by.length > 0)
    expect(blocked).toEqual([])
  })

  it('比對規則本身：排除 dev/ 或 *.ts 會被抓到', () => {
    expect(ignoredBy('dev/fakeApi.ts', ['dev'])).toEqual(['dev'])
    expect(ignoredBy('vite.config.ts', ['*.ts'])).toEqual(['*.ts'])
    expect(ignoredBy('src/a.ts', ['*.ts'])).toEqual([])
    expect(ignoredBy('src/a.md', ['**/*.md'])).toEqual(['**/*.md'])
  })
})
