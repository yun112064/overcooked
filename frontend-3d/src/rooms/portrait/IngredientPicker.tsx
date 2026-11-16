/**
 * 從標準食材字典挑食材（過敏、禁吃）。不接受自由輸入：字典裡對不到的禁忌，後端沒辦法幫忙避開。
 * 行為照舊前端的 IngredientPicker：停止打字 250ms 後才搜尋；選了以後搜尋字留著，可以接著選同一批結果。
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useState, type KeyboardEvent } from 'react'
import { errorMessage } from '../../api/client'
import type { IngredientRef } from '../../api/types'
import { ingredientLabel } from './people'
import { ingredientsQuery } from './queries'
import styles from './MemberForm.module.css'

type Props = {
  label: string
  value: IngredientRef[]
  onChange: (value: IngredientRef[]) => void
  onBlur?: () => void
  helperText?: string
  error?: string
}

const DEBOUNCE_MS = 250
/** 後端的搜尋字最長 50 個字，超過會回 422 */
const QUERY_MAX = 50

export function IngredientPicker({ label, value, onChange, onBlur, helperText, error }: Props) {
  const inputId = useId()
  const listId = useId()
  const hintId = useId()
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(input.trim()), DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [input])

  const results = useQuery(ingredientsQuery(query))
  const options = query ? results.data ?? [] : []
  const chosen = (item: IngredientRef) => value.some((v) => v.ingredient_id === item.ingredient_id)
  // 清單內容換了以後，原本指到的那一項可能已經不在
  const current = Math.min(active, Math.max(0, options.length - 1))

  const toggle = (item: IngredientRef) => {
    onChange(chosen(item) ? value.filter((v) => v.ingredient_id !== item.ingredient_id) : [...value, item])
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      if (options.length > 0) {
        const step = event.key === 'ArrowDown' ? 1 : -1
        setActive((current + step + options.length) % options.length)
      }
    } else if (event.key === 'Enter') {
      // 不論有沒有選到東西，Enter 都不該把整張表單送出去
      event.preventDefault()
      if (open && options[current]) toggle(options[current])
    } else if (event.key === 'Escape' && open) {
      // 先收起清單；preventDefault 讓外層面板知道這一下不是要關面板
      event.preventDefault()
      setOpen(false)
    } else if (event.key === 'Backspace' && input === '' && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  const status = !query ? '輸入名稱搜尋'
    : results.isPending ? '搜尋中…'
      : results.isError ? errorMessage(results.error, '搜尋失敗，請再試一次。')
        : options.length === 0 ? '查無符合的標準食材' : null

  return (
    <div className={styles.field}>
      <label htmlFor={inputId}>{label}</label>
      {value.length > 0 && (
        <ul className={styles.chips} aria-label={`已選的${label}`}>
          {value.map((item) => (
            <li key={item.ingredient_id}>
              <button type="button" aria-label={`移除 ${ingredientLabel(item)}`} onClick={() => toggle(item)}>
                <span>{ingredientLabel(item)}<i aria-hidden="true">✕</i></span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className={styles.combo}>
        <input
          id={inputId} type="text" role="combobox" autoComplete="off" maxLength={QUERY_MAX} placeholder="輸入名稱搜尋"
          aria-expanded={open} aria-controls={listId} aria-autocomplete="list" aria-invalid={!!error}
          aria-describedby={error || helperText ? hintId : undefined}
          aria-activedescendant={open && options[current] ? `${listId}-${current}` : undefined}
          value={input}
          onChange={(event) => { setInput(event.target.value); setOpen(true); setActive(0) }}
          onFocus={() => setOpen(true)}
          onBlur={() => { setOpen(false); onBlur?.() }}
          onKeyDown={onKeyDown}
        />
        <div className={styles.results} hidden={!open}>
          <ul id={listId} role="listbox" aria-label={label} aria-multiselectable="true">
            {options.map((item, i) => (
              <li
                key={item.ingredient_id} id={`${listId}-${i}`} role="option" aria-selected={chosen(item)}
                className={i === current ? styles.activeOption : undefined}
                // 用 mousedown 擋掉失焦，清單才不會在點到之前就收起來
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => toggle(item)}
              >
                <span>{ingredientLabel(item)}</span>
                {chosen(item) && <small>已選・再點一次移除</small>}
              </li>
            ))}
          </ul>
          {status && <p role="status">{status}</p>}
        </div>
      </div>
      {(error || helperText) && <p id={hintId} className={error ? styles.error : styles.hint}>{error ?? helperText}</p>}
    </div>
  )
}
