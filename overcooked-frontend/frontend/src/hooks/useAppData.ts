import { useCallback, useEffect, useRef, useState } from 'react'
import type { AppData, SaveStatus, Scenario } from '../domain/types.ts'
import { readData, writeData } from '../services/storage.ts'
import { seedData } from '../services/seed.ts'

export function useAppData(scenario: Scenario) {
  const [initial] = useState(() => {
    try { const stored = readData(localStorage); return { data: stored ?? seedData(), isNew: !stored, error: '' } }
    catch(error) { return { data: seedData(), isNew: false, error: error instanceof Error ? error.message : '無法讀取本機資料。' } }
  })
  const [data, setData] = useState(initial.data)
  const [status, setStatus] = useState<SaveStatus>(initial.isNew ? 'saving' : 'saved')
  const [saveError, setSaveError] = useState('')
  const current = useRef(data), durable = useRef(structuredClone(data)), dirty = useRef(initial.isNew)
  const update = useCallback((transform: (previous: AppData) => AppData) => {
    const next = transform(current.current); current.current = next; dirty.current = true; setData(next); setStatus('saving'); setSaveError('')
    return next
  }, [])
  const saveNow = useCallback((candidate = current.current): boolean => {
    try {
      if(initial.error) throw new Error(initial.error)
      if(scenario === 'save-failure') throw new Error('保存失敗，請重試。')
      writeData(localStorage, candidate); durable.current = structuredClone(candidate); dirty.current = false; setStatus('saved'); setSaveError(''); return true
    } catch(error) { setStatus('failed'); setSaveError(error instanceof Error ? error.message : '保存失敗，請重試。'); return false }
  }, [initial.error, scenario])
  useEffect(() => {
    if(!dirty.current || initial.error) return
    const timer = window.setTimeout(() => saveNow(), 550)
    return () => window.clearTimeout(timer)
  }, [data, saveNow, initial.error])
  const discard = useCallback(() => {
    const previous = structuredClone(durable.current); current.current = previous; dirty.current = false; setData(previous); setStatus('saved'); setSaveError('')
  }, [])
  const commit = useCallback((next: AppData) => {
    if(!saveNow(next)) return false
    current.current = next; setData(next); return true
  }, [saveNow])
  return { data, current, update, commit, status, saveError, saveNow, discard, fatalError: initial.error }
}
