// Derives the payment trace from a top-up and its stored events. Pure, so the
// customer waiting screen and the console detail page light the same way.

import type { EventSource, PaymentEventSummary, Topup } from './api'
import { failureCopy } from './failure-copy'
import { formatMinor } from './format'

export type StationId = 'app' | 'api' | 'pawapay' | 'phone' | 'callback' | 'ledger'

/**
 * idle: not reached yet. active: the current hop, pulsing. lit: passed.
 * done: the ledger credit. held: parked for review. failed: where it broke.
 * muted: after a break, not reached.
 */
export type StationState = 'idle' | 'active' | 'lit' | 'done' | 'held' | 'failed' | 'muted'
export type LinkState = 'idle' | 'lit' | 'broken'

export interface TraceStation {
  id: StationId
  label: string
  state: StationState
  at: string | null
  caption: string | null
}

export interface Trace {
  stations: TraceStation[]
  /** links[i] joins stations[i] and stations[i + 1] */
  links: LinkState[]
  startedAt: string
  settledBy: EventSource | null
}

const SETTLE_SOURCES: EventSource[] = ['callback', 'status_check', 'reconcile']

/** The pawaPay status an event carried, if the API exposed it. */
export function eventStatus(e: PaymentEventSummary & { payload?: unknown }): string | null {
  if (e.status) return e.status.toUpperCase()
  if (e.pawapay_status) return e.pawapay_status.toUpperCase()
  const p = e.payload as Record<string, unknown> | null | undefined
  if (p && typeof p === 'object') {
    const direct = p.status
    const nested = (p.data as Record<string, unknown> | undefined)?.status
    const s = typeof nested === 'string' ? nested : typeof direct === 'string' ? direct : null
    if (s && s !== 'FOUND') return s.toUpperCase()
  }
  return null
}

function byTime(a: PaymentEventSummary, b: PaymentEventSummary): number {
  return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
}

/** The event that moved the top-up to its final state. */
export function settlingEvent(topup: Topup, events: PaymentEventSummary[]): PaymentEventSummary | null {
  const final = topup.status === 'COMPLETED' || topup.status === 'FAILED' || topup.status === 'NEEDS_ATTENTION'
  if (!final) return null
  const sorted = [...events].sort(byTime).reverse()
  const wanted = topup.status === 'NEEDS_ATTENTION' ? null : topup.status
  const decisive = sorted.filter(
    (e) => SETTLE_SOURCES.includes(e.source) && (e.outcome === 'applied' || e.outcome === 'held_for_review'),
  )
  return (
    decisive.find((e) => (wanted ? eventStatus(e) === wanted : true)) ??
    decisive[0] ??
    // Fallback: the API did not label outcomes, use the latest settle-type event.
    sorted.find((e) => SETTLE_SOURCES.includes(e.source)) ??
    null
  )
}

const SOURCE_PHRASE: Record<string, string> = {
  callback: 'callback',
  status_check: 'status check',
  reconcile: 'reconciliation',
}

