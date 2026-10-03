import { GithubLogoIcon } from '@phosphor-icons/react'
import { Link } from 'react-router'
import { BrandMark } from '../components/BrandMark'
import { formatMinor } from '../lib/format'
import { useHealth, useSession, useStats, useWallet } from '../lib/queries'

const REPO_URL = 'https://github.com/Mukela12/pawapay-topup-demo'

const LOOK_AT = [
  {
    title: 'A duplicate callback credits once',
    body: 'Open a completed top-up and press Replay last callback. The webhook answers 200, records duplicate_ignored, and the balance stays where it was.',
    to: '/console?status=completed',
    cta: 'Open completed top-ups',
  },
  {
    title: 'A forged callback is rejected',
    body: 'Send forged callback posts a COMPLETED body with a bad signature through the real webhook handler. It gets a 401 and nothing changes.',
    to: '/console?status=completed',
    cta: 'Pick a top-up to forge against',
  },
  {
    title: 'A missed callback is settled by reconciliation',
    body: 'If a callback never arrives, a scheduled job asks pawaPay about every top-up still open past the threshold and applies the answer.',
    to: '/console/reconciliation',
    cta: 'See reconciliation runs',
  },
] as const

function Entry({ to, title, body, meta }: { to: string; title: string; body: string; meta?: string | null }) {
  return (
    <Link
      to={to}
      className="block rounded-card border border-line bg-surface px-5 py-4 transition-[border-color,transform] duration-150 hover:border-ink-3 active:scale-[0.99] motion-reduce:active:scale-100"
    >
      <span className="block text-[16px] font-semibold tracking-[-0.01em] text-ink">{title}</span>
      <span className="mt-1 block text-[14px] text-ink-2">{body}</span>
      {meta ? <span className="mt-3 block border-t border-line pt-3 text-[13px] text-ink-2">{meta}</span> : null}
    </Link>
  )
}

export function Launcher() {
  const health = useHealth()
  const h = health.data
  const session = useSession()
  const wallet = useWallet(session.isSuccess)
  const stats = useStats()

  const balance = wallet.data?.balances[0]
  const walletMeta = balance
    ? `Your balance in this browser: ${formatMinor(balance.balance_minor, balance.currency, balance.exponent)}`
    : null
  const s = stats.data
  const consoleMeta = s
    ? `Today: ${s.completed_today} completed, ${s.failed_today} failed, ${s.open_now} open now`
    : null

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex h-16 w-full max-w-[1120px] items-center justify-between px-4 sm:px-6">
        <BrandMark sub="pawaPay top-up demo" />
        <a
          href={REPO_URL}
          className="btn btn-ghost btn-sm min-w-11"
          target="_blank"
          rel="noreferrer"
          aria-label="Source code on GitHub"
        >
          <GithubLogoIcon size={16} aria-hidden="true" />
          <span className="hidden sm:inline">Source</span>
        </a>
      </header>

      <main className="mx-auto w-full max-w-[1120px] flex-1 px-4 pb-16 pt-6 sm:px-6 sm:pt-14">
        <div className="grid gap-12 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:gap-16">
          <div>
            <h1 className="max-w-[22ch] text-[clamp(1.9rem,1.4rem+2vw,2.75rem)] leading-[1.08] text-ink">
              Mobile money <span className="whitespace-nowrap">top-ups</span> with pawaPay, end to end
            </h1>
            <section aria-labelledby="what-h" className="mt-6">
              <h2 id="what-h" className="text-[15px] text-ink">
                What this is
              </h2>
              <p className="mt-2 max-w-[62ch] text-[16px] leading-relaxed text-ink-2">
                Ringwise is a made-up calling-credit brand. Topping up its wallet calls the real pawaPay sandbox API, and the
                final status arrives through pawaPay’s real callbacks into a Flask and PostgreSQL backend with a React front
                end. Credits go through an append-only ledger, every callback is signature-checked or confirmed with pawaPay
                before it moves money, and a reconciliation job catches the ones that never arrive. No real money moves: pawaPay’s sandbox simulates the customer’s phone.
              </p>
            </section>

            <div className="mt-8 grid gap-3">
              <Entry
                to="/wallet"
                title="Top up a wallet"
                body="Pick a sandbox test number, send a top-up and watch the payment trace light up as pawaPay reports back."
                meta={walletMeta}
              />
              <Entry
                to="/console"
                title="Open the operations console"
                body="Every top-up, callback and reconciliation run, with controls to re-check, replay and forge callbacks."
                meta={consoleMeta}
              />
            </div>
          </div>

          <section aria-labelledby="look-h" className="lg:pt-2">
            <h2 id="look-h" className="text-[15px] text-ink">
              What to look at
            </h2>
            <ul className="mt-4 divide-y divide-line border-y border-line">
              {LOOK_AT.map((item) => (
                <li key={item.title} className="py-5">
                  <h3 className="text-[15px] font-semibold tracking-[-0.01em] text-ink">{item.title}</h3>
                  <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{item.body}</p>
                  <Link
                    to={item.to}
                    className="mt-2 inline-flex min-h-[44px] items-center text-[14px] font-medium text-accent underline decoration-accent/30 underline-offset-4 hover:decoration-accent"
                  >
                    {item.cta}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1120px] flex-col gap-2 px-4 py-6 text-[13px] text-ink-2 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p>Flask and PostgreSQL backend, React and TypeScript front end, pawaPay Merchant API v2 in sandbox.</p>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
            {h ? (
              <span>
                {h.pawapay_configured ? 'pawaPay sandbox token set' : 'pawaPay token not set'}, signed callbacks{' '}
                {h.signature_required ? 'required' : 'optional'}
              </span>
            ) : health.isError ? (
              <span>Server health unavailable</span>
            ) : null}
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-h-[44px] items-center gap-1.5 font-medium text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink"
            >
              <GithubLogoIcon size={15} />
              github.com/Mukela12/pawapay-topup-demo
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}
