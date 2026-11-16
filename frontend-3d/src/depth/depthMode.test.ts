import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  deviceStore, markMotionPrompted, parseDepthMode, readDepthMode, shouldPromptReducedMotion, writeDepthMode,
} from './depthMode'

afterEach(() => vi.unstubAllGlobals())

function memoryStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value) },
  }
}

const brokenStore = {
  getItem: () => { throw new Error('blocked') },
  setItem: () => { throw new Error('blocked') },
}

describe('景深模式', () => {
  it('沒設定過或值不認得時是沉浸', () => {
    expect(parseDepthMode(null)).toBe('immersive')
    expect(parseDepthMode('3d')).toBe('immersive')
    expect(parseDepthMode('static')).toBe('static')
  })

  it('寫入後讀得回來', () => {
    const store = memoryStore()
    writeDepthMode(store, 'off')
    expect(readDepthMode(store)).toBe('off')
  })

  it('儲存空間不能用時不會丟例外', () => {
    expect(readDepthMode(brokenStore)).toBe('immersive')
    expect(() => writeDepthMode(brokenStore, 'static')).not.toThrow()
    expect(() => markMotionPrompted(brokenStore)).not.toThrow()
  })
})

describe('系統減少動態的提示', () => {
  it('系統開了減少動態、目前是沉浸、還沒問過 → 提示', () => {
    expect(shouldPromptReducedMotion(memoryStore(), true, 'immersive')).toBe(true)
  })

  it('只提示一次', () => {
    const store = memoryStore()
    markMotionPrompted(store)
    expect(shouldPromptReducedMotion(store, true, 'immersive')).toBe(false)
  })

  it('系統沒開減少動態就不提示', () => {
    expect(shouldPromptReducedMotion(memoryStore(), false, 'immersive')).toBe(false)
  })

  it('已經不是沉浸模式的人不用問', () => {
    expect(shouldPromptReducedMotion(memoryStore(), true, 'static')).toBe(false)
  })

  it('讀不到紀錄時寧可不提示，避免每次進站都跳出來', () => {
    expect(shouldPromptReducedMotion(brokenStore, true, 'immersive')).toBe(false)
  })
})

describe('deviceStore', () => {
  it('儲存空間可以用：就是 localStorage', () => {
    const store = memoryStore({ 'depth-mode': 'off' })
    vi.stubGlobal('window', { localStorage: store })
    expect(deviceStore()).toBe(store)
    expect(readDepthMode(deviceStore())).toBe('off')
  })

  it('連讀 window.localStorage 都丟例外（瀏覽器封鎖）：不往外丟，一律走預設值', () => {
    vi.stubGlobal('window', { get localStorage(): Storage { throw new Error('SecurityError') } })
    expect(() => deviceStore()).not.toThrow()
    expect(readDepthMode(deviceStore())).toBe('immersive')
    expect(() => writeDepthMode(deviceStore(), 'static')).not.toThrow()
    expect(() => markMotionPrompted(deviceStore())).not.toThrow()
    // 記不住有沒有問過，就不要每次進站都問
    expect(shouldPromptReducedMotion(deviceStore(), true, 'immersive')).toBe(false)
  })

  it('給 null 的內嵌瀏覽器也一樣', () => {
    vi.stubGlobal('window', { localStorage: null })
    expect(readDepthMode(deviceStore())).toBe('immersive')
    expect(shouldPromptReducedMotion(deviceStore(), true, 'immersive')).toBe(false)
  })
})
