import type { DetailedHTMLProps, HTMLAttributes } from 'react'

// The <lord-icon> custom element registered by https://cdn.lordicon.com/lordicon.js
declare module 'react' {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace JSX {
    interface IntrinsicElements {
      'lord-icon': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        src?: string
        trigger?: string
        state?: string
        colors?: string
        delay?: string | number
        target?: string
      }
    }
  }
}
