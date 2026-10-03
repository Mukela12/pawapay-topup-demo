import type { Country, Provider, Topup } from './api'

// ISO 4217 minor-unit exponents for pawaPay currencies. The API sends `exponent`
// where it can; this table is the fallback.
const EXPONENTS: Record<string, number> = {
  ZMW: 2,
  KES: 2,
  GHS: 2,
  ETB: 2,
  TZS: 2,
  MWK: 2,
  NGN: 2,
  CDF: 2,
  USD: 2,
  MZN: 2,
  SLE: 2,
  LSL: 2,
  UGX: 0,
  RWF: 0,
  XOF: 0,
  XAF: 0,
}

export function exponentFor(currency: string, explicit?: number | null): number {
  if (typeof explicit === 'number') return explicit
  return EXPONENTS[currency.toUpperCase()] ?? 2
}

export function formatMinor(amountMinor: number, currency: string, exponent?: number | null): string {
  const exp = exponentFor(currency, exponent)
  const value = amountMinor / 10 ** exp
  const n = new Intl.NumberFormat('en', { minimumFractionDigits: exp, maximumFractionDigits: exp }).format(value)
  return `${currency} ${n}`
}

export function topupAmount(t: Pick<Topup, 'amount_minor' | 'currency' | 'exponent'>): string {
  return formatMinor(t.amount_minor, t.currency, t.exponent)
}

/** Formats a major-unit amount string or number from config, for example min/max. */
export function formatMajor(value: string | number | null | undefined, currency: string, decimals: number): string {
  if (value === null || value === undefined || value === '') return ''
  const n = Number(value)
  if (!Number.isFinite(n)) return `${currency} ${value}`
  const frac = decimals === 0 ? 0 : Number.isInteger(n) ? 0 : 2
  return `${currency} ${new Intl.NumberFormat('en', { minimumFractionDigits: frac, maximumFractionDigits: frac }).format(n)}`
}

const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})

function toDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatTime(iso: string | null | undefined): string {
  const d = toDate(iso)
  return d ? timeFmt.format(d) : ''
}

export function formatDateTime(iso: string | null | undefined): string {
  const d = toDate(iso)
  return d ? dateTimeFmt.format(d) : ''
}

export function msBetween(a: string | null | undefined, b: string | null | undefined): number | null {
  const da = toDate(a)
  const db = toDate(b)
  if (!da || !db) return null
  return db.getTime() - da.getTime()
}

/** "+0.4s", "+12s", "+3m 05s": elapsed from the start of the trace. */
export function formatDelta(ms: number | null): string {
  if (ms === null || ms < 0) return ''
  if (ms < 10_000) return `+${(ms / 1000).toFixed(1)}s`
  const s = Math.round(ms / 1000)
  if (s < 60) return `+${s}s`
  const m = Math.floor(s / 60)
  return `+${m}m ${String(s % 60).padStart(2, '0')}s`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return 'n/a'
  if (seconds < 10) return `${seconds.toFixed(1)}s`
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}

/** "0:42", "12:07": a running clock for open top-ups. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  return `${m}:${String(total % 60).padStart(2, '0')}`
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  const d = toDate(iso)
  if (!d) return ''
  const s = Math.round((now - d.getTime()) / 1000)
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return formatDateTime(iso)
}

/** +260 763 456 789. Masked numbers from the console come through unchanged. */
export function formatMsisdn(msisdn: string | null | undefined, prefix?: string): string {
  if (!msisdn) return ''
  if (/[^0-9]/.test(msisdn)) return msisdn
  const p = prefix && msisdn.startsWith(prefix) ? prefix : msisdn.slice(0, 3)
  const rest = msisdn.slice(p.length)
  const groups = rest.match(/.{1,3}/g) ?? [rest]
  return `+${p} ${groups.join(' ')}`
}

export function shortId(id: string | null | undefined): string {
  if (!id) return ''
  return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id
}

export function providerFor(countries: Country[] | undefined, country: string, provider: string): Provider | undefined {
  return countries?.find((c) => c.country === country)?.providers.find((p) => p.provider === provider)
}

/** Readable network name even before config has loaded: MTN_MOMO_ZMB becomes MTN MoMo. */
export function providerName(countries: Country[] | undefined, country: string, provider: string): string {
  const p = providerFor(countries, country, provider)
  if (p) return p.display_name
  const known: Record<string, string> = {
    MTN_MOMO_ZMB: 'MTN MoMo',
    AIRTEL_OAPI_ZMB: 'Airtel Money',
    ZAMTEL_ZMB: 'Zamtel Kwacha',
    MPESA_ETH: 'M-Pesa',
    MPESA_KEN: 'M-Pesa',
  }
  return known[provider] ?? provider.replace(/_/g, ' ')
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}
