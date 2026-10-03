import { ArrowLeftIcon, SquaresFourIcon } from '@phosphor-icons/react'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { BrandMark } from '../../components/BrandMark'
import { EmptyState } from '../../components/EmptyState'
import { ErrorNotice } from '../../components/ErrorNotice'
import { StatusPill } from '../../components/StatusPill'
import type { AppConfig, Topup, Wallet } from '../../lib/api'
import { failureCopy } from '../../lib/failure-copy'
import { formatMinor, formatMsisdn, providerName, relativeTime, topupAmount } from '../../lib/format'
import { useNow } from '../../lib/hooks'
import { useConfig, useScenarios, useSession, useWallet } from '../../lib/queries'
import { TopupForm, type TopupDraft } from './TopupForm'
import { TopupProgress } from './TopupProgress'

export function WalletPage() {
  const session = useSession()
  const ready = session.isSuccess
  const config = useConfig(ready)
  const scenarios = useScenarios(ready)
  const wallet = useWallet(ready)
  const [params, setParams] = useSearchParams()
  const openId = params.get('topup')
  const [draft, setDraft] = useState<{ value: TopupDraft | null; n: number }>({ value: null, n: 0 })

  function showTopup(t: Topup) {
    setParams({ topup: t.id })
    window.scrollTo({ top: 0, behavior: 'auto' })
  }
  function backToForm(next: TopupDraft | null = null) {
    setDraft((d) => ({ value: next, n: d.n + 1 }))
    setParams({})
  }

  const loadError = session.error ?? config.error

  return (
    <div className="min-h-dvh pb-16">
      <header className="sticky top-0 z-20 border-b border-line bg-base/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center justify-between gap-3 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-1">
            <Link to="/" className="btn btn-ghost btn-sm -ml-3 size-11 px-0" aria-label="Back to the overview">
              <ArrowLeftIcon size={16} />
            </Link>
            <BrandMark sub="Calling credit" />
          </div>
          <div className="flex items-center gap-2">
            {config.data?.sandbox !== false ? <span className="pill pill-accent hidden sm:inline-flex">pawaPay sandbox</span> : null}
            <Link to="/console" className="btn btn-ghost btn-sm">
              <SquaresFourIcon size={16} />
              <span className="hidden sm:inline">Operations console</span>
              <span className="sm:hidden">Console</span>
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1120px] px-4 pt-6 sm:px-6 sm:pt-10">
        {loadError ? (
          <div className="mb-6">
            <ErrorNotice
              error={loadError}
              title={session.error ? 'Could not start a wallet session' : 'Could not load the top-up options'}
              onRetry={() => {
                if (session.error) void session.refetch()
                else void config.refetch()
              }}
            />
          </div>
        ) : null}

        <div className="grid gap-6 [grid-template-areas:'balance'_'panel'_'history'] lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-[auto_1fr] lg:gap-x-10 lg:[grid-template-areas:'panel_balance'_'panel_history']">
          <section className="[grid-area:balance]" aria-labelledby="balance-h">
            <Balances wallet={wallet.data} config={config.data} loading={wallet.isPending} />
          </section>

          {/* A completed top-up's receipt is its own paper object, so the panel drops its card chrome around it. */}
          <section
            className="card p-5 [grid-area:panel] has-[.receipt]:border-transparent has-[.receipt]:bg-transparent has-[.receipt]:p-0 sm:p-7 sm:has-[.receipt]:p-0"
            aria-label={openId ? 'Top-up progress' : 'New top-up'}
          >
            {!config.data ? (
              <PanelSkeleton />
            ) : openId ? (
              <TopupProgress
                key={openId}
                topupId={openId}
                config={config.data}
                ready={ready}
                onBack={() => backToForm(null)}
                onRetry={(d) => backToForm(d)}
              />
            ) : (
              <TopupForm
                key={draft.n}
                config={config.data}
                scenarios={scenarios.data ?? []}
                draft={draft.value}
                onCreated={showTopup}
              />
            )}
          </section>

          <section className="[grid-area:history]" aria-labelledby="history-h">
            <History
              topups={wallet.data?.topups ?? []}
              loading={wallet.isPending}
              config={config.data}
              activeId={openId}
              onOpen={showTopup}
            />
          </section>
        </div>
      </main>
    </div>
  )
}

function PanelSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading">
      <div className="h-5 w-48 rounded-pill bg-inset" />
      <div className="h-4 w-80 max-w-full rounded-pill bg-inset" />
      <div className="h-11 w-full rounded-control bg-inset" />
      <div className="h-11 w-full rounded-control bg-inset" />
      <div className="h-11 w-2/3 rounded-control bg-inset" />
    </div>
  )
}

function Balances({ wallet, config, loading }: { wallet?: Wallet; config?: AppConfig; loading: boolean }) {
  const balances = wallet?.balances ?? []
  const fallbackCurrency = config?.countries[0]?.currency ?? 'ZMW'
  const primary = balances[0]
  return (
    <div className="lg:pt-1">
      <h2 id="balance-h" className="text-[13px] font-medium tracking-normal text-ink-2">
        Calling credit balance
      </h2>
      {loading ? (
        <div className="mt-2 h-9 w-44 rounded-pill bg-inset" />
      ) : (
        <p className="mt-1 text-[34px] font-semibold leading-tight tracking-[-0.02em] text-ink">
          {primary ? formatMinor(primary.balance_minor, primary.currency, primary.exponent) : formatMinor(0, fallbackCurrency)}
        </p>
      )}
      {balances.length > 1 ? (
        <ul className="mt-2 space-y-1">
          {balances.slice(1).map((b) => (
            <li key={b.currency} className="mono text-[14px] text-ink-2">
              {formatMinor(b.balance_minor, b.currency, b.exponent)}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 text-[13px] text-ink-2">Credited from the ledger only after pawaPay confirms the payment.</p>
    </div>
  )
}

function History({
  topups,
  loading,
  config,
  activeId,
  onOpen,
}: {
  topups: Topup[]
  loading: boolean
  config?: AppConfig
  activeId: string | null
  onOpen: (t: Topup) => void
}) {
  const now = useNow(30_000)
  return (
    <div className="lg:border-t lg:border-line lg:pt-6">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 id="history-h" className="text-[15px] text-ink">
          History
        </h2>
        {topups.length > 0 ? <span className="text-[12px] text-ink-2">Last {Math.min(topups.length, 20)}</span> : null}
      </div>
      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 rounded-control bg-inset" />
          ))}
        </div>
      ) : topups.length === 0 ? (
        <EmptyState title="No top-ups yet">Each top-up you start lands here with its status, including failed ones.</EmptyState>
      ) : (
        <ul className="divide-y divide-line overflow-hidden rounded-card border border-line bg-surface">
          {topups.map((t) => {
            const prefix = config?.countries.find((c) => c.country === t.country)?.prefix
            const failed = t.status === 'FAILED' || t.status === 'REJECTED'
            return (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => onOpen(t)}
                  aria-current={activeId === t.id ? 'true' : undefined}
                  className={`table-row-link flex min-h-[60px] w-full items-center gap-3 px-4 py-3 text-left ${
                    activeId === t.id ? 'bg-inset' : ''
                  }`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="mono block text-[14px] font-medium text-ink">{topupAmount(t)}</span>
                    <span className="block truncate text-[12px] text-ink-2">
                      {failed
                        ? failureCopy(t.failure_code).short
                        : `${providerName(config?.countries, t.country, t.provider)}, ${formatMsisdn(t.phone, prefix)}`}
                    </span>
                  </span>
                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <StatusPill status={t.status} />
                    <span className="text-[12px] text-ink-2">{relativeTime(t.created_at, now)}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
