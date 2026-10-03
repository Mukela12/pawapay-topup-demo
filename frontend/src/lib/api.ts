// Typed client for the Ringwise Flask API. Same origin only: in dev Vite proxies
// /api, /webhooks and /healthz to Flask; in production Flask serves this build.

export type TopupStatus =
  | 'CREATED'
  | 'ACCEPTED'
  | 'PROCESSING'
  | 'IN_RECONCILIATION'
  | 'COMPLETED'
  | 'FAILED'
  | 'REJECTED'
  | 'NEEDS_ATTENTION'

export const OPEN_STATUSES: readonly TopupStatus[] = ['CREATED', 'ACCEPTED', 'PROCESSING', 'IN_RECONCILIATION']

export function isOpenStatus(status: TopupStatus | string): boolean {
  return (OPEN_STATUSES as readonly string[]).includes(status)
}

export interface Topup {
  id: string
  deposit_id: string
  customer_id?: string
  amount_minor: number
  currency: string
  /** ISO 3166 alpha-3, for example ZMB */
  country: string
  provider: string
  /** MSISDN: in full for the customer who owns it, masked in the console */
  phone: string
  status: TopupStatus
  is_open?: boolean
  /** The API's own customer-facing reason for failed or held top-ups */
  reason?: string | null
  failure_code: string | null
  /** pawaPay's failureMessage, console only */
  failure_message?: string | null
  provider_txn_id: string | null
  scenario: string | null
  created_at: string
  accepted_at: string | null
  finalized_at: string | null
  last_checked_at: string | null
  check_count: number
  /** Optional convenience fields; the client falls back to its own currency table. */
  exponent?: number
  amount?: string
}

export type EventSource =
  | 'initiate_response'
  | 'callback'
  | 'status_check'
  | 'reconcile'
  | 'replay'
  | 'forged_test'
  | 'resend_request'

export type SignatureState = 'verified' | 'invalid' | 'missing' | 'not_checked'

export type EventOutcome = 'applied' | 'duplicate_ignored' | 'rejected' | 'held_for_review' | 'no_change' | 'error'

export interface PaymentEventSummary {
  id: string
  topup_id?: string | null
  deposit_id?: string | null
  source: EventSource
  http_status: number | null
  signature: SignatureState
  outcome: EventOutcome
  detail: string | null
  created_at: string
  /** The pawaPay status this event carried, when the API includes it. */
  status?: string | null
  pawapay_status?: string | null
}

export interface PaymentEvent extends PaymentEventSummary {
  payload?: unknown
  headers?: Record<string, unknown> | null
}

/** One approval channel from active-conf pinPromptInstructions, flattened by the API. */
export interface PinInstruction {
  type?: string | null
  display_name?: string | null
  quick_link?: string | null
  steps?: string[]
}

export interface Provider {
  provider: string
  display_name: string
  logo: string | null
  name_displayed_to_customer: string | null
  min_amount: string | number | null
  max_amount: string | number | null
  decimals: 0 | 2
  decimals_in_amount?: 'TWO_PLACES' | 'NONE' | string
  currency?: string
  status: 'OPERATIONAL' | 'DELAYED' | 'CLOSED' | string
  pin_prompt: string | null
  pin_prompt_revivable?: boolean
  pin_prompt_instructions: PinInstruction[]
}

export interface Country {
  /** ISO alpha-3 */
  country: string
  display_name: string
  /** Dialling prefix without the plus, for example 260 */
  prefix: string
  currency: string
  providers: Provider[]
}

export interface AppConfig {
  countries: Country[]
  sandbox: boolean
  reconcile_after_seconds: number
  /** 'pawapay' when built from active-conf, 'fallback' for the static Zambia list */
  source?: 'pawapay' | 'fallback' | string
  fallback_reason?: string | null
  pawapay_configured?: boolean
}

export interface PredictedProvider {
  country: string
  provider: string
  phone_number: string
}

export interface Scenario {
  id: string
  label: string
  description: string
  country: string
  provider: string
  phone: string
  expected: string
}

export interface Balance {
  currency: string
  balance_minor: number
  exponent: number
}

export interface Wallet {
  balances: Balance[]
  topups: Topup[]
}

export interface CreateTopupInput {
  country: string
  provider: string
  phone: string
  amount: string
  scenario?: string
}

export interface TopupWithEvents {
  topup: Topup
  events: PaymentEventSummary[]
  ledger_entry?: LedgerEntry | null
}

export interface ConsoleStats {
  completed_today: number
  failed_today: number
  open_now: number
  needs_attention: number
  success_rate_7d: number | null
  median_seconds_to_final: number | null
  total_credited: { currency: string; amount_minor: number }[]
}

export interface LedgerEntry {
  id: string
  account_id?: string
  topup_id?: string
  kind: string
  amount_minor: number
  currency?: string
  created_at: string
}

export interface OutboxRow {
  id: string
  topic: 'receipt.issued' | 'voip_credit.granted' | string
  label?: string
  payload?: unknown
  dedupe_key: string
  attempts: number
  created_at: string
  delivered_at: string | null
}

export interface ConsoleTopupDetail {
  topup: Topup
  events: PaymentEvent[]
  ledger_entry: LedgerEntry | null
  outbox: OutboxRow[]
  balance_minor?: number
}

/**
 * A console action's answer, flattened: the API wraps the handler result in
 * `result` and adds before/after state next to it.
 */
export interface ActionResult {
  outcome?: EventOutcome | string
  http_status?: number | null
  /** Top-up status after the action (re-check) */
  status?: string | null
  pawapay_status?: string | null
  signature?: SignatureState | string
  signature_reason?: string | null
  topup_status?: string | null
  detail?: string | null
  credited?: boolean
  event_id?: string | null
  status_before?: string
  status_after?: string
  balance_before?: number
  balance_after?: number
  topup?: Topup
  raw: unknown
}

