import { useState } from 'react'
import { Link, Outlet, createBrowserRouter } from 'react-router'
import { Toaster } from 'sonner'
import { useSession } from './lib/queries'
import { Launcher } from './routes/Launcher'

/** Root: starts the anonymous demo session once, on load, before any customer call. */
function Root() {
  useSession()
  // On phones toasts drop from the top so they never cover the primary button.
  const [narrow] = useState(() => window.matchMedia('(max-width: 639px)').matches)
  return (
    <>
      <Outlet />
      <Toaster position={narrow ? 'top-center' : 'bottom-right'} duration={4000} />
    </>
  )
}

function NotFound() {
  return (
    <div className="mx-auto flex min-h-dvh max-w-[560px] flex-col justify-center px-4">
      <p className="text-[13px] font-medium text-ink-2">404</p>
      <h1 className="mt-1 text-[26px] text-ink">There is no page at this address</h1>
      <p className="mt-2 text-[15px] text-ink-2">The demo has three places to go: the overview, the wallet and the console.</p>
      <div className="mt-6 flex flex-wrap gap-3">
        <Link to="/" className="btn btn-primary">
          Go to the overview
        </Link>
        <Link to="/wallet" className="btn btn-secondary">
          Open the wallet
        </Link>
      </div>
    </div>
  )
}

export const router = createBrowserRouter([
  {
    element: <Root />,
    hydrateFallbackElement: <div className="min-h-dvh bg-base" />,
    children: [
      { path: '/', element: <Launcher /> },
      // The wallet and the console load on demand, so the overview stays light.
      { path: '/wallet', lazy: async () => ({ Component: (await import('./routes/wallet/WalletPage')).WalletPage }) },
      {
        path: '/console',
        lazy: async () => ({ Component: (await import('./routes/console/ConsoleLayout')).ConsoleLayout }),
        children: [
          { index: true, lazy: async () => ({ Component: (await import('./routes/console/TopupsPage')).TopupsPage }) },
          {
            path: 'topups/:id',
            lazy: async () => ({ Component: (await import('./routes/console/TopupDetailPage')).TopupDetailPage }),
          },
          {
            path: 'reconciliation',
            lazy: async () => ({ Component: (await import('./routes/console/ReconcilePage')).ReconcilePage }),
          },
          { path: 'events', lazy: async () => ({ Component: (await import('./routes/console/EventsPage')).EventsPage }) },
        ],
      },
      { path: '*', element: <NotFound /> },
    ],
  },
])
