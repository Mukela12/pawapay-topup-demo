import { WarningIcon } from '@phosphor-icons/react'
import { ApiError } from '../lib/api'

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return 'Something went wrong.'
}

export function ErrorNotice({ error, title, onRetry }: { error: unknown; title?: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-card border border-danger/25 bg-danger-tint px-4 py-3">
      <WarningIcon size={18} weight="bold" className="mt-0.5 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="text-[14px] font-semibold text-ink">{title ?? 'Could not load this'}</p>
        <p className="mt-0.5 text-[13px] text-ink-2">{errorMessage(error)}</p>
      </div>
      {onRetry ? (
        <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  )
}
