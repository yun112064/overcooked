import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readData, storageKey, writeData } from './storage.ts'
import { seedData } from './seed.ts'

class MemoryStorage {
  values = new Map<string, string>()
  getItem(key: string) { return this.values.get(key) ?? null }
  setItem(key: string, value: string) { this.values.set(key, value) }
}
test('empty storage is distinct from valid saved data', () => {
  const storage = new MemoryStorage(), data = seedData()
  assert.equal(readData(storage), null)
  writeData(storage, data)
  assert.deepEqual(readData(storage), data)
})
test('corrupt storage is reported without overwriting the original', () => {
  const storage = new MemoryStorage()
  storage.setItem(storageKey, '{broken')
  assert.throws(() => readData(storage))
  assert.equal(storage.getItem(storageKey), '{broken')
})
test('unsupported schema versions and unknown household references are rejected', () => {
  const storage = new MemoryStorage(), data = seedData()
  storage.setItem(storageKey, JSON.stringify({ ...data, version: 999 }))
  assert.throws(() => readData(storage))
  storage.setItem(storageKey, JSON.stringify({ ...data, activeHouseholdId: 'missing' }))
  assert.throws(() => readData(storage), /家庭/)
})
test('storage write failures propagate instead of claiming persistence', () => {
  const storage = { getItem: () => null, setItem: () => { throw new Error('quota') } }
  assert.throws(() => writeData(storage, seedData()), /quota/)
})
