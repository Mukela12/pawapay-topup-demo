import { CaretDownIcon, CheckCircleIcon, CheckIcon, CircleNotchIcon, ClockIcon, InfoIcon, XCircleIcon } from '@phosphor-icons/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { ErrorNotice } from '../../components/ErrorNotice'
import { ProviderLogo } from '../../components/ProviderLogo'
import { api, ApiError, type AppConfig, type Country, type Provider, type Scenario, type Topup } from '../../lib/api'
import { failureCopy } from '../../lib/failure-copy'
import { formatMajor, formatMsisdn } from '../../lib/format'
import { useDebounced } from '../../lib/hooks'
import { keys } from '../../lib/queries'

export interface TopupDraft {
  country: string
  provider: string
  phoneLocal: string
  amount: string
  scenario?: string
}

/**
 * Left padding that clears a mono prefix such as "+260" or "ZMW". The prefix is
 * set at the field's own size, so `ch` measures it exactly: 12px inset, the
 * prefix, then a 10px gap before the typed text.
 */
function prefixPad(chars: number): string {
  return `calc(12px + ${chars}ch + 10px)`
}

/** Digits the customer typed, without the dialling prefix or a trunk zero. */
function localPart(msisdn: string, prefix: string): string {
  const digits = msisdn.replace(/\D/g, '')
  return digits.startsWith(prefix) && digits.length > prefix.length + 6 ? digits.slice(prefix.length) : digits
}

function composeMsisdn(prefix: string, local: string): string {
  const digits = local.replace(/\D/g, '')
  if (digits.startsWith(prefix) && digits.length > prefix.length + 6) return digits
  return prefix + digits.replace(/^0+/, '')
}

function validateAmount(value: string, provider: Provider | undefined, currency: string): string | null {
  const v = value.trim()
  if (!v) return 'Enter an amount.'
  if (!provider) return null
  const whole = provider.decimals === 0
  const pattern = whole ? /^\d+$/ : /^\d+(\.\d{1,2})?$/
  if (!pattern.test(v)) return whole ? `${provider.display_name} takes whole numbers only.` : 'Use at most two decimal places.'
  const n = Number(v)
  if (!(n > 0)) return 'Enter an amount above zero.'
  const min = provider.min_amount !== null && provider.min_amount !== undefined ? Number(provider.min_amount) : null
  const max = provider.max_amount !== null && provider.max_amount !== undefined ? Number(provider.max_amount) : null
  if (min !== null && Number.isFinite(min) && n < min)
    return `The minimum on ${provider.display_name} is ${formatMajor(min, currency, provider.decimals)}.`
  if (max !== null && Number.isFinite(max) && n > max)
    return `The maximum on ${provider.display_name} is ${formatMajor(max, currency, provider.decimals)}.`
  return null
}

type Expected = { kind: 'completes' | 'fails' | 'open'; label: string }

/** Scenario `expected` reads like "COMPLETED", "FAILED: PAYER_NOT_FOUND" or "SUBMITTED (stays open)". */
function readExpected(expected: string): Expected {
  const e = expected.toUpperCase()
  if (e.startsWith('COMPLETED')) return { kind: 'completes', label: 'Completes' }
  if (e.startsWith('FAILED')) {
    const code = e.split(':')[1]?.trim()
    return { kind: 'fails', label: code ? `Fails, ${failureCopy(code).short.toLowerCase()}` : 'Fails' }
  }
  return { kind: 'open', label: 'Stays open' }
}

const EXPECTED_ICON = {
  completes: <CheckCircleIcon size={15} weight="fill" className="shrink-0 text-success" />,
  fails: <XCircleIcon size={15} weight="fill" className="shrink-0 text-danger" />,
  open: <ClockIcon size={15} weight="bold" className="shrink-0 text-ink-2" />,
}

interface TopupFormProps {
  config: AppConfig
  scenarios: Scenario[]
  draft: TopupDraft | null
  onCreated: (topup: Topup) => void
}

