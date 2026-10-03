import {
  ArrowsClockwiseIcon,
  CaretRightIcon,
  CircleNotchIcon,
  PaperPlaneTiltIcon,
  RepeatIcon,
  ShieldWarningIcon,
  type Icon,
} from '@phosphor-icons/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useId, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { toast } from 'sonner'
import { CopyId } from '../../components/CopyId'
import { EmptyState } from '../../components/EmptyState'
import { ErrorNotice, errorMessage } from '../../components/ErrorNotice'
import { OutcomePill, SignatureBadge, StatusPill } from '../../components/StatusPill'
import { PaymentTrace } from '../../components/PaymentTrace'
import { api, ApiError, isOpenStatus, type ActionResult, type PaymentEvent, type Topup } from '../../lib/api'
import { failureCopy } from '../../lib/failure-copy'
import {
  formatDateTime,
  formatMinor,
  formatMsisdn,
  formatTime,
  providerName,
  shortId,
  topupAmount,
} from '../../lib/format'
import { keys, useConfig, useConsoleTopup, useSession } from '../../lib/queries'
import { eventStatus } from '../../lib/trace'

const SOURCE_LABEL: Record<string, string> = {
  initiate_response: 'Initiate response',
  callback: 'Callback',
  status_check: 'Status check',
  reconcile: 'Reconciliation',
  replay: 'Replayed callback',
  forged_test: 'Forged callback test',
  resend_request: 'Resend request',
}

const TOPIC_LABEL: Record<string, string> = {
  'receipt.issued': 'Receipt issued',
  'voip_credit.granted': 'Calling credit granted',
}

type ActionId = 'recheck' | 'resend' | 'replay' | 'forged'

interface ActionDef {
  id: ActionId
  label: string
  /** The verb on the button. The full label stays its accessible name. */
  short: string
  icon: Icon
  body: ReactNode
  expect: string
  run: (id: string) => Promise<ActionResult>
  /** True when the answer is the one the demo is meant to show. */
  matches?: (data: ActionResult | undefined, err: unknown) => boolean
}

function httpOf(data: ActionResult | undefined, err: unknown): number | null {
  if (err instanceof ApiError) return err.status
  return data?.http_status ?? null
}

function outcomeOf(data: ActionResult | undefined, err: unknown): string | null {
  if (err instanceof ApiError) return err.body?.result?.outcome ?? null
  return data?.outcome ?? null
}

/** "Status stayed COMPLETED, balance unchanged at ZMW 25.00." */
function beforeAfter(data: ActionResult | undefined, currency: string, exponent?: number): string | null {
  if (!data) return null
  const parts: string[] = []
  if (data.status_before && data.status_after) {
    parts.push(
      data.status_before === data.status_after
        ? `Status stayed ${data.status_after}`
        : `Status went from ${data.status_before} to ${data.status_after}`,
    )
  }
  if (typeof data.balance_before === 'number' && typeof data.balance_after === 'number') {
    const a = formatMinor(data.balance_before, currency, exponent)
    const b = formatMinor(data.balance_after, currency, exponent)
    parts.push(data.balance_before === data.balance_after ? `balance unchanged at ${b}` : `balance went from ${a} to ${b}`)
  }
  if (!parts.length) return null
  const text = parts.join(', ')
  return `${text[0].toUpperCase()}${text.slice(1)}.`
}