function actionFrom(raw: unknown): ActionResult {
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const result = (obj.result && typeof obj.result === 'object' ? obj.result : obj) as Record<string, unknown>
  return {
    ...(result as Partial<ActionResult>),
    status_before: obj.status_before as string | undefined,
    status_after: obj.status_after as string | undefined,
    balance_before: obj.balance_before as number | undefined,
    balance_after: obj.balance_after as number | undefined,
    topup: obj.topup as Topup | undefined,
    raw,
  }
}

export interface ReconcileRun {
  id: string
  trigger: 'schedule' | 'manual' | 'eb_cron' | string
  started_at: string
  finished_at: string | null
  checked: number
  settled: number
  still_open: number
  failed_not_found: number
  errors: number
}

export interface Health {
  ok: boolean
  db: boolean | string
  pawapay_configured: boolean
  signature_required: boolean
  commit: string | null
}

export interface ApiErrorBody {
  error?: { code?: string; message?: string; field?: string; topup?: Topup }
  topup?: Topup
  result?: { outcome?: string; detail?: string; [key: string]: unknown }
  [key: string]: unknown
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly field?: string
  readonly body: ApiErrorBody | null

  constructor(status: number, code: string, message: string, field: string | undefined, body: ApiErrorBody | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.field = field
    this.body = body
  }

  /** On 409 topup_open the API returns the open top-up alongside the error. */
  get openTopup(): Topup | null {
    return this.body?.topup ?? this.body?.error?.topup ?? null
  }
}

type Method = 'GET' | 'POST'

async function request<T>(method: Method, path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'network_error', 'Could not reach the server. Check your connection and try again.', undefined, null)
  }

  const text = await res.text()
  let data: unknown = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = null
    }
  }

  if (!res.ok) {
    const errBody = (data && typeof data === 'object' ? data : null) as ApiErrorBody | null
    const err = errBody?.error
    throw new ApiError(
      res.status,
      err?.code ?? `http_${res.status}`,
      err?.message ?? errBody?.result?.detail ?? `The server answered HTTP ${res.status}.`,
      err?.field,
      errBody,
    )
  }
  return data as T
}

/** List endpoints may answer with a bare array or wrap it; accept both. */
function listFrom<T>(data: unknown, ...keys: string[]): T[] {
  if (Array.isArray(data)) return data as T[]
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>
    for (const key of [...keys, 'items', 'results', 'data']) {
      if (Array.isArray(obj[key])) return obj[key] as T[]
    }
  }
  return []
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const api = {
  // Customer
  createSession: () => request<{ customer_id: string }>('POST', '/api/session'),
  config: () => request<AppConfig>('GET', '/api/config'),
  predictProvider: (phone: string) => request<PredictedProvider>('POST', '/api/predict-provider', { phone }),
  scenarios: async () => listFrom<Scenario>(await request<unknown>('GET', '/api/scenarios'), 'scenarios'),
  wallet: () => request<Wallet>('GET', '/api/wallet'),
  createTopup: (input: CreateTopupInput, idempotencyKey?: string) =>
    request<{ topup: Topup }>(
      'POST',
      '/api/topups',
      input,
      idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    ),
  topup: (id: string) => request<TopupWithEvents>('GET', `/api/topups/${encodeURIComponent(id)}`),

  // Console
  stats: () => request<ConsoleStats>('GET', '/api/console/stats'),
  consoleTopups: async (params: { status?: string; q?: string; limit?: number }) =>
    listFrom<Topup>(await request<unknown>('GET', `/api/console/topups${qs({ limit: 50, ...params })}`), 'topups'),
  consoleTopup: async (id: string): Promise<ConsoleTopupDetail> => {
    const raw = await request<Record<string, unknown>>('GET', `/api/console/topups/${encodeURIComponent(id)}`)
    const ledger = (raw.ledger_entry ?? raw.ledger ?? null) as LedgerEntry | LedgerEntry[] | null
    return {
      topup: (raw.topup ?? raw) as Topup,
      events: listFrom<PaymentEvent>(raw.events),
      ledger_entry: Array.isArray(ledger) ? (ledger[0] ?? null) : ledger,
      outbox: listFrom<OutboxRow>(raw.outbox ?? raw.outbox_rows ?? raw.deliveries),
      balance_minor: typeof raw.balance_minor === 'number' ? raw.balance_minor : undefined,
    }
  },
  recheck: async (id: string) =>
    actionFrom(await request<unknown>('POST', `/api/console/topups/${encodeURIComponent(id)}/recheck`)),
  resendCallback: async (id: string) =>
    actionFrom(await request<unknown>('POST', `/api/console/topups/${encodeURIComponent(id)}/resend-callback`)),
  replayCallback: async (id: string) =>
    actionFrom(await request<unknown>('POST', `/api/console/topups/${encodeURIComponent(id)}/replay-callback`)),
  forgedCallback: async (id: string) =>
    actionFrom(await request<unknown>('POST', `/api/console/topups/${encodeURIComponent(id)}/forged-callback`)),
  reconcileRuns: async (limit = 20) =>
    listFrom<ReconcileRun>(await request<unknown>('GET', `/api/console/reconcile-runs${qs({ limit })}`), 'runs'),
  runReconcile: async () => {
    const raw = await request<Record<string, unknown>>('POST', '/api/console/reconcile')
    return (raw.run ?? raw) as ReconcileRun
  },
  events: async (limit = 100) =>
    listFrom<PaymentEvent>(await request<unknown>('GET', `/api/console/events${qs({ limit })}`), 'events'),

  // Ops
  health: () => request<Health>('GET', '/healthz'),
}
