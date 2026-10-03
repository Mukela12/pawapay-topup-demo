import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  children?: ReactNode
  action?: ReactNode
  /** Inside a panel that already has a frame: plain text, no second box. */
  inline?: boolean
}

export function EmptyState({ title, children, action, inline = false }: EmptyStateProps) {
  if (inline) {
    return (
      <div className="py-2">
        <p className="text-[14px] font-medium text-ink">{title}</p>
        {children ? <p className="mt-1 max-w-[52ch] text-[13px] text-ink-2">{children}</p> : null}
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    )
  }
  return (
    <div className="empty-state">
      <p className="text-[14px] font-semibold text-ink">{title}</p>
      {children ? <p className="mx-auto mt-1 max-w-[46ch] text-[13px] text-ink-2">{children}</p> : null}
      {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
    </div>
  )
}