export function TopupForm({ config, scenarios, draft, onCreated }: TopupFormProps) {
  const qc = useQueryClient()
  const formId = useId()
  const countries = config.countries
  // Without a pawaPay token the API refuses top-ups with a 503; say so up front.
  const configured = config.pawapay_configured !== false
  const [countryCode, setCountryCode] = useState(draft?.country ?? countries[0]?.country ?? '')
  const country: Country | undefined = countries.find((c) => c.country === countryCode) ?? countries[0]
  const prefix = country?.prefix ?? ''

  const [phoneLocal, setPhoneLocal] = useState(draft?.phoneLocal ?? '')
  const [chosenProvider, setProviderCode] = useState(draft?.provider ?? '')
  const [providerPicked, setProviderPicked] = useState(Boolean(draft?.provider))
  const [amount, setAmount] = useState(draft?.amount ?? '')
  const [scenarioId, setScenarioId] = useState<string | undefined>(draft?.scenario)
  const [submitted, setSubmitted] = useState(false)
  const [amountTouched, setAmountTouched] = useState(Boolean(draft?.amount))
  const [serverField, setServerField] = useState<{ field: string; message: string } | null>(null)
  const [banner, setBanner] = useState<unknown>(null)

  const currency = country?.currency ?? ''
  const msisdn = composeMsisdn(prefix, phoneLocal)
  const phoneDigits = phoneLocal.replace(/\D/g, '')

  // Live network prediction, debounced, preselecting until the customer picks one.
  const debouncedMsisdn = useDebounced(msisdn, 400)
  const canPredict = configured && phoneDigits.length >= 8 && debouncedMsisdn === msisdn
  const prediction = useQuery({
    queryKey: keys.predict(debouncedMsisdn),
    queryFn: () => api.predictProvider(debouncedMsisdn),
    enabled: canPredict,
    staleTime: 10 * 60_000,
    retry: false,
  })
  // Until the customer picks a network, the prediction preselects it.
  const predictedCode =
    prediction.data && country?.providers.some((x) => x.provider === prediction.data.provider)
      ? prediction.data.provider
      : null
  const providerCode = !providerPicked && predictedCode ? predictedCode : chosenProvider

  const provider = country?.providers.find((p) => p.provider === providerCode)

  const predictedName = useMemo(() => {
    const p = prediction.data
    if (!p) return null
    return countries.find((c) => c.country === p.country)?.providers.find((x) => x.provider === p.provider)?.display_name ?? null
  }, [prediction.data, countries])

  const amountError = validateAmount(amount, provider, currency)
  const phoneError = phoneDigits.length < 7 ? 'Enter the mobile money number.' : null
  const providerError = !provider ? 'Pick the mobile money network.' : null
  const showAmountError = (submitted || amountTouched) && amountError
  const fieldError = (name: string) => (serverField?.field === name ? serverField.message : null)

  // Same body, same Idempotency-Key: a double submit or a retry after a dropped
  // response returns the same top-up instead of creating a second one.
  const keysByBody = useRef(new Map<string, string>())

  // Field refs, so a failed submit can move focus to the first field to fix.
  const countryRef = useRef<HTMLSelectElement>(null)
  const phoneRef = useRef<HTMLInputElement>(null)
  const networkRef = useRef<HTMLFieldSetElement>(null)
  const amountRef = useRef<HTMLInputElement>(null)
  function focusField(field: string) {
    if (field === 'country') countryRef.current?.focus()
    else if (field === 'phone') phoneRef.current?.focus()
    else if (field === 'provider') {
      const radios = networkRef.current?.querySelectorAll<HTMLInputElement>('input[type="radio"]')
      const target = radios ? (Array.from(radios).find((r) => r.checked) ?? radios[0]) : undefined
      target?.focus()
    } else if (field === 'amount') amountRef.current?.focus()
  }

  const create = useMutation({
    mutationFn: (vars: { body: Parameters<typeof api.createTopup>[0]; key: string }) => api.createTopup(vars.body, vars.key),
    onSuccess: ({ topup }) => {
      qc.setQueryData(keys.topup(topup.id), { topup, events: [] })
      void qc.invalidateQueries({ queryKey: keys.wallet })
      onCreated(topup)
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 409 && err.openTopup) {
          toast('You already have a top-up in progress', { description: 'Showing it now. Start a new one once it finishes.' })
          onCreated(err.openTopup)
          return
        }
        if (err.status === 422 && err.field) {
          setServerField({ field: err.field, message: err.message })
          focusField(err.field)
          return
        }
      }
      setBanner(err)
    },
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitted(true)
    setServerField(null)
    setBanner(null)
    if (amountError || phoneError || providerError || !country || !provider) {
      // Same order as the form: phone, then network, then amount.
      if (!country) focusField('country')
      else if (phoneError) focusField('phone')
      else if (providerError || !provider) focusField('provider')
      else focusField('amount')
      return
    }
    const body = {
      country: country.country,
      provider: provider.provider,
      phone: prediction.data && debouncedMsisdn === msisdn ? prediction.data.phone_number : msisdn,
      amount: amount.trim(),
      ...(scenarioId ? { scenario: scenarioId } : {}),
    }
    const sig = JSON.stringify(body)
    let key = keysByBody.current.get(sig)
    if (!key) {
      key = crypto.randomUUID()
      keysByBody.current.set(sig, key)
    }
    create.mutate({ body, key })
  }

  function applyScenario(s: Scenario) {
    const target = countries.find((c) => c.country === s.country)
    if (target) setCountryCode(target.country)
    setPhoneLocal(localPart(s.phone, target?.prefix ?? prefix))
    setProviderCode(s.provider)
    setProviderPicked(true)
    setScenarioId(s.id)
    setServerField(null)
    if (!amount) {
      const p = target?.providers.find((x) => x.provider === s.provider)
      const min = p?.min_amount ? Number(p.min_amount) : 1
      setAmount(String(Math.max(25, Number.isFinite(min) ? Math.ceil(min) : 25)))
    }
  }

  const presets = useMemo(() => {
    if (!provider) return []
    const min = Number(provider.min_amount ?? 0)
    const max = Number(provider.max_amount ?? Infinity)
    return [10, 25, 50, 100].filter((n) => n >= min && n <= max).slice(0, 3)
  }, [provider])

  const busy = create.isPending
  const selectedScenario = scenarios.find((x) => x.id === scenarioId)
  const amountLabel = !amountError && provider ? formatMajor(amount.trim(), currency, provider.decimals) : null

  return (
    <form id={formId} onSubmit={onSubmit} noValidate className="space-y-6">
      <div>
        <h2 className="text-[18px] text-ink">Top up calling credit</h2>
        <p className="mt-1 text-[14px] text-ink-2">
          Pay from a mobile money wallet. The money moves through pawaPay and your balance updates once pawaPay confirms it.
        </p>
      </div>

      {!configured ? (
        <div role="status" className="rounded-card border border-warning/30 bg-warning-tint px-4 py-3">
          <p className="text-[14px] font-semibold text-ink">Top-ups are switched off on this server</p>
          <p className="mt-0.5 text-[13px] text-ink-2">
            pawaPay is not configured (PAWAPAY_API_TOKEN is unset), so the API refuses new top-ups. The form, the history and
            the operations console still work.
          </p>
        </div>
      ) : null}

      {scenarios.length > 0 ? (
        <fieldset>
          <legend className="label">Sandbox test numbers</legend>
          <p className="hint -mt-1 mb-3">Each number makes pawaPay’s sandbox return a fixed result. Pick one to fill the form.</p>
          <div className="flex flex-wrap gap-2">
            {scenarios.map((s) => {
              const active = scenarioId === s.id
              const exp = readExpected(s.expected)
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => applyScenario(s)}
                  aria-pressed={active}
                  aria-label={`${s.label}. ${exp.label}.`}
                  className={`btn btn-sm gap-1.5 border font-medium ${
                    active ? 'border-accent bg-accent-tint text-ink' : 'border-line-strong bg-surface text-ink hover:bg-inset'
                  }`}
                >
                  {EXPECTED_ICON[exp.kind]}
                  <span className="max-w-[24ch] truncate">{s.label}</span>
                </button>
              )
            })}
          </div>
          {selectedScenario ? (
            <p className="hint mt-2">
              <span className="mono text-ink">{formatMsisdn(selectedScenario.phone)}</span>: {selectedScenario.description}
            </p>
          ) : null}
        </fieldset>
      ) : null}

      <div className="grid gap-5 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)]">
        <div>
          <label className="label" htmlFor={`${formId}-country`}>
            Country
          </label>
          {countries.length > 1 ? (
            <div className="relative">
              <select
                ref={countryRef}
                id={`${formId}-country`}
                className="field appearance-none pr-9"
                value={country?.country ?? ''}
                onChange={(e) => {
                  setCountryCode(e.target.value)
                  setProviderCode('')
                  setProviderPicked(false)
                  setScenarioId(undefined)
                }}
              >
                {countries.map((c) => (
                  <option key={c.country} value={c.country}>
                    {c.display_name}
                  </option>
                ))}
              </select>
              <CaretDownIcon
                size={14}
                weight="bold"
                aria-hidden="true"
                className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ink-2"
              />
            </div>
          ) : (
            <p id={`${formId}-country`} className="field flex items-center bg-inset text-ink-2">
              {country?.display_name}
            </p>
          )}
        </div>

        <div>
          <label className="label" htmlFor={`${formId}-phone`}>
            Mobile money number
          </label>
          <div className="relative">
            <span
              className="mono pointer-events-none absolute inset-y-0 left-3 flex items-center text-[16px] text-ink-2 md:text-[15px]"
            >
              +{prefix}
            </span>
            <input
              ref={phoneRef}
              id={`${formId}-phone`}
              className="field mono"
              style={{ paddingLeft: prefixPad(prefix.length + 1) }}
              inputMode="tel"
              autoComplete="tel-national"
              placeholder="763 456 789"
              value={phoneLocal}
              aria-invalid={Boolean((submitted && phoneError) || fieldError('phone'))}
              aria-describedby={`${formId}-phone-hint`}
              onChange={(e) => {
                setPhoneLocal(e.target.value)
                setScenarioId(undefined)
                setProviderPicked(false)
                setServerField(null)
              }}
            />
          </div>
          <p id={`${formId}-phone-hint`} className="hint mt-1.5 min-h-[20px]" aria-live="polite">
            {fieldError('phone') ? (
              <span className="text-danger">{fieldError('phone')}</span>
            ) : submitted && phoneError ? (
              <span className="text-danger">{phoneError}</span>
            ) : prediction.isFetching ? (
              'Checking the network…'
            ) : prediction.data && predictedName ? (
              <span className="inline-flex items-center gap-1.5">
                <CheckIcon size={13} weight="bold" className="text-success" />
                pawaPay reads this as {predictedName}
                {providerPicked && providerCode !== prediction.data.provider ? ', you picked a different network' : ''}
              </span>
            ) : prediction.isError ? (
              'pawaPay could not tell the network from this number. Pick it below.'
            ) : !configured ? (
              'Network prediction needs the pawaPay token. Pick the network below.'
            ) : (
              'Type the number and pawaPay predicts the network.'
            )}
          </p>
        </div>
      </div>

      <fieldset ref={networkRef}>
        <legend className="label">Network</legend>
        {country && country.providers.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-3">
            {country.providers.map((p) => {
              const checked = p.provider === providerCode
              const predicted = prediction.data?.provider === p.provider
              return (
                <label
                  key={p.provider}
                  className={`relative flex min-h-[56px] cursor-pointer items-center gap-3 rounded-control border px-3 py-2 transition-colors duration-150 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent ${
                    checked ? 'border-accent bg-accent-tint' : 'border-line-strong bg-surface hover:bg-inset'
                  }`}
                >
                  <input
                    type="radio"
                    name={`${formId}-provider`}
                    value={p.provider}
                    checked={checked}
                    onChange={() => {
                      setProviderCode(p.provider)
                      setProviderPicked(true)
                      setServerField(null)
                    }}
                    className="sr-only"
                  />
                  <ProviderLogo src={p.logo} name={p.display_name} size={32} />
                  <span className="min-w-0">
                    <span className="block truncate text-[14px] font-medium text-ink">{p.display_name}</span>
                    {p.status === 'DELAYED' || predicted ? (
                      <span className="block truncate text-[12px] text-ink-2">
                        {p.status === 'DELAYED' ? 'Running slow right now' : 'Predicted'}
                      </span>
                    ) : null}
                  </span>
                </label>
              )
            })}
          </div>
        ) : (
          <p className="hint">No networks are open for top-ups in this country right now.</p>
        )}
        {(submitted && providerError) || fieldError('provider') ? (
          <p className="mt-1.5 text-[13px] text-danger">{fieldError('provider') ?? providerError}</p>
        ) : null}
      </fieldset>

      <div>
        <label className="label" htmlFor={`${formId}-amount`}>
          Amount
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full sm:w-[220px]">
            <span
              className="mono pointer-events-none absolute inset-y-0 left-3 flex items-center text-[16px] text-ink-2 md:text-[15px]"
            >
              {currency}
            </span>
            <input
              ref={amountRef}
              id={`${formId}-amount`}
              className="field mono"
              style={{ paddingLeft: prefixPad(currency.length) }}
              inputMode={provider?.decimals === 0 ? 'numeric' : 'decimal'}
              placeholder={provider?.decimals === 0 ? '500' : '25.00'}
              value={amount}
              aria-invalid={Boolean(showAmountError || fieldError('amount'))}
              aria-describedby={`${formId}-amount-hint`}
              onChange={(e) => {
                setAmount(e.target.value)
                setServerField(null)
              }}
              onBlur={() => setAmountTouched(true)}
            />
          </div>
          {presets.map((n) => (
            <button
              key={n}
              type="button"
              className={`btn btn-sm btn-secondary mono font-medium ${amount === String(n) ? 'border-accent bg-accent-tint' : ''}`}
              onClick={() => {
                setAmount(String(n))
                setAmountTouched(true)
              }}
            >
              {n}
            </button>
          ))}
        </div>
        <p id={`${formId}-amount-hint`} className="hint mt-1.5">
          {fieldError('amount') ? (
            <span className="text-danger">{fieldError('amount')}</span>
          ) : showAmountError ? (
            <span className="text-danger">{amountError}</span>
          ) : provider ? (
            <>
              Between {formatMajor(provider.min_amount, currency, provider.decimals)} and{' '}
              {formatMajor(provider.max_amount, currency, provider.decimals)} on {provider.display_name}
              {provider.decimals === 0 ? ', whole numbers only' : ''}.
            </>
          ) : (
            'Pick a network to see its limits.'
          )}
        </p>
      </div>

      {banner ? <ErrorNotice error={banner} title="The top-up was not started" /> : null}
      {serverField && !['phone', 'amount', 'provider'].includes(serverField.field) ? (
        <ErrorNotice error={new Error(serverField.message)} title="Check the details" />
      ) : null}

      <div className="flex flex-col-reverse gap-3 border-t border-line pt-5 sm:flex-row sm:items-center sm:justify-between">
        <p className="flex items-start gap-2 text-[13px] text-ink-2">
          <InfoIcon size={16} className="mt-0.5 shrink-0" />
          <span>pawaPay sandbox: no real money moves.</span>
        </p>
        <button
          type="submit"
          className="btn btn-primary w-full sm:w-auto sm:min-w-[200px]"
          disabled={busy || !configured}
          aria-busy={busy || undefined}
        >
          {busy ? <CircleNotchIcon size={16} weight="bold" aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : null}
          {!configured ? 'Top-ups are switched off' : amountLabel ? `Top up ${amountLabel}` : 'Top up'}
        </button>
        <p className="sr-only" aria-live="polite">
          {busy ? 'Sending the top-up to pawaPay.' : ''}
        </p>
      </div>
    </form>
  )
}
