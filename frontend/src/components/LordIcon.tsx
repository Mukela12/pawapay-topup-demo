import { useReducedMotion } from 'framer-motion'

// Lordicon is reserved for the few stateful moments: waiting on the phone,
// success, failure, and a payment that is taking longer than usual.
const ICONS = {
  phone: { src: '/icons/phone-ring.json', rest: 'hover-phone-ring' },
  check: { src: '/icons/check.json', rest: 'hover-pinch' },
  error: { src: '/icons/error.json', rest: 'hover-error-1' },
  clock: { src: '/icons/clock.json', rest: 'hover-clock' },
} as const

export type LordIconName = keyof typeof ICONS

interface LordIconProps {
  name: LordIconName
  size?: number
  /** loop: keeps playing. in: plays the intro once on mount. */
  trigger?: 'loop' | 'in' | 'hover'
  state?: string
  color?: string
  delay?: number
  className?: string
}

export function LordIcon({ name, size = 48, trigger = 'in', state, color, delay, className }: LordIconProps) {
  const reduce = useReducedMotion()
  const icon = ICONS[name]
  // Reduced motion: no trigger, rest on the static first frame of the default state.
  return (
    <lord-icon
      aria-hidden="true"
      className={className}
      src={icon.src}
      trigger={reduce ? undefined : trigger}
      state={reduce ? icon.rest : state}
      colors={color ? `primary:${color},secondary:${color}` : undefined}
      delay={reduce ? undefined : delay}
      style={{ width: size, height: size, display: 'inline-block', flexShrink: 0 }}
    />
  )
}