const ACTIONS: ActionDef[] = [
  {
    id: 'recheck',
    label: 'Re-check with pawaPay',
    short: 'Re-check',
    icon: ArrowsClockwiseIcon,
    body: (
      <>
        Calls <code className="mono text-[12px]">GET /v2/deposits/&#123;depositId&#125;</code> now and applies the answer through
        the same guarded transition a callback uses.
      </>
    ),
    expect: 'On a final top-up: duplicate_ignored, nothing changes.',
    run: api.recheck,
  },
  {
    id: 'resend',
    label: 'Ask pawaPay to resend',
    short: 'Resend',
    icon: PaperPlaneTiltIcon,
    body: (
      <>
        Calls <code className="mono text-[12px]">POST /v2/deposits/resend-callback</code>. pawaPay only resends for payments in a
        final state.
      </>
    ),
    expect: 'A second real callback arrives and is ignored as a duplicate.',
    run: api.resendCallback,
  },
  {
    id: 'replay',
    label: 'Replay last callback',
    short: 'Replay',
    icon: RepeatIcon,
    body: 'Feeds the last stored callback, raw body and headers, back through the real webhook handler.',
    expect: 'HTTP 200, duplicate_ignored, balance unchanged.',
    run: api.replayCallback,
    matches: (d, e) => outcomeOf(d, e) === 'duplicate_ignored',
  },
  {
    id: 'forged',
    label: 'Send forged callback',
    short: 'Send forged',
    icon: ShieldWarningIcon,
    body: 'Posts a COMPLETED callback with an invalid signature, with signature checks forced on for that one request.',
    expect: 'HTTP 401, rejected, no state change.',
    run: api.forgedCallback,
    matches: (d, e) => httpOf(d, e) === 401 || outcomeOf(d, e) === 'rejected',
  },
]

function ActionCard({
  def,
  topup,
  hasCallback,
  pawapayConfigured,
}: {
  def: ActionDef
  topup: Topup
  hasCallback: boolean
  pawapayConfigured: boolean
}) {
  const qc = useQueryClient()
  const reasonId = useId()
  const [result, setResult] = useState<{ ok: boolean; data?: ActionResult; error?: unknown; at: string } | null>(null)
  const mutation = useMutation({
    mutationFn: () => def.run(topup.id),
    onSuccess: (data) => {
      setResult({ ok: true, data, at: new Date().toISOString() })
      toast(`${def.label}: ${String(data.outcome ?? data.pawapay_status ?? 'done').replace(/_/g, ' ')}`)
    },
    onError: (error) => {
      // If the API passes the webhook's 401 straight through, that is still the expected answer.
      const expected = def.matches?.(undefined, error) ?? false
      setResult({ ok: expected, error, at: new Date().toISOString() })
      if (expected) toast(`${def.label}: rejected with HTTP 401, as expected`)
      else toast.error(`${def.label}: ${errorMessage(error)}`)
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.consoleTopup(topup.id) })
      void qc.invalidateQueries({ queryKey: keys.stats })
      void qc.invalidateQueries({ queryKey: keys.events })
    },
  })

  // Mirrors the API: resend is accepted for COMPLETED, FAILED and NEEDS_ATTENTION only. A
  // REJECTED deposit never reached the network, so pawaPay has no callback to send again.
  const resendable = topup.status === 'COMPLETED' || topup.status === 'FAILED' || topup.status === 'NEEDS_ATTENTION'
  const needsPawapay = def.id === 'recheck' || def.id === 'resend'
  const disabledReason =
    needsPawapay && !pawapayConfigured
      ? 'Needs the pawaPay sandbox token, which is not set on this server.'
      : def.id === 'resend' && topup.status === 'REJECTED'
        ? 'pawaPay sends no callback for a rejected payment, so there is nothing to resend.'
        : def.id === 'resend' && !resendable
          ? 'Available once the payment is final.'
          : def.id === 'replay' && !hasCallback
            ? 'No callback has been stored for this top-up yet.'
            : null

  const IconCmp = def.icon
  const data = result?.data
  const err = result?.error
  const errBody = err instanceof ApiError ? err.body : null
  const matched = result && def.matches ? def.matches(data, err) : null

  return (
    <div className="px-4 py-4 sm:px-5">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-[14px] font-semibold text-ink">{def.label}</p>
          <p className="mt-1 text-[13px] text-ink-2">{def.body}</p>
          <p className="mt-1 text-[12px] text-ink-2">
            <span className="font-medium text-ink">Expect:</span> {def.expect}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm shrink-0"
          disabled={mutation.isPending || !!disabledReason}
          onClick={() => mutation.mutate()}
          aria-label={def.label}
          aria-describedby={disabledReason ? reasonId : undefined}
          title={disabledReason ?? undefined}
        >
          {mutation.isPending ? (
            <CircleNotchIcon size={15} weight="bold" className="animate-spin motion-reduce:animate-none" />
          ) : (
            <IconCmp size={15} weight="bold" />
          )}
          {def.short}
        </button>
      </div>
      {disabledReason ? (
        <p id={reasonId} className="mt-2 text-[12px] text-ink-2">
          {disabledReason}
        </p>
      ) : null}
      {result ? (
        <div
          className={`mt-3 rounded-control border px-3 py-2.5 ${
            result.ok ? 'border-line bg-inset/60' : 'border-danger/25 bg-danger-tint'
          }`}
          aria-live="polite"
        >
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            {data?.outcome ? <OutcomePill outcome={String(data.outcome)} /> : null}
            {data?.signature ? <SignatureBadge signature={data.signature} /> : null}
            {err instanceof ApiError ? (
              <span className={`pill ${matched ? 'pill-neutral' : 'pill-danger'}`}>HTTP {err.status || 'network'}</span>
            ) : null}
            {data?.http_status ? <span className="mono text-ink-2">HTTP {data.http_status}</span> : null}
            {matched ? <span className="pill pill-success">As expected</span> : null}
            <span className="mono ml-auto text-ink-2">{formatTime(result.at)}</span>
          </div>
          <p className="mt-1.5 text-[13px] text-ink">{data ? String(data.detail ?? 'Done.') : errorMessage(err)}</p>
          {beforeAfter(data, topup.currency, topup.exponent) ? (
            <p className="mt-1 text-[13px] text-ink-2">{beforeAfter(data, topup.currency, topup.exponent)}</p>
          ) : null}
          <details className="group mt-1.5">
            <summary className="inline-flex min-h-[32px] cursor-pointer list-none items-center gap-1 text-[12px] font-medium text-ink-2 hover:text-ink max-md:min-h-11 [&::-webkit-details-marker]:hidden">
              <CaretRightIcon size={12} weight="bold" className="transition-transform duration-150 group-open:rotate-90" />
              Response body
            </summary>
            <pre className="mono mt-2 max-h-56 overflow-auto rounded-control bg-surface p-2.5 text-[12px] leading-relaxed text-ink">
              {JSON.stringify(data ? data.raw : errBody, null, 2)}
            </pre>
          </details>
        </div>
      ) : null}
    </div>
  )
}