export function deriveTrace(
  topup: Topup,
  events: PaymentEventSummary[],
  opts: { reconcileAfterSeconds?: number; now?: number } = {},
): Trace {
  const sorted = [...events].sort(byTime)
  const initiate = sorted.find((e) => e.source === 'initiate_response') ?? null
  const settle = settlingEvent(topup, sorted)
  const status = topup.status
  const now = opts.now ?? Date.now()

  const stations: TraceStation[] = [
    { id: 'app', label: 'React app', state: 'lit', at: topup.created_at, caption: 'Top-up requested' },
    { id: 'api', label: 'Flask API', state: 'idle', at: null, caption: null },
    { id: 'pawapay', label: 'pawaPay', state: 'idle', at: null, caption: null },
    { id: 'phone', label: 'Customer phone', state: 'idle', at: null, caption: null },
    { id: 'callback', label: 'Callback', state: 'idle', at: null, caption: null },
    { id: 'ledger', label: 'Ledger', state: 'idle', at: null, caption: null },
  ]
  const [, api, pawapay, phone, callback, ledger] = stations

  // Flask API: the row and its depositId are committed before pawaPay is called.
  if (status === 'CREATED' && !initiate) {
    api.state = 'active'
    api.at = topup.created_at
    api.caption = 'Calling pawaPay'
  } else {
    api.state = 'lit'
    api.at = topup.created_at
    api.caption = 'depositId stored'
  }

  const pastInitiation = status !== 'CREATED'
  const initiateAt = initiate?.created_at ?? topup.accepted_at ?? null

  // pawaPay
  if (status === 'REJECTED') {
    pawapay.state = 'failed'
    pawapay.at = initiateAt ?? topup.finalized_at
    pawapay.caption = `Rejected: ${failureCopy(topup.failure_code).short.toLowerCase()}`
  } else if (status === 'FAILED' && failureCopy(topup.failure_code).hop === 'pawapay') {
    pawapay.state = 'failed'
    pawapay.at = topup.finalized_at ?? initiateAt
    pawapay.caption = failureCopy(topup.failure_code).short
  } else if (status === 'CREATED' && initiate) {
    // Initiation came back with an unknown outcome (HTTP 500 or a timeout).
    // The API checks the deposit status next; only NOT_FOUND means failed.
    pawapay.state = 'active'
    pawapay.at = initiate.created_at
    pawapay.caption = 'Outcome unknown, checking'
  } else if (pastInitiation) {
    pawapay.state = 'lit'
    pawapay.at = initiateAt
    pawapay.caption = 'Accepted'
  }

  // Customer phone
  if (status === 'ACCEPTED' || status === 'PROCESSING' || status === 'IN_RECONCILIATION') {
    phone.state = 'active'
    phone.at = topup.accepted_at ?? initiateAt
    phone.caption =
      status === 'ACCEPTED'
        ? 'Waiting for PIN approval'
        : status === 'PROCESSING'
          ? 'Processing at the network'
          : 'pawaPay is reconciling'
  } else if (status === 'COMPLETED' || status === 'NEEDS_ATTENTION') {
    // pawaPay never reports when the customer approved, so this station shows no
    // time. accepted_at is when pawaPay took the request, before any PIN step.
    phone.state = 'lit'
    phone.at = null
    phone.caption = status === 'COMPLETED' ? 'Approved' : null
  } else if (status === 'FAILED' && pawapay.state !== 'failed') {
    phone.state = 'failed'
    phone.at = settle?.created_at ?? topup.finalized_at
    phone.caption = failureCopy(topup.failure_code).short
  }

  // Callback (or whatever delivered the final status)
  const settlePhrase = settle ? SOURCE_PHRASE[settle.source] : null
  if (status === 'COMPLETED' || status === 'NEEDS_ATTENTION') {
    callback.state = 'lit'
    callback.at = settle?.created_at ?? topup.finalized_at
    if (settle?.source === 'reconcile') {
      callback.label = 'Settled by reconciliation'
      callback.caption = 'No callback needed'
    } else if (settle?.source === 'status_check') {
      callback.label = 'Settled by status check'
      callback.caption = 'Asked pawaPay directly'
    } else {
      callback.caption =
        settle?.signature === 'verified'
          ? 'Signature verified'
          : settle
            ? 'Confirmed with pawaPay'
            : 'Final status received'
    }
  } else if (status === 'FAILED' || status === 'REJECTED') {
    callback.state = 'muted'
    if (status === 'FAILED' && settlePhrase) {
      callback.at = settle?.created_at ?? null
      callback.caption = `Failure reported by ${settlePhrase}`
    } else {
      callback.caption = status === 'REJECTED' ? 'No callback for rejections' : null
    }
  } else if (phone.state === 'active') {
    const openFor = (now - new Date(topup.created_at).getTime()) / 1000
    const threshold = opts.reconcileAfterSeconds ?? 900
    callback.caption =
      openFor > threshold ? 'No callback yet, reconciliation is checking' : 'Waiting for pawaPay'
  }

  // Ledger
  if (status === 'COMPLETED') {
    ledger.state = 'done'
    ledger.at = topup.finalized_at ?? settle?.created_at ?? null
    ledger.caption = `${formatMinor(topup.amount_minor, topup.currency, topup.exponent)} credited`
  } else if (status === 'NEEDS_ATTENTION') {
    ledger.state = 'held'
    ledger.at = topup.finalized_at ?? settle?.created_at ?? null
    ledger.caption = 'Held for review'
  } else if (status === 'FAILED' || status === 'REJECTED') {
    ledger.state = 'muted'
    ledger.caption = 'Nothing credited'
  }

  // Everything after a failed station is muted.
  const failedAt = stations.findIndex((s) => s.state === 'failed')
  if (failedAt >= 0) {
    for (let i = failedAt + 1; i < stations.length; i++) {
      if (stations[i].state !== 'muted') {
        stations[i].state = 'muted'
      }
    }
  }

  const reached: StationState[] = ['active', 'lit', 'done', 'held', 'failed']
  const links: LinkState[] = []
  for (let i = 0; i < stations.length - 1; i++) {
    if (stations[i].state === 'failed') links.push('broken')
    else if (reached.includes(stations[i + 1].state)) links.push('lit')
    else links.push('idle')
  }

  return { stations, links, startedAt: topup.created_at, settledBy: settle?.source ?? null }
}
