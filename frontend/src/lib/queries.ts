import { QueryCache, QueryClient, useQuery } from '@tanstack/react-query'
import { api, ApiError, isOpenStatus } from './api'

export const POLL_MS = 1500
const CONSOLE_REFRESH_MS = 5000

let lastSessionRecovery = 0

export const queryClient: QueryClient = new QueryClient({
  // If the session cookie is gone (cleared, expired), start a new session and
  // let the customer queries run again instead of showing a dead page. At most
  // once every 15 seconds, so a cookie that never sticks cannot cause a loop.
  queryCache: new QueryCache({
    onError: (err, query) => {
      if (!(err instanceof ApiError) || err.code !== 'no_session' || query.queryKey[0] === 'session') return
      if (Date.now() - lastSessionRecovery < 15_000) return
      lastSessionRecovery = Date.now()
      void queryClient.invalidateQueries({ queryKey: ['session'] }).then(() => queryClient.invalidateQueries())
    },
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: (count, err) => {
        if (err instanceof ApiError && err.status > 0 && err.status < 500) return false
        return count < 2
      },
    },
  },
})

export const keys = {
  session: ['session'] as const,
  config: ['config'] as const,
  scenarios: ['scenarios'] as const,
  wallet: ['wallet'] as const,
  topup: (id: string) => ['topup', id] as const,
  predict: (phone: string) => ['predict', phone] as const,
  stats: ['console', 'stats'] as const,
  consoleTopups: (status: string, q: string) => ['console', 'topups', status, q] as const,
  consoleTopup: (id: string) => ['console', 'topup', id] as const,
  runs: ['console', 'runs'] as const,
  events: ['console', 'events'] as const,
  health: ['health'] as const,
}

/**
 * The anonymous demo session. Called once on app load, before any other
 * customer call; everything in the customer API is scoped to its cookie.
 */
export function useSession() {
  return useQuery({
    queryKey: keys.session,
    queryFn: api.createSession,
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

export function useConfig(enabled: boolean) {
  return useQuery({ queryKey: keys.config, queryFn: api.config, enabled, staleTime: 5 * 60_000 })
}

export function useScenarios(enabled: boolean) {
  return useQuery({ queryKey: keys.scenarios, queryFn: api.scenarios, enabled, staleTime: Infinity })
}

export function useWallet(enabled: boolean) {
  return useQuery({ queryKey: keys.wallet, queryFn: api.wallet, enabled })
}

/** Polls every 1.5s while the top-up is open and stops once it is final. */
export function useTopup(id: string | null, enabled: boolean) {
  return useQuery({
    queryKey: keys.topup(id ?? ''),
    queryFn: () => api.topup(id as string),
    enabled: enabled && !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.topup.status
      if (query.state.error) return false
      return !status || isOpenStatus(status) ? POLL_MS : false
    },
  })
}

export function useStats() {
  return useQuery({ queryKey: keys.stats, queryFn: api.stats, refetchInterval: CONSOLE_REFRESH_MS })
}

export function useConsoleTopups(status: string, q: string) {
  return useQuery({
    queryKey: keys.consoleTopups(status, q),
    queryFn: () => api.consoleTopups({ status: status || undefined, q: q || undefined }),
    refetchInterval: CONSOLE_REFRESH_MS,
    placeholderData: (prev) => prev,
  })
}

export function useConsoleTopup(id: string) {
  return useQuery({
    queryKey: keys.consoleTopup(id),
    queryFn: () => api.consoleTopup(id),
    refetchInterval: (query) => {
      const status = query.state.data?.topup.status
      if (query.state.error) return false
      return !status || isOpenStatus(status) ? POLL_MS : CONSOLE_REFRESH_MS
    },
  })
}

export function useReconcileRuns() {
  return useQuery({ queryKey: keys.runs, queryFn: () => api.reconcileRuns(20), refetchInterval: CONSOLE_REFRESH_MS })
}

export function useEvents() {
  return useQuery({ queryKey: keys.events, queryFn: () => api.events(100), refetchInterval: CONSOLE_REFRESH_MS })
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: api.health, refetchInterval: 30_000, retry: false })
}
