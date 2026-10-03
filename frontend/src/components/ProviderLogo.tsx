import { useState } from 'react'

/** pawaPay serves a logo per provider in active-conf. Falls back to a monogram. */
export function ProviderLogo({ src, name, size = 32 }: { src: string | null | undefined; name: string; size?: number }) {
  const [failed, setFailed] = useState(false)
  if (src && !failed) {
    return (
      <img
        src={src}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        onError={() => setFailed(true)}
        className="shrink-0 rounded-control border border-line bg-white object-contain"
        style={{ width: size, height: size }}
      />
    )
  }
  const initials = name
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
  return (
    <span
      aria-hidden="true"
      className="grid shrink-0 place-items-center rounded-control bg-inset text-[11px] font-semibold text-ink-2"
      style={{ width: size, height: size }}
    >
      {initials}
    </span>
  )
}
