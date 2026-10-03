// Sample data in the exact shapes the Flask API returns. For visual reasoning and
// local UI checks only: nothing in the app imports this file, and the app never
// falls back to it. Every screen calls the real API.

import type {
  AppConfig,
  ConsoleStats,
  ConsoleTopupDetail,
  Health,
  PaymentEvent,
  ReconcileRun,
  Scenario,
  Topup,
  TopupStatus,
  Wallet,
} from './api'

const BASE = Date.parse('2026-10-03T09:41:07Z')
const at = (offsetMs: number) => new Date(BASE + offsetMs).toISOString()

export const fixtureConfig: AppConfig = {
  countries: [
    {
      country: 'ZMB',
      display_name: 'Zambia',
      prefix: '260',
      currency: 'ZMW',
      providers: [
        {
          provider: 'MTN_MOMO_ZMB',
          display_name: 'MTN',
          logo: null,
          currency: 'ZMW',
          name_displayed_to_customer: 'Ringwise',
          min_amount: '1',
          max_amount: '10000',
          decimals: 2,
          decimals_in_amount: 'TWO_PLACES',
          status: 'OPERATIONAL',
          pin_prompt: 'AUTOMATIC',
          pin_prompt_revivable: true,
          pin_prompt_instructions: [
            {
              type: 'USSD',
              display_name: 'If the prompt does not appear',
              quick_link: null,
              steps: ['Dial *115#', 'Choose My Approvals', 'Enter your MoMo PIN to approve the payment'],
            },
          ],
        },
        {
          provider: 'AIRTEL_OAPI_ZMB',
          display_name: 'Airtel',
          logo: null,
          currency: 'ZMW',
          name_displayed_to_customer: 'Ringwise',
          min_amount: '1',
          max_amount: '10000',
          decimals: 2,
          decimals_in_amount: 'TWO_PLACES',
          status: 'OPERATIONAL',
          pin_prompt: 'AUTOMATIC',
          pin_prompt_revivable: false,
          pin_prompt_instructions: [],
        },
        {
          provider: 'ZAMTEL_ZMB',
          display_name: 'Zamtel',
          logo: null,
          currency: 'ZMW',
          name_displayed_to_customer: 'Ringwise',
          min_amount: '1',
          max_amount: '10000',
          decimals: 2,
          decimals_in_amount: 'TWO_PLACES',
          status: 'DELAYED',
          pin_prompt: 'AUTOMATIC',
          pin_prompt_revivable: false,
          pin_prompt_instructions: [],
        },
      ],
    },
  ],
  sandbox: true,
  reconcile_after_seconds: 60,
  source: 'fallback',
  fallback_reason: 'pawapay_not_configured',
  pawapay_configured: true,
}

export const fixtureScenarios: Scenario[] = [
  {
    id: 'mtn-completed',
    label: 'MTN, approved',
    description: 'The sandbox approves the payment and sends a COMPLETED callback.',
    country: 'ZMB',
    provider: 'MTN_MOMO_ZMB',
    phone: '260763456789',
    expected: 'COMPLETED',
  },
  {
    id: 'mtn-not-approved',
    label: 'MTN, PIN not approved',
    description: 'The customer does not approve the PIN prompt in time.',
    country: 'ZMB',
    provider: 'MTN_MOMO_ZMB',
    phone: '260763456039',
    expected: 'FAILED: PAYMENT_NOT_APPROVED',
  },
  {
    id: 'mtn-payer-not-found',
    label: 'MTN, no wallet',
    description: 'The number has no MTN mobile money account.',
    country: 'ZMB',
    provider: 'MTN_MOMO_ZMB',
    phone: '260763456029',
    expected: 'FAILED: PAYER_NOT_FOUND',
  },
  {
    id: 'mtn-unspecified',
    label: 'MTN, declined',
    description: 'The network declines without a specific reason.',
    country: 'ZMB',
    provider: 'MTN_MOMO_ZMB',
    phone: '260763456069',
    expected: 'FAILED: UNSPECIFIED_FAILURE',
  },
  {
    id: 'mtn-submitted',
    label: 'MTN, never settles',
    description: 'The payment stays pending with no final callback, so reconciliation has to settle it.',
    country: 'ZMB',
    provider: 'MTN_MOMO_ZMB',
    phone: '260763456129',
    expected: 'SUBMITTED (stays open)',
  },
  {
    id: 'airtel-completed',
    label: 'Airtel, approved',
    description: 'The sandbox approves the payment and sends a COMPLETED callback.',
    country: 'ZMB',
    provider: 'AIRTEL_OAPI_ZMB',
    phone: '260973456789',
    expected: 'COMPLETED',
  },
  {
    id: 'airtel-insufficient',
    label: 'Airtel, low balance',
    description: 'The wallet does not have enough money for the payment.',
    country: 'ZMB',
    provider: 'AIRTEL_OAPI_ZMB',
    phone: '260973456049',
    expected: 'FAILED: INSUFFICIENT_BALANCE',
  },
]

