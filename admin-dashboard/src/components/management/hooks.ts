import { useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { command, read } from '../../api/management'

export function useManagementRead<T>(path: string, params?: Record<string, string | number | undefined>, enabled = true, refresh?: number) {
  return useQuery({
    queryKey: ['management', path, params], enabled, retry: false, refetchInterval: refresh,
    queryFn: ({ signal }) => read<T>(path, params, signal),
  })
}

export function useManagementCommand<T>(path: string, onSuccess?: (data: T) => void) {
  const client = useQueryClient()
  const retry = useRef<{ body: unknown[]; key: string } | null>(null)
  return useMutation({
    mutationFn: (body: object) => {
      const signature = body instanceof FormData ? Array.from(body.entries()).flat() : [JSON.stringify(body)]
      const previous = retry.current
      const key = previous && previous.body.length === signature.length &&
        signature.every((value, index) => value === previous.body[index]) ? previous.key : crypto.randomUUID()
      retry.current = { body: signature, key }
      return command<T>(path, body, key)
    },
    onSuccess: (data) => {
      retry.current = null
      void client.invalidateQueries({ queryKey: ['management'] })
      onSuccess?.(data)
    },
  })
}
