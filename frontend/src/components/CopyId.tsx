import { CheckIcon, CopyIcon } from '@phosphor-icons/react'
import { useState } from 'react'
import { shortId } from '../lib/format'

/** A depositId (or any id) in mono with a copy button. */
export function CopyId({ value, full = false, label = 'Copy ID' }: { value: string; full?: boolean; label?: string }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1400)
    } catch {
      setCopied(false)
    }
  }
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1">
      <span className="mono min-w-0 truncate text-[13px] text-ink" title={value}>
        {full ? value : shortId(value)}
      </span>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Copied' : label}
        className="inline-flex size-8 shrink-0 items-center max-md:size-11 justify-center rounded-control text-ink-2 transition-colors duration-150 hover:bg-inset hover:text-ink"
      >
        {copied ? <CheckIcon size={15} weight="bold" className="text-success" /> : <CopyIcon size={15} />}
      </button>
    </span>
  )
}
