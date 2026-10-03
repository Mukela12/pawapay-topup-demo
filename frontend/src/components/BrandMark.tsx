/** Ringwise wordmark: a ring with a cobalt core, the same shape as the favicon. */
export function BrandMark({ inverted = false, sub }: { inverted?: boolean; sub?: string }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <svg width="24" height="24" viewBox="0 0 32 32" aria-hidden="true" className="shrink-0">
        <rect width="32" height="32" rx="7" fill={inverted ? '#EEF1ED' : '#10201F'} />
        <circle cx="16" cy="16" r="6.5" fill="none" stroke={inverted ? '#0E1A1C' : '#F5F6F3'} strokeWidth="2.5" />
        <circle cx="16" cy="16" r="2" fill="#2747D6" />
      </svg>
      <span className="flex items-baseline gap-2">
        <span className={`text-[15px] font-semibold tracking-[-0.02em] ${inverted ? 'text-sidebar-ink' : 'text-ink'}`}>
          Ringwise
        </span>
        {sub ? (
          <span className={`hidden text-[13px] sm:inline ${inverted ? 'text-sidebar-text' : 'text-ink-2'}`}>{sub}</span>
        ) : null}
      </span>
    </span>
  )
}