export function fixtureTopup(status: TopupStatus, overrides: Partial<Topup> = {}): Topup {
  const final = !['CREATED', 'ACCEPTED', 'PROCESSING', 'IN_RECONCILIATION'].includes(status)
  return {
    id: '7f1c2a9e-5b3d-4e8a-9c61-0d2f4b7a8e13',
    deposit_id: 'c3a8f0d2-61e4-4b9f-8a27-5e9d1c0b4f76',
    status,
    is_open: !final,
    amount: 'ZMW 25.00',
    amount_minor: 2500,
    currency: 'ZMW',
    exponent: 2,
    country: 'ZMB',
    provider: 'MTN_MOMO_ZMB',
    phone: '260763456789',
    failure_code: null,
    reason: null,
    provider_txn_id: status === 'COMPLETED' ? 'MP261003.0941.C47215' : null,
    scenario: 'mtn-completed',
    created_at: at(0),
    accepted_at: status === 'CREATED' ? null : at(412),
    finalized_at: final ? at(3870) : null,
    last_checked_at: null,
    check_count: 0,
    ...overrides,
  }
}

export function fixtureEvents(topup: Topup): PaymentEvent[] {
  const events: PaymentEvent[] = []
  if (topup.status !== 'CREATED') {
    events.push({
      id: 'e1',
      topup_id: topup.id,
      deposit_id: topup.deposit_id,
      source: 'initiate_response',
      http_status: 200,
      signature: 'not_checked',
      outcome: 'no_change',
      pawapay_status: topup.status === 'REJECTED' ? 'REJECTED' : 'ACCEPTED',
      detail: topup.status === 'REJECTED' ? 'pawaPay rejected the deposit.' : 'pawaPay accepted the deposit.',
      created_at: at(412),
      payload: { depositId: topup.deposit_id, status: 'ACCEPTED', created: at(400) },
      headers: { 'content-type': 'application/json' },
    })
  }
  if (topup.status === 'COMPLETED' || topup.status === 'FAILED') {
    events.push({
      id: 'e2',
      topup_id: topup.id,
      deposit_id: topup.deposit_id,
      source: 'callback',
      http_status: 200,
      signature: 'verified',
      outcome: 'applied',
      pawapay_status: topup.status,
      detail: topup.status === 'COMPLETED' ? 'Credited ZMW 25.00 to the wallet.' : 'Marked failed.',
      created_at: at(3870),
      payload: {
        depositId: topup.deposit_id,
        status: topup.status,
        amount: '25.00',
        currency: 'ZMW',
        country: 'ZMB',
        payer: { type: 'MMO', accountDetails: { phoneNumber: topup.phone, provider: topup.provider } },
        customerMessage: 'Ringwise topup',
        created: at(400),
        providerTransactionId: topup.provider_txn_id,
        metadata: { customerId: '0b6e', source: 'ringwise-demo' },
      },
      headers: {
        'content-type': 'application/json; charset=UTF-8',
        'content-digest': 'present',
        'signature-input': 'present',
        signature: 'present',
      },
    })
  }
  return events
}

export const fixtureWallet: Wallet = {
  balances: [{ currency: 'ZMW', balance_minor: 12500, exponent: 2 }],
  topups: [
    fixtureTopup('COMPLETED'),
    fixtureTopup('FAILED', {
      id: 'a2',
      deposit_id: '5d0e9b1f-2c7a-4f3e-b8d6-91a4c2e07f58',
      phone: '260763456039',
      failure_code: 'PAYMENT_NOT_APPROVED',
      created_at: at(-3_600_000),
    }),
  ],
}

export const fixtureStats: ConsoleStats = {
  completed_today: 14,
  failed_today: 5,
  open_now: 1,
  needs_attention: 0,
  success_rate_7d: 0.7368,
  median_seconds_to_final: 3.9,
  total_credited: [{ currency: 'ZMW', amount_minor: 48250 }],
}

export function fixtureDetail(status: TopupStatus = 'COMPLETED'): ConsoleTopupDetail {
  const topup = fixtureTopup(status, { phone: '2607****6789' })
  return {
    topup,
    events: fixtureEvents(topup),
    ledger_entry:
      status === 'COMPLETED'
        ? { id: '9a7d1e44-0c2b-4d8f-a6e3-27b51f0c9d82', kind: 'topup_credit', amount_minor: 2500, created_at: at(3871) }
        : null,
    outbox:
      status === 'COMPLETED'
        ? [
            {
              id: 'o1',
              topic: 'receipt.issued',
              label: 'Receipt issued',
              dedupe_key: `receipt.issued:${topup.deposit_id}`,
              attempts: 1,
              created_at: at(3871),
              delivered_at: at(15_200),
            },
            {
              id: 'o2',
              topic: 'voip_credit.granted',
              label: 'Calling credit granted',
              dedupe_key: `voip_credit.granted:${topup.deposit_id}`,
              attempts: 0,
              created_at: at(3871),
              delivered_at: null,
            },
          ]
        : [],
  }
}

export const fixtureRuns: ReconcileRun[] = [
  {
    id: 'r1',
    trigger: 'schedule',
    started_at: at(60_000),
    finished_at: at(60_420),
    checked: 1,
    settled: 0,
    still_open: 1,
    failed_not_found: 0,
    errors: 0,
  },
  {
    id: 'r2',
    trigger: 'manual',
    started_at: at(0),
    finished_at: at(180),
    checked: 0,
    settled: 0,
    still_open: 0,
    failed_not_found: 0,
    errors: 0,
  },
]

export const fixtureHealth: Health = {
  ok: true,
  db: true,
  pawapay_configured: true,
  signature_required: false,
  commit: '0d7b5ce',
}
