import { ArrowsClockwiseIcon, CircleNotchIcon } from '@phosphor-icons/react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { EmptyState } from '../../components/EmptyState'
import { ErrorNotice, errorMessage } from '../../components/ErrorNotice'
import { api, type ReconcileRun } from '../../lib/api'
import { formatDateTime, formatDuration, formatTime, msBetween, plural } from '../../lib/format'
import { keys, useConfig, useReconcileRuns, useSession } from '../../lib/queries'

const TRIGGER_LABEL: Record<string, string> = {
  schedule: 'Scheduler',
  manual: 'Manual',
  eb_cron: 'EB worker cron',
}

function summary(run: ReconcileRun): string {
  if (run.checked === 0) return 'Nothing was open past the threshold.'
  const parts = [`Checked ${plural(run.checked, 'top-up')}`]
  parts.push(`${run.settled} settled`)
  if (run.still_open) parts.push(`${run.still_open} still open`)
  if (run.failed_not_found) parts.push(`${run.failed_not_found} not found at pawaPay`)
  if (run.errors) parts.push(plural(run.errors, 'error'))
  return `${parts.join(', ')}.`
}

function Num({ value, tone }: { value: number; tone?: string }) {
  return <span className={`mono ${value > 0 && tone ? tone : value === 0 ? 'text-ink-2' : 'text-ink font-medium'}`}>{value}</span>
}

export function ReconcilePage() {
  const qc = useQueryClient()
  const runs = useReconcileRuns()
  const session = useSession()
  const config = useConfig(session.isSuccess)
  const threshold = config.data?.reconcile_after_seconds
  const [last, setLast] = useState<ReconcileRun | null>(null)

  const runNow = useMutation({
    mutationFn: api.runReconcile,
    onSuccess: (run) => {
      setLast(run)
      toast(`Reconciliation run finished. ${summary(run)}`)
    },
    onError: (err) => toast.error(`Reconciliation run failed: ${errorMessage(err)}`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: keys.runs })
      void qc.invalidateQueries({ queryKey: keys.stats })
      void qc.invalidateQueries({ queryKey: ['console', 'topups'] })
    },
  })

  const list = runs.data ?? []

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-[72ch]">
          <h1 className="text-[24px] leading-tight text-ink">Reconciliation</h1>
          <p className="mt-2 text-[14px] leading-relaxed text-ink-2">
            pawaPay retries a callback for 15 minutes, and callbacks still go missing: a deploy at the wrong moment, a load
            balancer timeout, a bug in a handler. Every 60 seconds a scheduled job claims the top-ups still open after{' '}
            {threshold ? plural(threshold, 'second') : 'the threshold'}, asks pawaPay about each one with{' '}
            <code className="mono text-[13px] text-ink">GET /v2/deposits/&#123;depositId&#125;</code> outside any database
            transaction, and applies the answer through the same guarded transition a callback uses. NOT_FOUND fails the
            top-up, PROCESSING and IN_RECONCILIATION stay open, and a Postgres advisory lock means only one worker runs it at a
            time. On Elastic Beanstalk the same job is triggered by the worker tier’s cron.yaml.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-primary shrink-0"
          onClick={() => runNow.mutate()}
          disabled={runNow.isPending}
        >
          {runNow.isPending ? (
            <CircleNotchIcon size={16} weight="bold" className="animate-spin motion-reduce:animate-none" />
          ) : (
            <ArrowsClockwiseIcon size={16} weight="bold" />
          )}
          Run now
        </button>
      </div>

      {last ? (
        <div className="mt-5 rounded-card border border-line bg-surface px-4 py-3" aria-live="polite">
          <p className="text-[14px] font-semibold text-ink">Manual run at {formatTime(last.started_at || new Date().toISOString())}</p>
          <p className="mt-0.5 text-[13px] text-ink-2">{summary(last)}</p>
        </div>
      ) : null}
      {runNow.isError ? (
        <div className="mt-5">
          <ErrorNotice error={runNow.error} title="The run did not complete" />
        </div>
      ) : null}

      <section className="mt-8" aria-labelledby="runs-h">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 id="runs-h" className="text-[15px] text-ink">
            Recent runs
          </h2>
          <span className="text-[12px] text-ink-2">Last 20</span>
        </div>
        {runs.isError ? (
          <ErrorNotice error={runs.error} title="Could not load runs" onRetry={() => void runs.refetch()} />
        ) : runs.isPending ? (
          <div className="card h-40 bg-inset/40" />
        ) : list.length === 0 ? (
          <EmptyState title="No runs recorded yet">
            The scheduler writes a row here every minute once the API is running. Run now starts one immediately.
          </EmptyState>
        ) : (
          <>
            <div className="card hidden overflow-x-auto lg:block">
              <table className="w-full text-left text-[13px]">
                <thead className="border-b border-line bg-inset/60 text-[12px] text-ink-2">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Started</th>
                    <th className="px-4 py-2.5 font-medium">Trigger</th>
                    <th className="px-4 py-2.5 text-right font-medium">Checked</th>
                    <th className="px-4 py-2.5 text-right font-medium">Settled</th>
                    <th className="px-4 py-2.5 text-right font-medium">Still open</th>
                    <th className="px-4 py-2.5 text-right font-medium">Not found</th>
                    <th className="px-4 py-2.5 text-right font-medium">Errors</th>
                    <th className="px-4 py-2.5 text-right font-medium">Took</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {list.map((r) => {
                    const ms = msBetween(r.started_at, r.finished_at)
                    return (
                      <tr key={r.id}>
                        <td className="whitespace-nowrap px-4 py-3 text-ink-2">{formatDateTime(r.started_at)}</td>
                        <td className="px-4 py-3 text-ink">{TRIGGER_LABEL[r.trigger] ?? r.trigger}</td>
                        <td className="px-4 py-3 text-right">
                          <Num value={r.checked} />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Num value={r.settled} tone="text-success" />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Num value={r.still_open} />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Num value={r.failed_not_found} tone="text-danger" />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Num value={r.errors} tone="text-danger" />
                        </td>
                        <td className="mono px-4 py-3 text-right text-ink-2">{ms === null ? 'running' : formatDuration(ms / 1000)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <ul className="card divide-y divide-line overflow-hidden lg:hidden">
              {list.map((r) => (
                <li key={r.id} className="px-4 py-3">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="text-[14px] font-medium text-ink">{TRIGGER_LABEL[r.trigger] ?? r.trigger}</p>
                    <p className="text-[12px] text-ink-2">{formatDateTime(r.started_at)}</p>
                  </div>
                  <p className="mt-0.5 text-[13px] text-ink-2">{summary(r)}</p>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  )
}
