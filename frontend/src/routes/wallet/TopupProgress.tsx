import { ArrowCounterClockwiseIcon, InfoIcon } from '@phosphor-icons/react'
import { useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { CopyId } from '../../components/CopyId'
import { ErrorNotice } from '../../components/ErrorNotice'
import { LordIcon } from '../../components/LordIcon'
import { PaymentTrace } from '../../components/PaymentTrace'
import { ReceiptCard } from '../../components/ReceiptCard'
import { ApiError, isOpenStatus, type AppConfig, type PinInstruction, type Topup, type Wallet } from '../../lib/api'
import { failureCopy, isKnownFailure } from '../../lib/failure-copy'
import { exponentFor, formatClock, formatMsisdn, providerFor, providerName, topupAmount } from '../../lib/format'
import { useNow } from '../../lib/hooks'
import { keys, useTopup } from '../../lib/queries'
import { settlingEvent } from '../../lib/trace'
import type { TopupDraft } from './TopupForm'

function channelsWithSteps(list: PinInstruction[] | undefined): PinInstruction[] {
  return (list ?? []).filter((c) => c && Array.isArray(c.steps) && c.steps.length > 0)
}

export function draftFrom(t: Topup, prefix: string): TopupDraft {
  const exp = exponentFor(t.currency, t.exponent)
  const major = String(Number((t.amount_minor / 10 ** exp).toFixed(exp)))
  const local = t.phone.startsWith(prefix) ? t.phone.slice(prefix.length) : t.phone
  return { country: t.country, provider: t.provider, phoneLocal: local, amount: major }
}

type Phase = 'waiting' | 'completed' | 'failed' | 'held'

function phaseOf(t: Topup): Phase {
  if (isOpenStatus(t.status)) return 'waiting'
  if (t.status === 'COMPLETED') return 'completed'
  if (t.status === 'NEEDS_ATTENTION') return 'held'
  return 'failed'
}

interface TopupProgressProps {
  topupId: string
  config: AppConfig
  ready: boolean
  onBack: () => void
  onRetry: (draft: TopupDraft) => void
}

export function TopupProgress({ topupId, config, ready, onBack, onRetry }: TopupProgressProps) {
  const qc = useQueryClient()
  const reduce = useReducedMotion()
  const query = useTopup(topupId, ready)
  const topup = query.data?.topup
  const events = query.data?.events ?? []

  // When a top-up we were watching turns final: refresh the balance and say so.
  const lastStatus = useRef<string | null>(null)
  useEffect(() => {
    if (!topup) return
    const prev = lastStatus.current
    lastStatus.current = topup.status
    // Keep the history row in step with the polled status without refetching the wallet.
    if (prev !== topup.status) {
      qc.setQueryData<Wallet>(keys.wallet, (w) =>
        w ? { ...w, topups: w.topups.map((t) => (t.id === topup.id ? topup : t)) } : w,
      )
    }
    if (prev && isOpenStatus(prev) && !isOpenStatus(topup.status)) {
      // The receipt says it on screen; the live region below announces it.
      void qc.invalidateQueries({ queryKey: keys.wallet })
    }
  }, [topup, qc])

  const open = topup ? isOpenStatus(topup.status) : false
  const now = useNow(open ? 1000 : null)

  // When the status turns final while the customer is watching, hold the waiting
  // view for a beat so the last hops light on the trace, then show the result.
  const livePhase: Phase | null = topup ? phaseOf(topup) : null
  const [shownPhase, setShownPhase] = useState<Phase | null>(null)
  const [watchedLive, setWatchedLive] = useState(false)
  // Lock in the first phase we see (state adjusted during render, no effect needed).
  if (livePhase !== null && shownPhase === null) setShownPhase(livePhase)
  useEffect(() => {
    if (livePhase === null || shownPhase === null || livePhase === shownPhase) return
    const delay = shownPhase === 'waiting' && !reduce ? 1100 : 0
    const id = window.setTimeout(() => {
      if (shownPhase === 'waiting') setWatchedLive(true)
      setShownPhase(livePhase)
    }, delay)
    return () => window.clearTimeout(id)
  }, [livePhase, shownPhase, reduce])

  if (query.isPending) {
    return (
      <div className="py-16 text-center text-[14px] text-ink-2" role="status">
        Loading the top-up…
      </div>
    )
  }

  if (query.isError || !topup) {
    const notFound = query.error instanceof ApiError && query.error.status === 404
    return (
      <div className="space-y-4">
        <ErrorNotice
          error={notFound ? new Error('This top-up does not belong to this browser’s wallet, or it no longer exists.') : query.error}
          title={notFound ? 'Top-up not found' : 'Could not load the top-up'}
          onRetry={notFound ? undefined : () => void query.refetch()}
        />
        <button type="button" className="btn btn-secondary" onClick={onBack}>
          Back to the top-up form
        </button>
      </div>
    )
  }

  const country = config.countries.find((c) => c.country === topup.country)
  const prefix = country?.prefix ?? ''
  const provider = providerFor(config.countries, topup.country, topup.provider)
  const network = providerName(config.countries, topup.country, topup.provider)
  const phase = shownPhase ?? phaseOf(topup)
  const elapsedMs = now - new Date(topup.created_at).getTime()
  const overdue = open && elapsedMs / 1000 > config.reconcile_after_seconds
  const channels = channelsWithSteps(provider?.pin_prompt_instructions)
  const settle = settlingEvent(topup, events)
  const known = failureCopy(topup.failure_code)
  // Codes this page has no copy for (for example amount mismatches) fall back to the API's reason.
  const copy = isKnownFailure(topup.failure_code) ? known : { ...known, body: topup.reason ?? known.body }

  const motionProps = {
    initial: { opacity: 0, transform: reduce ? 'none' : 'translateY(6px)' },
    animate: { opacity: 1, transform: 'translateY(0px)' },
    exit: { opacity: 0, transition: { duration: 0.12 } },
    transition: { duration: 0.22, ease: [0.16, 1, 0.3, 1] as const },
  }

  const trace = (
    <PaymentTrace
      topup={topup}
      events={events}
      reconcileAfterSeconds={config.reconcile_after_seconds}
      playOnMount={phase === 'waiting' || !watchedLive}
    />
  )

  return (
    <div>
      <p className="sr-only" aria-live="polite">
        {phase === 'waiting'
          ? 'Waiting for the payment to be approved.'
          : phase === 'completed'
            ? `Top-up complete. ${topupAmount(topup)} added.`
            : phase === 'held'
              ? 'This payment is held for review.'
              : `Top-up failed. ${copy.title}.`}
      </p>
      <AnimatePresence mode="wait" initial={false}>
        {phase === 'waiting' ? (
          <motion.div key="waiting" {...motionProps}>
            {/* Phones: icon and text side by side, the clock under the text. From sm the clock takes its own column. */}
            <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:gap-x-4">
              <LordIcon
                name="phone"
                trigger="loop"
                state="hover-phone-ring"
                delay={700}
                size={52}
                color="#2747D6"
                className="max-sm:size-10!"
              />
              <div className="min-w-0">
                <h2 className="text-[20px] leading-tight text-ink">
                  Approve <span className="whitespace-nowrap">{topupAmount(topup)}</span> on the phone
                </h2>
                <p className="mt-1 text-[14px] text-ink-2">
                  {network} is asking <span className="mono whitespace-nowrap text-ink">{formatMsisdn(topup.phone, prefix)}</span> for the
                  PIN.
                </p>
              </div>
              <p className="col-start-2 mt-2 text-[13px] text-ink-2 sm:col-start-3 sm:row-start-1 sm:mt-0 sm:pt-1">
                <span className="sm:sr-only">Open for </span>
                <span className="mono text-[14px]">{formatClock(elapsedMs)}</span>
              </p>
            </div>

            <div className="mt-5 flex items-start gap-3 rounded-card bg-inset px-4 py-3">
              <InfoIcon size={18} className="mt-0.5 shrink-0 text-ink-2" />
              <p className="text-[13px] text-ink-2">
                <span className="font-semibold text-ink">No phone rings in the sandbox.</span> pawaPay’s sandbox simulates the
                customer’s PIN approval, and the test number decides the result. Everything else here is real: the API call, the
                callback and the ledger write.
              </p>
            </div>

            {channels.length > 0 || provider?.pin_prompt === 'MANUAL' ? (
              <div className="mt-5">
                <h3 className="text-[14px] text-ink">On a real phone</h3>
                {provider?.pin_prompt === 'MANUAL' ? (
                  <p className="mt-1 text-[13px] text-ink-2">
                    {network} does not push a prompt. The customer approves from the network’s menu.
                  </p>
                ) : null}
                <div className="mt-2 grid gap-4 sm:grid-cols-2">
                  {channels.map((ch, ci) => (
                    <div key={ci}>
                      {ch.display_name ? <p className="text-[13px] font-medium text-ink">{ch.display_name}</p> : null}
                      <ol className="mt-1 list-decimal space-y-1 pl-5 text-[13px] text-ink-2 marker:text-ink-2">
                        {(ch.steps ?? []).map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ol>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <section className="mt-7" aria-labelledby="trace-h">
              <h3 id="trace-h" className="mb-4 text-[14px] text-ink">
                Payment trace
              </h3>
              {trace}
            </section>

            {overdue ? (
              <div className="mt-6 flex items-start gap-3 rounded-card border border-line bg-surface px-4 py-3">
                <LordIcon name="clock" trigger="loop" state="loop-clock" size={32} color="#46575A" />
                <p className="text-[13px] text-ink-2">
                  <span className="font-semibold text-ink">Still open after {formatClock(elapsedMs)}.</span> pawaPay has not sent
                  a final status yet. The reconciliation job asks pawaPay directly about any top-up open longer than{' '}
                  {config.reconcile_after_seconds} seconds and settles it as soon as pawaPay has an answer, callback or not.
                </p>
              </div>
            ) : null}

            <div className="mt-7 flex flex-col gap-3 border-t border-line pt-5 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-center gap-2 text-[13px] text-ink-2">
                <span className="shrink-0">Deposit ID</span>
                <CopyId value={topup.deposit_id} label="Copy deposit ID" />
              </div>
              <button type="button" className="btn btn-secondary" onClick={onBack}>
                Back to the top-up form
              </button>
            </div>
            <p className="mt-2 text-[12px] text-ink-2 sm:text-right">Leaving does not cancel it. It shows up in your history.</p>
          </motion.div>
        ) : phase === 'completed' ? (
          <motion.div key="completed" {...motionProps}>
            <ReceiptCard topup={topup} networkName={network} settledBy={settle?.source ?? null} prefix={prefix} />
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <button type="button" className="btn btn-primary" onClick={() => onRetry(draftFrom(topup, prefix))}>
                Top up again
              </button>
              <button type="button" className="btn btn-secondary" onClick={onBack}>
                Back to the top-up form
              </button>
            </div>
            <section className="mt-8 border-t border-line pt-6" aria-labelledby="trace-done-h">
              <h3 id="trace-done-h" className="mb-4 text-[14px] text-ink">
                How it settled
              </h3>
              {trace}
            </section>
          </motion.div>
        ) : (
          <motion.div key={phase} {...motionProps}>
            <div className="flex items-start gap-4">
              {phase === 'held' ? (
                <LordIcon name="clock" trigger="in" state="in-clock" size={48} color="#A85A07" />
              ) : (
                <LordIcon name="error" trigger="in" state="in-error" size={48} color="#B42318" />
              )}
              <div className="min-w-0">
                <p className={`text-[13px] font-medium ${phase === 'held' ? 'text-warning' : 'text-danger'}`}>
                  {phase === 'held' ? 'Needs attention' : topup.status === 'REJECTED' ? 'Rejected by pawaPay' : 'Top-up failed'}
                </p>
                <h2 className="mt-0.5 text-[20px] leading-tight text-ink">
                  {phase === 'held' ? 'This payment is held for review' : copy.title}
                </h2>
              </div>
            </div>
            {phase === 'held' ? (
              <p className="mt-4 text-[14px] text-ink-2">
                pawaPay reported a result that does not match this top-up, so nothing was credited automatically. It is flagged
                as Needs attention in the operations console. Your balance has not changed.
              </p>
            ) : (
              <>
                <p className="mt-4 text-[14px] text-ink-2">
                  {copy.body} Nothing was charged and your balance has not changed.
                </p>
                <p className="mt-2 text-[14px] font-medium text-ink">{copy.next}</p>
              </>
            )}
            {topup.failure_code ? (
              <p className="mt-3 text-[13px] text-ink-2">
                pawaPay code <span className="mono text-ink">{topup.failure_code}</span>
              </p>
            ) : null}
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              {phase === 'failed' ? (
                <button type="button" className="btn btn-primary" onClick={() => onRetry(draftFrom(topup, prefix))}>
                  <ArrowCounterClockwiseIcon size={16} weight="bold" />
                  Try again
                </button>
              ) : null}
              <button type="button" className="btn btn-secondary" onClick={onBack}>
                Back to the top-up form
              </button>
            </div>
            <section className="mt-8 border-t border-line pt-6" aria-labelledby="trace-fail-h">
              <h3 id="trace-fail-h" className="mb-4 text-[14px] text-ink">
                Where it stopped
              </h3>
              {trace}
            </section>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
