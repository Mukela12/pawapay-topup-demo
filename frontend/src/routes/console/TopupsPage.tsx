import { MagnifyingGlassIcon } from '@phosphor-icons/react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { ErrorNotice } from '../../components/ErrorNotice'
import { StatusPill } from '../../components/StatusPill'
import { isOpenStatus, type ConsoleStats, type Topup } from '../../lib/api'
import { formatDateTime, formatDuration, formatMinor, formatMsisdn, providerName, shortId, topupAmount } from '../../lib/format'
import { useDebounced } from '../../lib/hooks'
import { useConfig, useConsoleTopups, useSession, useStats } from '../../lib/queries'

export const STATUS_GROUPS = [
  { id: 'all', label: 'All' },
  { id: 'open', label: 'Open' },
  { id: 'completed', label: 'Completed' },
  { id: 'failed', label: 'Failed' },
  { id: 'attention', label: 'Needs attention' },
] as const
type Group = (typeof STATUS_GROUPS)[number]['id']

/** Start of the empty-filter sentence; a search adds " match “q”." */
const EMPTY_PHRASE: Record<Group, string> = {
  all: 'No top-ups',
  open: 'No open top-ups',
  completed: 'No completed top-ups',
  failed: 'No failed top-ups',
  attention: 'No top-ups that need attention',
}

/** The API takes OPEN or a comma list of statuses. Rows are re-checked here too. */
function apiStatus(group: Group): string {
  if (group === 'open') return 'OPEN'
  if (group === 'completed') return 'COMPLETED'
  if (group === 'failed') return 'FAILED,REJECTED'
  if (group === 'attention') return 'NEEDS_ATTENTION'
  return ''
}

function inGroup(t: Topup, group: Group): boolean {
  switch (group) {
    case 'open':
      return isOpenStatus(t.status)
    case 'completed':
      return t.status === 'COMPLETED'
    case 'failed':
      return t.status === 'FAILED' || t.status === 'REJECTED'
    case 'attention':
      return t.status === 'NEEDS_ATTENTION'
    default:
      return true
  }
}

