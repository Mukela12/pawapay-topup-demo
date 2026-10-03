import {
  AppWindowIcon,
  DeviceMobileIcon,
  HardDrivesIcon,
  NotebookIcon,
  PlugsConnectedIcon,
  WebhooksLogoIcon,
  XIcon,
  type Icon,
} from '@phosphor-icons/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { isOpenStatus, type PaymentEventSummary, type Topup } from '../lib/api'
import { formatDelta, formatTime, msBetween } from '../lib/format'
import { useNow } from '../lib/hooks'
import { deriveTrace, type StationId, type StationState, type Trace } from '../lib/trace'
import './PaymentTrace.css'

const ICONS: Record<StationId, Icon> = {
  app: AppWindowIcon,
  api: HardDrivesIcon,
  pawapay: PlugsConnectedIcon,
  phone: DeviceMobileIcon,
  callback: WebhooksLogoIcon,
  ledger: NotebookIcon,
}

const SR_STATE: Record<StationState, string> = {
  idle: 'not reached',
  active: 'in progress',
  lit: 'passed',
  done: 'credited',
  held: 'held for review',
  failed: 'failed here',
  muted: 'not reached',
}

/** Time between one newly lit station and the next. */
const STEP_MS = 90
/** The link fills first, then the node it leads to settles. */
const NODE_LAG_MS = 110

const REACHED: StationState[] = ['active', 'lit', 'done', 'held', 'failed']

function blank(trace: Trace): Trace {
  return {
    ...trace,
    stations: trace.stations.map((s) => ({ ...s, state: 'idle', caption: null, at: null })),
    links: trace.links.map(() => 'idle'),
  }
}

interface PaymentTraceProps {
  topup: Topup
  events: PaymentEventSummary[]
  reconcileAfterSeconds?: number
  /** Play the lighting sequence from a blank rail on mount. Off when the viewer just watched it live. */
  playOnMount?: boolean
  className?: string
}

export function PaymentTrace({ topup, events, reconcileAfterSeconds, playOnMount = true, className }: PaymentTraceProps) {
  const open = isOpenStatus(topup.status)
  const now = useNow(open ? 1000 : null)
  const trace = useMemo(
    () => deriveTrace(topup, events, { reconcileAfterSeconds, now }),
    [topup, events, reconcileAfterSeconds, now],
  )

  // First paint shows a blank rail, the next frame lights it, so the sequence
  // plays once on arrival as well as live while polling.
  const [mounted, setMounted] = useState(!playOnMount)
  useEffect(() => {
    if (mounted) return
    let inner = 0
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setMounted(true))
    })
    return () => {
      cancelAnimationFrame(outer)
      cancelAnimationFrame(inner)
    }
  }, [mounted])
  const shown = mounted ? trace : blank(trace)

  // Stagger only the stations that became reached since the last commit.
  const signature = shown.stations.map((s) => s.state).join('|')
  const committed = useRef<StationState[] | null>(null)
  const delays = useMemo(() => {
    const prev = committed.current
    let order = 0
    return shown.stations.map((s, i) => {
      const was = prev?.[i] ?? 'idle'
      const isReached = REACHED.includes(s.state)
      const wasReached = REACHED.includes(was)
      return isReached && !wasReached ? order++ * STEP_MS : 0
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature])
  useEffect(() => {
    committed.current = signature.split('|') as StationState[]
  }, [signature])

  return (
    <div className={`trace ${className ?? ''}`}>
      <ol className="trace-rail" aria-label="Payment trace">
        {shown.stations.map((station, i) => {
          const IconCmp = ICONS[station.id]
          const delta = i === 0 ? null : msBetween(shown.startedAt, station.at)
          const link = i < shown.links.length ? shown.links[i] : null
          const nextDelay = i < shown.stations.length - 1 ? delays[i + 1] : 0
          const visibleTime = station.state !== 'idle' && station.at
          return (
            <li key={station.id} className="trace-station" data-state={station.state}>
              <div className="trace-head">
                <span
                  className="trace-node"
                  style={{ transitionDelay: `${delays[i] ? delays[i] + NODE_LAG_MS : 0}ms` }}
                  aria-hidden="true"
                >
                  <IconCmp size={16} weight={station.state === 'idle' || station.state === 'muted' ? 'regular' : 'bold'} />
                </span>
                {link ? (
                  <span className="trace-link" data-state={link} aria-hidden="true">
                    <span className="trace-link-fill" style={{ transitionDelay: `${nextDelay}ms` }} />
                    {link === 'broken' ? (
                      <span className="trace-break">
                        <XIcon size={11} weight="bold" />
                      </span>
                    ) : null}
                  </span>
                ) : null}
              </div>
              <div className="trace-text">
                <p className="trace-label">
                  {station.label}
                  <span className="sr-only">, {SR_STATE[station.state]}</span>
                </p>
                {visibleTime ? (
                  <p className="trace-time">
                    <time dateTime={station.at ?? undefined}>{formatTime(station.at)}</time>
                    {delta !== null && delta >= 0 ? <span className="trace-delta">{formatDelta(delta)}</span> : null}
                  </p>
                ) : station.state !== 'idle' && station.caption ? (
                  // No time for this hop (pawaPay never reports when the PIN was approved). An empty
                  // line keeps its caption level with the others on the horizontal rail.
                  <p className="trace-time trace-time-empty" aria-hidden="true">
                    {'\u00a0'}
                  </p>
                ) : null}
                {station.caption ? <p className="trace-caption">{station.caption}</p> : null}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
