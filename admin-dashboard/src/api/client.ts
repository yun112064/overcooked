import axios, { AxiosError } from 'axios'

// 經由 Vite proxy（開發）或 Caddy（正式）轉到後端；Cookie 由瀏覽器自動帶上
export const api = axios.create({
  baseURL: '/api',
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

export type ApiErrorBody = {
  error: {
    code: string
    message: string
    details?: {
      field?: string
      reason?: string
      fields?: { field: string; reason: string; message: string }[]
    }
  }
}

export function apiError(err: unknown): ApiErrorBody['error'] | null {
  if (err instanceof AxiosError) {
    const body = err.response?.data as ApiErrorBody | undefined
    if (body?.error) return body.error
    if (!err.response) {
      return { code: 'NETWORK', message: '無法連線到伺服器，請稍後再試。' }
    }
  }
  return null
}

export function errorMessage(err: unknown, fallback = '發生錯誤，請稍後再試。'): string {
  return apiError(err)?.message ?? fallback
}

export function errorStatus(err: unknown): number | undefined {
  return err instanceof AxiosError ? err.response?.status : undefined
}
