

import { useEffect, useState } from 'react'

type HealthResponse = {
  status: string
  service: string
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    async function checkHealth() {
      try {
        const response = await fetch('/api/health/db', {
          signal: controller.signal,
        })

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        const data: HealthResponse = await response.json()
        setHealth(data)
      } catch (err) {
        if (controller.signal.aborted) return

        setError(
          err instanceof Error ? err.message : '未知錯誤'
        )
      }
    }

    void checkHealth()

    return () => controller.abort()
  }, [])

  return (
    <main style={{ padding: '2rem' }}>
      <h1>T06 系統串接測試</h1>

      {error ? (
        <p>後端連線失敗：{error}</p>
      ) : health ? (
        <div>
          <p>後端連線成功！</p>
          <p>Status：{health.status}</p>
          <p>Service：{health.service}</p>
        </div>
      ) : (
        <p>正在檢查後端連線...</p>
      )}
    </main>
  )
}

export default App
