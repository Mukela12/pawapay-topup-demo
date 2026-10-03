import { ArrowsClockwiseIcon, HouseSimpleIcon, PulseIcon, ReceiptIcon, WalletIcon, type Icon } from '@phosphor-icons/react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { BrandMark } from '../../components/BrandMark'
import { useHealth } from '../../lib/queries'

const NAV: { to: string; label: string; icon: Icon; end?: boolean }[] = [
  { to: '/console', label: 'Top-ups', icon: ReceiptIcon },
  { to: '/console/reconciliation', label: 'Reconciliation', icon: ArrowsClockwiseIcon },
  { to: '/console/events', label: 'Event log', icon: PulseIcon },
]

function useActive() {
  const { pathname } = useLocation()
  return (to: string) =>
    to === '/console' ? pathname === '/console' || pathname.startsWith('/console/topups') : pathname.startsWith(to)
}

function HealthBlock() {
  const health = useHealth()
  const h = health.data
  const rows: { label: string; value: string; ok: boolean | null }[] = h
    ? [
        { label: 'pawaPay API', value: h.pawapay_configured ? 'Sandbox token set' : 'Token not set', ok: h.pawapay_configured },
        { label: 'Database', value: h.db === true || h.db === 'ok' ? 'Connected' : String(h.db), ok: h.db === true || h.db === 'ok' },
        { label: 'Signed callbacks', value: h.signature_required ? 'Required' : 'Optional, unsigned confirmed by status check', ok: null },
      ]
    : []
  return (
    <div className="rounded-control border border-sidebar-line px-3 py-3">
      <p className="text-[12px] font-medium text-sidebar-ink">Server</p>
      {health.isError ? (
        <p className="mt-1 text-[12px] text-sidebar-text">Health check unavailable</p>
      ) : (
        <dl className="mt-2 space-y-2">
          {rows.map((r) => (
            <div key={r.label}>
              <dt className="text-[11px] text-sidebar-text">{r.label}</dt>
              <dd className="flex items-center gap-1.5 text-[12px] text-sidebar-ink">
                {r.ok !== null ? (
                  <span
                    aria-hidden="true"
                    className={`size-1.5 shrink-0 rounded-full ${r.ok ? 'bg-sidebar-ok' : 'bg-sidebar-warn'}`}
                  />
                ) : null}
                {r.value}
              </dd>
            </div>
          ))}
          {h?.commit ? (
            <div>
              <dt className="text-[11px] text-sidebar-text">Commit</dt>
              <dd className="mono text-[12px] text-sidebar-ink">{h.commit.slice(0, 7)}</dd>
            </div>
          ) : null}
        </dl>
      )}
    </div>
  )
}

export function ConsoleLayout() {
  const isActive = useActive()
  return (
    <div className="min-h-dvh md:flex">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh w-[240px] shrink-0 flex-col bg-sidebar md:flex">
        <div className="flex h-14 items-center border-b border-sidebar-line px-5">
          <BrandMark inverted sub="Operations" />
        </div>
        <nav className="flex-1 space-y-0.5 px-3 py-4" aria-label="Console">
          {NAV.map((item) => {
            const active = isActive(item.to)
            const IconCmp = item.icon
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/console'}
                aria-current={active ? 'page' : undefined}
                className={`relative flex h-10 items-center gap-2.5 rounded-control px-3 text-[13px] font-medium transition-colors duration-150 ${
                  active ? 'bg-sidebar-hover text-sidebar-ink' : 'text-sidebar-text hover:bg-sidebar-hover hover:text-sidebar-ink'
                }`}
              >
                {active ? <span aria-hidden="true" className="absolute inset-y-2.5 left-0 w-[3px] rounded-r bg-sidebar-accent" /> : null}
                <IconCmp size={17} weight={active ? 'bold' : 'regular'} />
                {item.label}
              </NavLink>
            )
          })}
        </nav>
        <div className="space-y-3 px-3 pb-4">
          <HealthBlock />
          <NavLink
            to="/wallet"
            className="flex h-10 items-center gap-2.5 rounded-control px-3 text-[13px] font-medium text-sidebar-text transition-colors duration-150 hover:bg-sidebar-hover hover:text-sidebar-ink"
          >
            <WalletIcon size={17} />
            Customer wallet
          </NavLink>
          <NavLink
            to="/"
            className="flex h-10 items-center gap-2.5 rounded-control px-3 text-[13px] font-medium text-sidebar-text transition-colors duration-150 hover:bg-sidebar-hover hover:text-sidebar-ink"
          >
            <HouseSimpleIcon size={17} />
            Overview
          </NavLink>
        </div>
      </aside>

      {/* Phone and small tablet: dark top bar with the same three destinations */}
      <header className="sticky top-0 z-20 bg-sidebar md:hidden">
        <div className="flex h-14 items-center justify-between px-4">
          <BrandMark inverted sub="Operations" />
          <div className="flex items-center">
            <NavLink
              to="/wallet"
              aria-label="Customer wallet"
              className="grid size-11 place-items-center rounded-control text-sidebar-text hover:text-sidebar-ink"
            >
              <WalletIcon size={19} />
            </NavLink>
            <NavLink
              to="/"
              aria-label="Overview"
              className="grid size-11 place-items-center rounded-control text-sidebar-text hover:text-sidebar-ink"
            >
              <HouseSimpleIcon size={19} />
            </NavLink>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-2 pt-1" aria-label="Console">
          {NAV.map((item) => {
            const active = isActive(item.to)
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/console'}
                aria-current={active ? 'page' : undefined}
                className={`flex h-11 shrink-0 items-center rounded-control px-3 text-[14px] font-medium ${
                  active ? 'bg-sidebar-hover text-sidebar-ink' : 'text-sidebar-text'
                }`}
              >
                {item.label}
              </NavLink>
            )
          })}
        </nav>
      </header>

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1180px] px-4 pb-16 pt-6 sm:px-6 md:px-8 md:pt-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