function EventItem({ event, first }: { event: PaymentEvent; first: boolean }) {
  const [open, setOpen] = useState(false)
  const status = eventStatus(event)
  return (
    <li className={`relative pl-6 ${first ? '' : 'pt-5'}`}>
      <span aria-hidden="true" className="absolute left-[5px] top-0 h-full w-px bg-line" />
      <span
        aria-hidden="true"
        className={`absolute left-0 size-[11px] rounded-full border-2 border-surface ${first ? 'top-1' : 'top-6'} ${
          event.outcome === 'applied'
            ? 'bg-accent'
            : event.outcome === 'rejected' || event.outcome === 'error'
              ? 'bg-danger'
              : event.outcome === 'held_for_review'
                ? 'bg-warning'
                : 'bg-line-strong'
        }`}
      />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <p className="text-[14px] font-semibold text-ink">{SOURCE_LABEL[event.source] ?? event.source}</p>
        {status ? <span className="mono text-[12px] text-ink-2">{status}</span> : null}
        <span className="mono ml-auto text-[12px] text-ink-2">{formatTime(event.created_at)}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <OutcomePill outcome={event.outcome} />
        <SignatureBadge signature={event.signature} />
        {event.http_status ? <span className="pill pill-neutral mono">HTTP {event.http_status}</span> : null}
      </div>
      {event.detail ? <p className="mt-1.5 text-[13px] text-ink-2">{event.detail}</p> : null}
      {event.payload !== undefined || event.headers ? (
        <div className="mt-1.5">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            className="inline-flex min-h-[32px] items-center gap-1 text-[12px] font-medium text-ink-2 hover:text-ink max-md:min-h-[44px]"
          >
            <CaretRightIcon size={12} weight="bold" className={`transition-transform duration-150 ${open ? 'rotate-90' : ''}`} />
            {open ? 'Hide raw JSON' : 'Show raw JSON'}
          </button>
          {open ? (
            <div className="mt-1 space-y-2">
              {event.headers ? (
                <pre className="mono max-h-40 overflow-auto rounded-control border border-line bg-inset/50 p-3 text-[12px] leading-relaxed text-ink">
                  {JSON.stringify(event.headers, null, 2)}
                </pre>
              ) : null}
              <pre className="mono max-h-80 overflow-auto rounded-control border border-line bg-inset/50 p-3 text-[12px] leading-relaxed text-ink">
                {JSON.stringify(event.payload ?? null, null, 2)}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <dt className="shrink-0 text-[13px] text-ink-2">{label}</dt>
      <dd className="min-w-0 text-right text-[13px] text-ink">{children}</dd>
    </div>
  )
}

function Panel({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
        <h2 className="text-[14px] tracking-[-0.01em] text-ink">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

export function TopupDetailPage() {
  const { id = '' } = useParams()
  const query = useConsoleTopup(id)
  // Config is only for network names and the reconcile threshold; the console works without it.
  const session = useSession()
  const config = useConfig(session.isSuccess)

  if (query.isPending) {
    return <p className="py-16 text-center text-[14px] text-ink-2">Loading top-up…</p>
  }
  if (query.isError) {
    const notFound = query.error instanceof ApiError && query.error.status === 404
    return (
      <div className="space-y-4">
        <Breadcrumb id={id} />
        <ErrorNotice
          error={query.error}
          title={notFound ? 'No top-up with this ID' : 'Could not load the top-up'}
          onRetry={notFound ? undefined : () => void query.refetch()}
        />
      </div>
    )
  }

  const { topup, events, ledger_entry: ledger, outbox } = query.data
  const sorted = [...events].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
  const hasCallback = sorted.some((e) => e.source === 'callback')
  const network = providerName(config.data?.countries, topup.country, topup.provider)
  const prefix = config.data?.countries.find((c) => c.country === topup.country)?.prefix

  return (
    <div>
      <Breadcrumb id={topup.deposit_id} />

      <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="mono text-[26px] font-semibold leading-tight tracking-[-0.02em] text-ink">{topupAmount(topup)}</h1>
            <StatusPill status={topup.status} />
          </div>
          <p className="mt-1 text-[14px] text-ink-2">
            {network}, <span className="mono">{formatMsisdn(topup.phone, prefix)}</span>, started {formatDateTime(topup.created_at)}
          </p>
        </div>
        <div className="flex min-w-0 items-center gap-2 text-[13px] text-ink-2">
          <span className="shrink-0">Deposit ID</span>
          <CopyId value={topup.deposit_id} label="Copy deposit ID" />
        </div>
      </div>

      <section className="card mt-6 px-4 py-5 sm:px-6" aria-labelledby="trace-h">
        <div className="mb-5 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="trace-h" className="text-[14px] tracking-[-0.01em] text-ink">
            Payment trace
          </h2>
          {isOpenStatus(topup.status) ? (
            <span className="text-[12px] text-ink-2">Polling every 1.5 seconds while open</span>
          ) : null}
        </div>
        <PaymentTrace topup={topup} events={sorted} reconcileAfterSeconds={config.data?.reconcile_after_seconds} />
      </section>

      <div className="mt-6 grid grid-cols-[minmax(0,1fr)] gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-6">
          <Panel title="Actions">
            <div className="divide-y divide-line">
              {ACTIONS.map((def) => (
                <ActionCard
                  key={def.id}
                  def={def}
                  topup={topup}
                  hasCallback={hasCallback}
                  pawapayConfigured={config.data?.pawapay_configured !== false}
                />
              ))}
            </div>
          </Panel>

          <Panel title="Events" aside={<span className="text-[12px] text-ink-2">{sorted.length} stored, oldest first</span>}>
            <div className="px-4 py-5 sm:px-5">
              {sorted.length === 0 ? (
                <EmptyState inline title="No events yet">
                  The first event is the initiate response from pawaPay, stored the moment the API call returns.
                </EmptyState>
              ) : (
                <ol className="[&>li:last-child>span:first-child]:h-6">
                  {sorted.map((e, i) => (
                    <EventItem key={e.id} event={e} first={i === 0} />
                  ))}
                </ol>
              )}
            </div>
          </Panel>
        </div>

        <div className="min-w-0 space-y-6">
          <Panel title="Ledger entry">
            <div className="px-4 py-3 sm:px-5">
              {ledger ? (
                <dl className="divide-y divide-line">
                  <Field label="Amount">
                    <span className="mono text-success">+{formatMinor(ledger.amount_minor, ledger.currency ?? topup.currency, topup.exponent)}</span>
                  </Field>
                  <Field label="Kind">
                    <span className="mono">{ledger.kind}</span>
                  </Field>
                  <Field label="Written">
                    <span className="mono">{formatTime(ledger.created_at)}</span>
                  </Field>
                  <Field label="Entry ID">
                    <span className="mono">{shortId(ledger.id)}</span>
                  </Field>
                </dl>
              ) : (
                <div>
                  <EmptyState inline title="No credit written">
                    Only a COMPLETED top-up whose amount and currency match is credited, and the unique key on (topup_id, kind)
                    makes a second credit impossible.
                  </EmptyState>
                </div>
              )}
            </div>
          </Panel>

          <Panel title="Outbox deliveries">
            <div className="px-4 py-3 sm:px-5">
              {outbox.length === 0 ? (
                <div>
                  <EmptyState inline title="Nothing queued">
                    The credit and two outbox rows are written in one transaction, so a receipt never goes out without a credit.
                  </EmptyState>
                </div>
              ) : (
                <ul className="divide-y divide-line">
                  {outbox.map((o) => (
                    <li key={o.id} className="flex items-start justify-between gap-3 py-2.5">
                      <div className="min-w-0">
                        <p className="text-[13px] font-medium text-ink">{TOPIC_LABEL[o.topic] ?? o.label ?? o.topic}</p>
                        <p className="mono truncate text-[12px] text-ink-2">{o.dedupe_key}</p>
                      </div>
                      <div className="shrink-0 text-right">
                        {o.delivered_at ? (
                          <span className="pill pill-success">Delivered {formatTime(o.delivered_at)}</span>
                        ) : (
                          <span className="pill pill-neutral">
                            <span className="live-dot" aria-hidden="true" />
                            Queued
                          </span>
                        )}
                        <p className="mt-1 text-[12px] text-ink-2">
                          {o.attempts} {o.attempts === 1 ? 'attempt' : 'attempts'}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Panel>

          <Panel title="Record">
            <div className="px-4 py-3 sm:px-5">
              <dl className="divide-y divide-line">
                <Field label="Status">
                  <span className="mono">{topup.status}</span>
                </Field>
                {topup.failure_code ? (
                  <Field label="Failure">
                    <span className="mono">{topup.failure_code}</span>
                    <span className="block text-ink-2">{failureCopy(topup.failure_code).short}</span>
                  </Field>
                ) : null}
                {topup.failure_message ? <Field label="pawaPay message">{topup.failure_message}</Field> : null}
                <Field label="Provider">
                  <span className="mono">{topup.provider}</span>
                </Field>
                {topup.provider_txn_id ? (
                  <Field label="Network reference">
                    <span className="mono break-all">{topup.provider_txn_id}</span>
                  </Field>
                ) : null}
                {topup.scenario ? (
                  <Field label="Sandbox scenario">
                    <span className="mono">{topup.scenario}</span>
                  </Field>
                ) : null}
                <Field label="Created">
                  <span className="mono">{formatTime(topup.created_at)}</span>
                </Field>
                <Field label="Accepted">
                  <span className="mono">{formatTime(topup.accepted_at) || 'not yet'}</span>
                </Field>
                <Field label="Final">
                  <span className="mono">
                    {formatTime(topup.finalized_at) ||
                      (isOpenStatus(topup.status)
                        ? 'still open'
                        : topup.status === 'NEEDS_ATTENTION'
                          ? 'held for review'
                          : 'not recorded')}
                  </span>
                </Field>
                <Field label="Status checks">
                  <span className="mono">{topup.check_count}</span>
                  {topup.last_checked_at ? <span className="text-ink-2">, last {formatTime(topup.last_checked_at)}</span> : null}
                </Field>
              </dl>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}

function Breadcrumb({ id }: { id: string }) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[13px] text-ink-2">
      <Link to="/console" className="inline-flex min-h-[32px] items-center hover:text-ink max-md:min-h-11">
        Top-ups
      </Link>
      <CaretRightIcon size={12} />
      <span className="mono truncate text-ink">{shortId(id)}</span>
    </nav>
  )
}
