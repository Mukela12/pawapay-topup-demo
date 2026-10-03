import { CaretDownIcon } from '@phosphor-icons/react'
import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { ErrorNotice } from '../../components/ErrorNotice'
import { OutcomePill, SignatureBadge } from '../../components/StatusPill'
import type { PaymentEvent } from '../../lib/api'
import { formatDateTime, shortId } from '../../lib/format'
import { useEvents } from '../../lib/queries'
import { eventStatus } from '../../lib/trace'

const SOURCES = [
  { id: 'all', label: 'All sources' },
  { id: 'callback', label: 'Callbacks' },
  { id: 'initiate_response', label: 'Initiate responses' },
  { id: 'status_check', label: 'Status checks' },
  { id: 'reconcile', label: 'Reconciliation' },
  { id: 'replay', label: 'Replays' },
  { id: 'forged_test', label: 'Forged tests' },
  { id: 'resend_request', label: 'Resend requests' },
] as const

const SOURCE_LABEL: Record<string, string> = {
  initiate_response: 'Initiate response',
  callback: 'Callback',
  status_check: 'Status check',
  reconcile: 'Reconciliation',
  replay: 'Replay',
  forged_test: 'Forged test',
  resend_request: 'Resend request',
}

function topupLink(e: PaymentEvent, touch = false) {
  if (!e.topup_id) return <span className="mono text-ink-2">{e.deposit_id ? shortId(e.deposit_id) : 'unmatched'}</span>
  return (
    <Link
      to={`/console/topups/${e.topup_id}`}
      className={`mono text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink ${
        touch ? 'inline-flex min-h-11 items-center' : ''
      }`}
    >
      {shortId(e.deposit_id ?? e.topup_id)}
    </Link>
  )
}

export function EventsPage() {
  const events = useEvents()
  // The filter lives in the URL (?source=callback), like the Top-ups page, so it survives a reload and can be linked.
  const [params, setParams] = useSearchParams()
  const source = SOURCES.find((s) => s.id === params.get('source'))?.id ?? 'all'
  function setSource(id: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (id === 'all') next.delete('source')
      else next.set('source', id)
      return next
    })
  }
  const rows = useMemo(
    () =>
      (events.data ?? [])
        .filter((e) => source === 'all' || e.source === source)
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [events.data, source],
  )

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-[68ch]">
          <h1 className="text-[24px] leading-tight text-ink">Event log</h1>
          <p className="mt-1 text-[14px] text-ink-2">
            Every response, callback and check is stored before anything acts on it, including the ones that were rejected.
            Auth headers are never stored.
          </p>
        </div>
        <label className="relative block w-full sm:w-[220px]">
          <span className="sr-only">Filter by source</span>
          <select className="field appearance-none pr-9" value={source} onChange={(e) => setSource(e.target.value)}>
            {SOURCES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
          <CaretDownIcon
            size={14}
            weight="bold"
            aria-hidden="true"
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-2"
          />
        </label>
      </div>

      <div className="mt-6">
        {events.isError ? (
          <ErrorNotice error={events.error} title="Could not load events" onRetry={() => void events.refetch()} />
        ) : events.isPending ? (
          <div className="card h-48 bg-inset/40" />
        ) : rows.length === 0 ? (
          <EmptyState title={source === 'all' ? 'No events yet' : 'No events from this source'}>
            {source === 'all'
              ? 'Start a top-up from the wallet and its initiate response lands here first.'
              : 'Pick another source, or trigger one from a top-up’s detail page.'}
          </EmptyState>
        ) : (
          <>
            <div className="card hidden overflow-x-auto xl:block">
              <table className="w-full text-left text-[13px]">
                <thead className="border-b border-line bg-inset/60 text-[12px] text-ink-2">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Time</th>
                    <th className="px-4 py-2.5 font-medium">Source</th>
                    <th className="px-4 py-2.5 font-medium">Deposit ID</th>
                    <th className="px-4 py-2.5 font-medium">pawaPay status</th>
                    <th className="px-4 py-2.5 font-medium">HTTP</th>
                    <th className="px-4 py-2.5 font-medium">Signature</th>
                    <th className="px-4 py-2.5 font-medium">Outcome</th>
                    <th className="px-4 py-2.5 font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((e) => (
                    <tr key={e.id} className="align-top">
                      <td className="whitespace-nowrap px-4 py-3 text-ink-2">{formatDateTime(e.created_at)}</td>
                      <td className="whitespace-nowrap px-4 py-3 font-medium text-ink">{SOURCE_LABEL[e.source] ?? e.source}</td>
                      <td className="whitespace-nowrap px-4 py-3">{topupLink(e)}</td>
                      <td className="mono whitespace-nowrap px-4 py-3 text-ink-2">{eventStatus(e) ?? ''}</td>
                      <td className="mono px-4 py-3 text-ink-2">{e.http_status ?? ''}</td>
                      <td className="px-4 py-3">
                        <SignatureBadge signature={e.signature} />
                      </td>
                      <td className="px-4 py-3">
                        <OutcomePill outcome={e.outcome} />
                      </td>
                      <td className="max-w-[320px] px-4 py-3 text-ink-2">{e.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="card divide-y divide-line overflow-hidden xl:hidden">
              {rows.map((e) => (
                <li key={e.id} className="px-4 py-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="text-[14px] font-semibold text-ink">{SOURCE_LABEL[e.source] ?? e.source}</p>
                    <p className="shrink-0 text-[12px] text-ink-2">{formatDateTime(e.created_at)}</p>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <OutcomePill outcome={e.outcome} />
                    <SignatureBadge signature={e.signature} />
                    {e.http_status ? <span className="pill pill-neutral mono">HTTP {e.http_status}</span> : null}
                  </div>
                  <p className="mt-0.5 text-[13px]">{topupLink(e, true)}</p>
                  {e.detail ? <p className="mt-1 text-[13px] text-ink-2">{e.detail}</p> : null}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  )
}
