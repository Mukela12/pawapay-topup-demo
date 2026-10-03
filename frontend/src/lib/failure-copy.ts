// Customer-facing copy for pawaPay failure codes. pawaPay's own failureMessage is
// written for operators, so it is shown only in the console.

export interface FailureCopy {
  /** Short label for the trace and history rows */
  short: string
  title: string
  body: string
  next: string
  /** Where the payment broke, for the red break on the trace */
  hop: 'pawapay' | 'phone'
}

const COPY: Record<string, FailureCopy> = {
  // Final deposit failures (FAILED)
  PAYMENT_NOT_APPROVED: {
    short: 'Not approved',
    title: 'The payment was not approved',
    body: 'The PIN prompt on the phone was declined, or it timed out before anyone entered the PIN.',
    next: 'Try again and enter the PIN as soon as the prompt appears.',
    hop: 'phone',
  },
  INSUFFICIENT_BALANCE: {
    short: 'Insufficient balance',
    title: 'Not enough money in the mobile money wallet',
    body: 'The wallet on this number did not have enough balance to cover the top-up.',
    next: 'Add money to the mobile money wallet, or try a smaller amount.',
    hop: 'phone',
  },
  PAYER_NOT_FOUND: {
    short: 'No wallet on this number',
    title: 'This number has no mobile money wallet',
    body: 'The network could not find a mobile money account for this phone number.',
    next: 'Check the number and the network, then try again.',
    hop: 'phone',
  },
  PAYER_LIMIT_REACHED: {
    short: 'Wallet limit reached',
    title: 'The wallet has reached its transaction limit',
    body: 'The network caps how much this wallet can send in a day or a month, and this top-up would go over it.',
    next: 'Try a smaller amount, or wait until the limit resets.',
    hop: 'phone',
  },
  WALLET_LIMIT_REACHED: {
    short: 'Wallet limit reached',
    title: 'The wallet has reached its limit',
    body: 'The network refused the payment because a limit on this wallet was reached.',
    next: 'Try a smaller amount, or try again later.',
    hop: 'phone',
  },
  PAYMENT_IN_PROGRESS: {
    short: 'Another payment pending',
    title: 'Another payment is still pending on this number',
    body: 'Some networks allow one pending payment at a time. After an unanswered PIN prompt, that can last up to 10 minutes.',
    next: 'Wait a few minutes, then try again.',
    hop: 'phone',
  },
  UNSPECIFIED_FAILURE: {
    short: 'Declined by the network',
    title: 'The network declined the payment',
    body: 'The mobile money network turned the payment down without giving a reason.',
    next: 'Try again. If it keeps failing, use a different number or network.',
    hop: 'phone',
  },
  UNKNOWN_ERROR: {
    short: 'Unknown error',
    title: 'pawaPay could not complete the payment',
    body: 'pawaPay reported an error it could not classify.',
    next: 'Try again in a minute.',
    hop: 'pawapay',
  },
  NOT_FOUND_AT_PROVIDER: {
    short: 'Not found at pawaPay',
    title: 'pawaPay has no record of this payment',
    body: 'The reconciliation job asked pawaPay about this top-up and the payment was never received, so no money moved.',
    next: 'Start a new top-up.',
    hop: 'pawapay',
  },

  // Initiation rejections (REJECTED, pawaPay answered HTTP 200 with status REJECTED)
  INVALID_PHONE_NUMBER: {
    short: 'Invalid number',
    title: 'That phone number is not valid for this network',
    body: 'pawaPay rejected the number before sending a PIN prompt.',
    next: 'Check the number, including the country code, then try again.',
    hop: 'pawapay',
  },
  AMOUNT_OUT_OF_BOUNDS: {
    short: 'Amount out of range',
    title: 'The amount is outside this network’s limits',
    body: 'Each network sets a minimum and maximum per payment, and this amount falls outside them.',
    next: 'Use an amount inside the range shown under the amount field.',
    hop: 'pawapay',
  },
  INVALID_AMOUNT: {
    short: 'Invalid amount',
    title: 'The amount was not in a format pawaPay accepts',
    body: 'Some networks take whole numbers only, others allow two decimal places.',
    next: 'Enter the amount again and try once more.',
    hop: 'pawapay',
  },
  INVALID_CURRENCY: {
    short: 'Wrong currency',
    title: 'This network does not take that currency',
    body: 'The currency on the top-up does not match the network that was picked.',
    next: 'Pick the network again and retry.',
    hop: 'pawapay',
  },
  INVALID_PROVIDER: {
    short: 'Network unavailable',
    title: 'That network is not available for top-ups',
    body: 'pawaPay does not accept deposits from this network on this account.',
    next: 'Pick a different network.',
    hop: 'pawapay',
  },
  PROVIDER_TEMPORARILY_UNAVAILABLE: {
    short: 'Network down',
    title: 'The network is temporarily unavailable',
    body: 'pawaPay reports that this mobile money network is not taking payments right now.',
    next: 'Try again shortly, or use another network.',
    hop: 'pawapay',
  },
  DEPOSITS_NOT_ALLOWED: {
    short: 'Deposits disabled',
    title: 'Top-ups are switched off for this network',
    body: 'The pawaPay account is not allowed to take deposits on this network.',
    next: 'Use a different network.',
    hop: 'pawapay',
  },
}

const CONFIG_ERRORS = new Set([
  'NO_AUTHENTICATION',
  'AUTHENTICATION_ERROR',
  'AUTHORISATION_ERROR',
  'HTTP_SIGNATURE_ERROR',
])

const REQUEST_ERRORS = new Set([
  'INVALID_INPUT',
  'MISSING_PARAMETER',
  'UNSUPPORTED_PARAMETER',
  'INVALID_PARAMETER',
  'DUPLICATE_METADATA_FIELD',
])

export function failureCopy(code: string | null | undefined): FailureCopy {
  const key = (code ?? '').toUpperCase()
  if (COPY[key]) return COPY[key]
  if (CONFIG_ERRORS.has(key)) {
    return {
      short: 'Service not set up',
      title: 'The payment service is not set up correctly',
      body: 'pawaPay refused the request because of how this server is connected to it. No PIN prompt was sent.',
      next: 'This needs an operator. Try again later.',
      hop: 'pawapay',
    }
  }
  if (REQUEST_ERRORS.has(key)) {
    return {
      short: 'Request rejected',
      title: 'pawaPay rejected the request',
      body: 'Something in the payment request did not pass pawaPay’s checks, so no PIN prompt was sent.',
      next: 'Check the details and try again.',
      hop: 'pawapay',
    }
  }
  return {
    short: 'Payment failed',
    title: 'The payment did not go through',
    body: 'The network or pawaPay reported a failure for this top-up.',
    next: 'Try again. If it keeps failing, use a different number.',
    hop: 'phone',
  }
}

export function isKnownFailure(code: string | null | undefined): boolean {
  const key = (code ?? '').toUpperCase()
  return key in COPY || CONFIG_ERRORS.has(key) || REQUEST_ERRORS.has(key)
}
