export interface HealthResponse { status: string; service: string }

export async function checkBackendHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch('/api/health/db', { credentials: 'same-origin', signal })
  if(!response.ok) throw new Error(`健康檢查失敗（HTTP ${response.status}）`)
  const value: unknown = await response.json()
  if(!value || typeof value !== 'object' || !('status' in value) || !('service' in value) || typeof value.status !== 'string' || typeof value.service !== 'string') throw new Error('健康檢查回應格式不正確。')
  return { status: value.status, service: value.service }
}
