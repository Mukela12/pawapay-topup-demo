import type { ReactNode } from 'react'
import type { Topup } from '../lib/api'
import { formatDateTime, formatDuration, formatMsisdn, formatTime, msBetween, topupAmount } from '../lib/format'
import { CopyId } from './CopyId'
import { LordIcon } from './LordIcon'

const SETTLED_BY: Record<string, string> = {
  callback: 'pawaPay callback',
  status_check: 'Status check',
  reconcile: 'Reconciliation job',
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-[13px] text-ink-2">{label}</dt>
      <dd className="min-w-0 text-right text-[13px] text-ink">{children}</dd>
    </div>
  )
}

/** Completed top-ups render as a receipt: perforated top edge, depositId in mono. */
export function ReceiptCard({
  topup,
  networkName,
  settledBy,
  prefix,
}: {
  topup: Topup
  networkName: string
  settledBy: string | null
  prefix?: string
}) {
  const ms = msBetween(topup.created_at, topup.finalized_at)
  return (
    <div className="receipt-shadow">
      <div className="receipt px-5 pb-5 pt-8 sm:px-7">
        <div className="flex items-center gap-3">
          <LordIcon name="check" trigger="in" state="in-reveal" size={44} color="#0F7B4F" />
          <div className="min-w-0">
            <p className="text-[13px] font-medium text-success">Top-up complete</p>
            <p className="text-[30px] font-semibold leading-tight tracking-[-0.02em] text-ink">{topupAmount(topup)}</p>
          </div>
        </div>
        <p className="mt-2 text-[14px] text-ink-2">Added to your calling credit. The ledger holds exactly one credit for this payment.</p>

        <dl className="mt-5 divide-y divide-dashed divide-line-strong border-y border-dashed border-line-strong">
          <Row label="Network">{networkName}</Row>
          <Row label="Phone">
            <span className="mono">{formatMsisdn(topup.phone, prefix)}</span>
          </Row>
          <Row label="Deposit ID">
            <CopyId value={topup.deposit_id} label="Copy deposit ID" />
          </Row>
          {topup.provider_txn_id ? (
            <Row label="Network reference">
              <span className="mono break-all">{topup.provider_txn_id}</span>
            </Row>
          ) : null}
          <Row label="Completed">
            <span className="mono">{formatTime(topup.finalized_at)}</span>
            <span className="ml-2 text-ink-2">{formatDateTime(topup.finalized_at).split(',')[0]}</span>
          </Row>
          {ms !== null ? <Row label="Time to complete">{formatDuration(ms / 1000)}</Row> : null}
          {settledBy ? <Row label="Confirmed by">{SETTLED_BY[settledBy] ?? settledBy}</Row> : null}
        </dl>
      </div>
    </div>
  )
}
