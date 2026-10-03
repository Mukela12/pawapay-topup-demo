import type { EventOutcome, SignatureState, TopupStatus } from '../lib/api'

const STATUS: Record<TopupStatus, { label: string; tone: 'neutral' | 'success' | 'danger' | 'warning'; live?: boolean }> = {
  CREATED: { label: 'Created', tone: 'neutral', live: true },
  ACCEPTED: { label: 'Waiting for PIN', tone: 'neutral', live: true },
  PROCESSING: { label: 'Processing', tone: 'neutral', live: true },
  IN_RECONCILIATION: { label: 'In reconciliation', tone: 'neutral', live: true },
  COMPLETED: { label: 'Completed', tone: 'success' },
  FAILED: { label: 'Failed', tone: 'danger' },
  REJECTED: { label: 'Rejected', tone: 'danger' },
  NEEDS_ATTENTION: { label: 'Needs attention', tone: 'warning' },
}

export function statusLabel(status: string): string {
  return STATUS[status as TopupStatus]?.label ?? status
}

/** The one status pill, used identically in the wallet and the console. */
export function StatusPill({ status }: { status: TopupStatus | string }) {
  const s = STATUS[status as TopupStatus] ?? { label: status, tone: 'neutral' as const }
  return (
    <span className={`pill pill-${s.tone}`}>
      {s.live ? <span className="live-dot" aria-hidden="true" /> : null}
      <span className="min-w-0 truncate">{s.label}</span>
    </span>
  )
}

const OUTCOME: Record<EventOutcome, { label: string; tone: string }> = {
  applied: { label: 'Applied', tone: 'accent' },
  duplicate_ignored: { label: 'Duplicate ignored', tone: 'neutral' },
  rejected: { label: 'Rejected', tone: 'danger' },
  held_for_review: { label: 'Held for review', tone: 'warning' },
  no_change: { label: 'No change', tone: 'neutral' },
  error: { label: 'Error', tone: 'danger' },
}

export function OutcomePill({ outcome }: { outcome: EventOutcome | string }) {
  const o = OUTCOME[outcome as EventOutcome] ?? { label: outcome.replace(/_/g, ' '), tone: 'neutral' }
  return (
    <span className={`pill pill-${o.tone}`}>
      <span className="min-w-0 truncate">{o.label}</span>
    </span>
  )
}

// not_checked is stored for API responses, status checks and reconciliation:
// pawaPay signs callbacks only, so those events never carry a signature.
const SIGNATURE: Record<SignatureState, { label: string; tone: string }> = {
  verified: { label: 'Signature verified', tone: 'success' },
  invalid: { label: 'Signature invalid', tone: 'danger' },
  missing: { label: 'Unsigned callback', tone: 'warning' },
  not_checked: { label: 'No signature expected', tone: 'neutral' },
}

export function SignatureBadge({ signature }: { signature: SignatureState | string }) {
  const s = SIGNATURE[signature as SignatureState] ?? { label: signature, tone: 'neutral' }
  return (
    <span className={`pill pill-${s.tone}`}>
      <span className="min-w-0 truncate">{s.label}</span>
    </span>
  )
}
