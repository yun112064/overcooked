import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// 正式版與原示範版分開載入，避免示範資料與全域樣式進入正式流程。
const demo = import.meta.env.MODE === 'demo'
const { default: App } = await (demo ? import('./App.tsx') : import('./live/App.tsx'))
if (demo) await import('./index.css')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