function StatStrip({ stats, loading }: { stats?: ConsoleStats; loading: boolean }) {
  const rate = stats?.success_rate_7d
  const ratePct = rate === null || rate === undefined ? null : rate <= 1 ? rate * 100 : rate
  const credited = stats?.total_credited ?? []
  const tiles: { label: string; value: string; tone?: string; note?: string }[] = [
    { label: 'Completed today', value: String(stats?.completed_today ?? 0) },
    { label: 'Failed today', value: String(stats?.failed_today ?? 0) },
    { label: 'Open now', value: String(stats?.open_now ?? 0) },
    {
      label: 'Needs attention',
      value: String(stats?.needs_attention ?? 0),
      tone: stats?.needs_attention ? 'text-warning' : undefined,
    },
    { label: 'Success rate, 7 days', value: ratePct === null ? 'n/a' : `${ratePct.toFixed(ratePct % 1 === 0 ? 0 : 1)}%` },
    { label: 'Median time to final', value: formatDuration(stats?.median_seconds_to_final) },
    {
      label: 'Total credited',
      value: credited.length ? formatMinor(credited[0].amount_minor, credited[0].currency) : 'None yet',
      note: credited.length > 1 ? credited.slice(1).map((c) => formatMinor(c.amount_minor, c.currency)).join(', ') : undefined,
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-4 xl:grid-cols-7">
      {tiles.map((t, i) => (
        <div key={t.label} className={`bg-surface px-4 py-4 ${i === tiles.length - 1 ? 'col-span-2 xl:col-span-1' : ''}`}>
          <p className="text-[12px] text-ink-2">{t.label}</p>
          {loading ? (
            <div className="mt-2 h-7 w-16 rounded-pill bg-inset" />
          ) : (
            <p className={`mt-1 text-[22px] font-semibold leading-tight tracking-[-0.02em] ${t.tone ?? 'text-ink'}`}>{t.value}</p>
          )}
          {t.note ? <p className="mono mt-0.5 text-[12px] text-ink-2">{t.note}</p> : null}
        </div>
      ))}
    </div>
  )
}

export function TopupsPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const group = (STATUS_GROUPS.find((g) => g.id === params.get('status'))?.id ?? 'all') as Group
  const [search, setSearch] = useState(params.get('q') ?? '')
  const q = useDebounced(search.trim(), 300)

  useEffect(() => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (q) next.set('q', q)
        else next.delete('q')
        return next
      },
      { replace: true },
    )
  }, [q, setParams])

  const stats = useStats()
  const list = useConsoleTopups(apiStatus(group), q)
  const session = useSession()
  const countries = useConfig(session.isSuccess).data?.countries
  const rows = (list.data ?? []).filter((t) => inGroup(t, group))

  function setGroup(id: Group) {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (id === 'all') next.delete('status')
      else next.set('status', id)
      return next
    })
  }

  return (
    <div>
      <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-[24px] leading-tight text-ink">Top-ups</h1>
          <p className="mt-1 text-[14px] text-ink-2">Every top-up in the database, newest first. Refreshes every 5 seconds.</p>
        </div>
      </div>

      <div className="mt-6">
        {stats.isError ? (
          <ErrorNotice error={stats.error} title="Could not load the numbers" onRetry={() => void stats.refetch()} />
        ) : (
          <StatStrip stats={stats.data} loading={stats.isPending} />
        )}
      </div>

      <div className="mt-8 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="scroll-fade-x -mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <div role="group" aria-label="Filter by status" className="inline-flex gap-1 rounded-control bg-inset p-1">
            {STATUS_GROUPS.map((g) => (
              <button
                key={g.id}
                type="button"
                aria-pressed={group === g.id}
                onClick={() => setGroup(g.id)}
                className={`h-9 min-w-11 shrink-0 whitespace-nowrap rounded-[5px] px-3 text-[13px] font-medium transition-colors duration-150 max-md:h-11 ${
                  group === g.id ? 'bg-surface text-ink shadow-xs' : 'text-ink-2 hover:text-ink'
                }`}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>
        <label className="relative block w-full lg:w-[320px]">
          <span className="sr-only">Search by deposit ID or phone</span>
          <MagnifyingGlassIcon size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-2" />
          <input
            type="search"
            className="field pl-9"
            placeholder="Search deposit ID or phone"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>

      <div className="mt-4">
        {list.isError ? (
          <ErrorNotice error={list.error} title="Could not load top-ups" onRetry={() => void list.refetch()} />
        ) : list.isPending ? (
          <div className="card space-y-px overflow-hidden">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-12 bg-inset/60" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          q || group !== 'all' ? (
            <EmptyState
              title="Nothing matches this filter"
              action={
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setSearch('')
                    setParams({})
                  }}
                >
                  Clear filters
                </button>
              }
            >
              {EMPTY_PHRASE[group]}
              {q ? ` match “${q}”` : ''}.
            </EmptyState>
          ) : (
            <EmptyState
              title="No top-ups yet"
              action={
                <Link to="/wallet" className="btn btn-primary btn-sm">
                  Start one from the wallet
                </Link>
              }
            >
              Top-ups appear here the moment the Flask API stores them, before pawaPay is even called.
            </EmptyState>
          )
        ) : (
          <>
            {/* Table from lg up; the sidebar leaves too little room at tablet width */}
            <div className="card hidden overflow-x-auto lg:block">
              <table className="w-full text-left text-[13px]">
                <thead className="border-b border-line bg-inset/60 text-[12px] text-ink-2">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Created</th>
                    <th className="px-4 py-2.5 font-medium">Deposit ID</th>
                    <th className="px-4 py-2.5 font-medium">Phone</th>
                    <th className="px-4 py-2.5 font-medium">Network</th>
                    <th className="px-4 py-2.5 text-right font-medium">Amount</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((t) => (
                    <tr
                      key={t.id}
                      className="table-row-link"
                      onClick={() => navigate(`/console/topups/${t.id}`)}
                    >
                      <td className="whitespace-nowrap px-4 py-3 text-ink-2">{formatDateTime(t.created_at)}</td>
                      <td className="whitespace-nowrap px-4 py-3">
                        <Link
                          to={`/console/topups/${t.id}`}
                          className="mono text-ink underline decoration-transparent underline-offset-4 hover:decoration-ink-3"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {shortId(t.deposit_id)}
                        </Link>
                      </td>
                      <td className="mono whitespace-nowrap px-4 py-3 text-ink-2">{formatMsisdn(t.phone)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-ink-2">{providerName(countries, t.country, t.provider)}</td>
                      <td className="mono whitespace-nowrap px-4 py-3 text-right text-ink">{topupAmount(t)}</td>
                      <td className="px-4 py-3">
                        <StatusPill status={t.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Stacked rows on phones */}
            <ul className="card divide-y divide-line overflow-hidden lg:hidden">
              {rows.map((t) => (
                <li key={t.id}>
                  <Link to={`/console/topups/${t.id}`} className="table-row-link flex items-start gap-3 px-4 py-3">
                    <span className="min-w-0 flex-1">
                      <span className="mono block text-[14px] font-medium text-ink">{topupAmount(t)}</span>
                      <span className="mono block truncate text-[12px] text-ink-2">{shortId(t.deposit_id)}</span>
                      <span className="block truncate text-[12px] text-ink-2">
                        {providerName(countries, t.country, t.provider)}, {formatMsisdn(t.phone)}
                      </span>
                    </span>
                    <span className="flex shrink-0 flex-col items-end gap-1">
                      <StatusPill status={t.status} />
                      <span className="text-[12px] text-ink-2">{formatDateTime(t.created_at)}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
