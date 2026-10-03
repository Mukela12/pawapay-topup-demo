# Ringwise: pawaPay wallet top-up on Flask, PostgreSQL and React

**Live demo:** https://web-production-b64c0.up.railway.app

Ringwise is a made-up brand that sells VoIP calling credit. In this demo a customer tops up their
Ringwise wallet with mobile money through the pawaPay Merchant API v2 sandbox, and an operations
console shows each step of every payment as it happens: the initiate response, callbacks,
status checks, reconciliation runs, the ledger entry and outbox deliveries. The backend is Flask
and PostgreSQL, the frontend is React, and one Railway service serves both. Elastic Beanstalk
deploy files are included too.

## What this proves

- **One guarded transition.** Every result from pawaPay goes through a single function,
  `apply_result`: the initiate response, a callback, a status check, reconciliation and a console
  replay. It runs `UPDATE ... WHERE status IN (open states) RETURNING` in one transaction. The
  ledger has a unique key on `(topup_id, kind)`, so a duplicate or racing callback can never
  credit twice. A COMPLETED amount that doesn't match, compared as `Decimal`, is held for review
  and nothing is credited.
- **Verified callbacks.** The backend checks RFC 9421 HTTP message signatures, with Content-Digest
  computed over the raw bytes and ECDSA P-256 keys fetched from pawaPay. It accepts both the DER
  signatures pawaPay actually sends and raw `r||s`. Unsigned callbacks are only a hint: the
  backend confirms them with `GET /v2/deposits/{id}` before changing anything. In the console,
  "Send forged callback" shows a forgery being rejected with 401 and nothing changing.
- **Reconciliation.** A scheduled job settles top-ups whose callback never arrived by asking
  pawaPay directly. A Postgres advisory lock means one process runs it, however many workers or
  instances there are. NOT_FOUND fails the top-up; PROCESSING stays open.
- **Ready for Elastic Beanstalk.** The repo includes a `Procfile`, a predeploy hook that runs
  migrations under an advisory lock, `.ebextensions` for the `/healthz` health check, a
  `cron.yaml` reconcile target for a worker tier, and the Secrets Manager mapping for the pawaPay
  token. All of it is documented in the Backend section below.

## What is real

- **The pawaPay sandbox API and its callbacks.** The backend makes real calls to
  `POST /v2/deposits`, `GET /v2/deposits/{id}`, `POST /v2/deposits/resend-callback/{id}`,
  `GET /v2/active-conf`, `POST /v2/predict-provider` and `GET /v2/public-key/http`. pawaPay's
  sandbox sends real callbacks to `/webhooks/pawapay`.
- **The PostgreSQL ledger.** Wallet balances have `CHECK (balance_minor >= 0)`. Ledger entries
  are append-only, enforced by a trigger. There is also a payment event log, a transactional
  outbox and reconcile run history, all created by one Alembic migration.
- **What the sandbox simulates.** pawaPay's sandbox simulates the customer's PIN approval. The
  test phone number decides the result: approved, PIN not approved, no wallet, declined,
  insufficient balance, or never settles. No real money moves.

## What production adds

- **Auth.** Real customer sign-in, which replaces the anonymous per-browser session, and an
  authenticated, role-checked operations console. The demo console has no login.
- **Real customers.** Wallets belong to existing customer accounts, and the outbox relay posts
  to the billing and softswitch systems (deduped on `dedupe_key`) instead of writing a log line.
- **An FX policy.** Deposits settle in local currency (ZMW, KES, GHS and so on). Production needs
  a rule for converting them into calling credit: the rate source, the spread, rounding, and when
  the rate is locked.
- **Alerts.** Held top-ups (NEEDS_ATTENTION), reconciliation errors, rejected signatures and an
  outbox backlog should page someone, for example through CloudWatch alarms or Slack. The demo
  only shows them in the console.
- **Production pawaPay settings.** Production credentials, the production callback URL, and
  signed callbacks switched on with `PAWAPAY_REQUIRE_SIGNATURE=true`.

## Run it locally

You need Python 3.11 or newer, Node 22 and PostgreSQL 16.

```
# Backend (API on port 5001)
python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
createdb ringwise_dev
cp backend/.env.example backend/.env       # PAWAPAY_API_TOKEN can stay empty
cd backend
.venv/bin/python scripts/migrate.py
FLASK_APP=wsgi.py .venv/bin/flask run --port 5001

# Frontend, in a second terminal (Vite dev server, proxies /api, /webhooks and /healthz to 5001)
cd frontend
npm ci
npm run dev                                # http://localhost:5173
```

To run it the way production does, as one process: build the frontend with `npm run build` in
`frontend/`. Then, from `backend/`, run
`.venv/bin/gunicorn wsgi:app --bind 127.0.0.1:5001 --workers 2 --threads 4` and open
http://127.0.0.1:5001. Flask serves `frontend/dist`. Client routes such as `/wallet` and
`/console/topups/<id>` return `index.html`, `/assets/*` gets long-lived caching, and unknown
`/api/*` paths return a JSON 404.

Without `PAWAPAY_API_TOKEN` everything still runs: the wallet says top-ups are switched off, and
the console works read-only. With a sandbox token, top-ups go to pawaPay. Callbacks can only reach
your machine through a public HTTPS tunnel registered in the sandbox dashboard. Without one,
reconciliation still settles each top-up once it has been open for `RECONCILE_AFTER_SECONDS` (60 in
`.env.example`).

Checks: `createdb ringwise_test`, then `.venv/bin/python -m pytest` in `backend/` (95 tests), and
`npm run build` in `frontend/` (type check plus production build).

## Deploying to Railway

- `Dockerfile` (repo root) has two stages. Stage 1, `node:22-slim`, runs `npm ci && npm run build`
  in `frontend/`. Stage 2, `python:3.12-slim`, installs `backend/requirements.txt`, copies
  `backend/` and the built `frontend/dist`, and runs as an unprivileged user. It starts with
  `python scripts/migrate.py && gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 30`.
- `railway.json` sets the builder to `DOCKERFILE` and the health check to `/healthz`.
  `.dockerignore` and `.railwayignore` keep `.venv`, `node_modules`, build output, caches and
  `.env` files out of the upload.
- Variables: `DATABASE_URL` (a reference to the Railway Postgres service), `SECRET_KEY`,
  `PAWAPAY_API_TOKEN`, `PAWAPAY_BASE_URL`, `PAWAPAY_REQUIRE_SIGNATURE`,
  `RECONCILE_AFTER_SECONDS=60`, `INTERNAL_TOKEN`, `PUBLIC_BASE_URL`, and optionally `GIT_COMMIT`.
  `SECRET_KEY` must be set: both gunicorn workers sign the session cookie with it.
- Register `https://web-production-b64c0.up.railway.app/webhooks/pawapay` as the deposit
  callback URL in the pawaPay sandbox dashboard.

## Repository layout

```
Dockerfile  railway.json  .dockerignore  .railwayignore  .gitignore
backend/    Flask app, Alembic migration, pytest suite, Elastic Beanstalk files
frontend/   Vite + React 19 + TypeScript + Tailwind v4 (wallet and operations console)
```

## Backend

Flask 3 + SQLAlchemy 2 (Flask-SQLAlchemy 3.1) + PostgreSQL, talking to the pawaPay Merchant API v2
sandbox. The code lives in `backend/`:

```
app/__init__.py            create_app() factory, ProxyFix, blueprints, lazy scheduler start
app/config.py              environment-only configuration (no secret defaults)
app/models.py              customers, wallet_accounts, topups, ledger_entries, payment_events, outbox, reconcile_runs
app/money.py               minor units, pawaPay amount strings, Decimal comparison
app/pawapay/client.py      httpx client: connect 5s / read 15s, typed errors, 5 min active-conf cache, 1 h key cache
app/pawapay/signatures.py  RFC 9421 signature base + Content-Digest + ECDSA (DER and raw r||s)
app/services/topups.py     create_topup, initiate, apply_result (the guarded transition), check_and_apply
app/services/callbacks.py  the callback handler shared by the webhook route and the console actions
app/services/reconcile.py  reconcile_once()
app/services/outbox.py     relay_once()
app/scheduler.py           APScheduler jobs behind pg_try_advisory_lock
app/api/*.py               customer, console, webhooks, internal (healthz, EB cron target)
app/spa.py                 serves frontend/dist (index.html for client routes, /assets with MIME types)
migrations/                Alembic (Flask-Migrate), one hand-edited initial revision
scripts/migrate.py         advisory-locked `flask db upgrade`
```

### Running it locally

```
python3.11 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
createdb ringwise_dev
cp backend/.env.example backend/.env          # PAWAPAY_API_TOKEN can stay empty
cd backend
.venv/bin/python scripts/migrate.py
FLASK_APP=wsgi.py .venv/bin/flask run --port 5001
```

Without `PAWAPAY_API_TOKEN` the app still boots: `/healthz` reports `pawapay_configured: false`,
`/api/config` serves a static Zambia catalogue, the customer top-up endpoints answer 503 with a
clear message, and the console works read-only.

For pawaPay to reach a local machine you need a public HTTPS URL (a tunnel) registered as the
deposit callback URL in the sandbox dashboard. The deployed demo uses
`https://<host>/webhooks/pawapay`.

### The guarded transition

Every result from pawaPay goes through one function, `apply_result(deposit_id, status, data,
source)`, no matter where it came from: the initiate response, a callback, a status check, the
reconciler or a console replay. It runs in one database transaction:

1. `UPDATE topups SET status = :new ... WHERE deposit_id = :id AND status IN (<allowed>) RETURNING status`.
   For final results the allowed set is the open states (CREATED, ACCEPTED, PROCESSING,
   IN_RECONCILIATION). Open states only move forward, so a late ACCEPTED can never undo a
   COMPLETED.
2. No row returned means the top-up is already final or held. The event is stored as
   `duplicate_ignored` and the balance is not touched. One exception: COMPLETED for a top-up
   already marked FAILED or REJECTED is a conflict, not a duplicate, because the customer may
   have paid. It moves to NEEDS_ATTENTION (`LATE_COMPLETED_AFTER_FAILURE`, `held_for_review`),
   keeps the original failure code in the message, and is never credited automatically.
3. COMPLETED: the amount is compared as `Decimal` (pawaPay returns `"100.00"` while it only
   accepts `"100"` on the way in) and the currency must match. A mismatch moves the top-up to
   NEEDS_ATTENTION (`held_for_review`) and credits nothing. On a match the same transaction
   inserts a `ledger_entries` row, adds the amount to `wallet_accounts.balance_minor`, and writes
   two `outbox` rows (`receipt.issued`, `voip_credit.granted`, deduped on `topic:deposit_id`).
4. FAILED and REJECTED store the failure code and message.
5. PROCESSING and IN_RECONCILIATION update the status and stay open.
6. Any status we do not recognise becomes NEEDS_ATTENTION, as pawaPay's own guidance suggests.
   There is no `else: fail()`.

Why this is safe under concurrency: PostgreSQL runs at Read Committed. When two writers race
(a callback and the reconciler, or a callback delivered twice), the second UPDATE waits for the
first to commit, then re-evaluates its WHERE clause against the new row, matches nothing and
returns no row. The database backs this up: `UNIQUE (topup_id, kind)` on `ledger_entries` makes
a second credit impossible, and an IntegrityError on that key is reported as
`duplicate_ignored`, never as a 500. `wallet_accounts` has `CHECK (balance_minor >= 0)`.
`ledger_entries` is append-only: a PL/pgSQL trigger raises on UPDATE or DELETE, so corrections
must be new entries.

The depositId is a uuid4 generated by the server and committed with the CREATED row before
pawaPay is called, and no HTTP call to pawaPay ever happens while a transaction is open. If
`POST /v2/deposits` times out or returns HTTP 500 the outcome is unknown, not failed: the
backend immediately calls `GET /v2/deposits/{id}`, applies a FOUND result, and only marks the
top-up FAILED on `NOT_FOUND`. After HTTP 500 (pawaPay answered) or a connect failure (the request
never left) that NOT_FOUND fails it at once. After a read timeout the request may still be in
flight at pawaPay, so an immediate NOT_FOUND leaves the top-up open and the reconciler applies the
NOT_FOUND rule once `RECONCILE_AFTER_SECONDS` have passed. REJECTED arrives as HTTP 200, so the
code branches on the body `status`, not the HTTP code alone.

### Callbacks and signature verification

`POST /webhooks/pawapay` reads the raw request bytes (`request.get_data(cache=True)`) before it
parses anything, because the Content-Digest is computed over those exact bytes.

When `Signature`, `Signature-Input` and `Content-Digest` are present:

1. The body is hashed with the algorithm named in `Content-Digest` (sha-256 or sha-512) and
   compared in constant time.
2. The RFC 9421 signature base is rebuilt from the components listed in `Signature-Input`,
   for pawaPay `"@method" "@authority" "@path" "signature-date" "content-digest" "content-type"`,
   one `"name": value` line each, ending with `"@signature-params": ` followed by the
   `Signature-Input` value exactly as received (so the `alg`, `keyid`, `created`, `expires`
   order is preserved). A unit test checks the builder against the example base in pawaPay's
   signatures guide character for character.
3. `@authority` is the host pawaPay called: `X-Forwarded-Host` when a proxy sets it (Railway
   does), otherwise `Host`. If this is wrong every signature fails, so check it first behind a
   new load balancer.
4. The signature must cover `content-digest`, must not be expired (`expires`, with 30 s of clock
   skew), and its `keyid` must match a key from `GET /v2/public-key/http` (cached for an hour,
   refreshed on an unknown keyid to survive key rotation, at most once a minute per process so
   callbacks with random keyids cannot make the server call pawaPay once per request).
5. The signature is verified with `cryptography` (ECDSA P-256 with SHA-256 for
   `ecdsa-p256-sha256`; P-384, RSA-PSS-SHA512 and RSA-PKCS1v1.5-SHA256 are also handled).

The DER note: RFC 9421 defines ECDSA signatures as the raw 64-byte `r||s` pair, but pawaPay's
published samples decode to 70 to 72 byte ASN.1 DER sequences (the default output of Node's
`crypto.sign`). Strict RFC 9421 libraries such as `http-message-signatures` reject them with
"Unexpected signature length". The verifier therefore tries DER first and, for a 64-byte value,
converts raw `r||s` with `encode_dss_signature` and tries again.

`PAWAPAY_REQUIRE_SIGNATURE` (default `false`, switch it on after enabling signed callbacks in the
pawaPay dashboard):

- `true`: a missing or invalid signature gets HTTP 401 and a `rejected` event. Nothing changes.
- `false`: an unsigned (or badly signed) callback is treated as a hint. The handler calls
  `GET /v2/deposits/{id}` and applies what the API says, including FAILED, never what the body
  says. This closes the hole where a forged unsigned FAILED callback could block a real credit.

Every callback is stored in `payment_events` with the raw body and only these headers:
`content-type`, `content-digest`, `signature-input` and presence flags. Authorization headers and
signature values are never stored. Applied, duplicate, held and no-change callbacks all get HTTP
200 so pawaPay stops retrying; a callback for a depositId we do not know is acknowledged with 200.
If the status check needed in hint mode fails, the handler answers 503 so pawaPay retries.
Bodies are untrusted until verified: JSON nested deeper than 32 levels is treated as malformed
(400), and U+0000, which PostgreSQL text and JSONB cannot store, is replaced with U+FFFD before
anything is stored or applied. The digest and signature are always checked over the original
bytes.

The console's "Send forged callback" builds a well-formed COMPLETED callback with a correct
Content-Digest, signs it with a throwaway P-256 key under pawaPay's key id, and pushes it through
the same handler with signature checking forced on: it is rejected with 401 and nothing changes.
"Replay last callback" re-feeds the stored raw body through the handler; the expected answer is
`duplicate_ignored` with the balance unchanged. Stored callbacks keep no signature value, and a
replayed signature would be expired anyway, so a replay always runs in hint mode (confirmed with
`GET /v2/deposits`, signature recorded as `not_checked`), including when
`PAWAPAY_REQUIRE_SIGNATURE=true`. Outside callers without a valid signature still get 401.

### Reconciliation

`reconcile_once(trigger)` settles top-ups whose callback never arrived:

1. A short transaction claims open top-ups older than `RECONCILE_AFTER_SECONDS` (default 900,
   the demo uses 60) with `FOR UPDATE SKIP LOCKED`, stamps `last_checked_at` and `check_count`,
   and commits.
2. `GET /v2/deposits/{id}` is called for each one with no transaction open.
3. Each answer goes through `apply_result` in its own transaction. `NOT_FOUND` means pawaPay
   never received the deposit, so it becomes FAILED with `NOT_FOUND_AT_PROVIDER`. PROCESSING and
   IN_RECONCILIATION stay open for the next run (pawaPay says no action is needed for
   IN_RECONCILIATION).
4. A `reconcile_runs` row records checked, settled, still open, failed as not found, and errors.

In process, APScheduler runs reconciliation every 60 s and the outbox relay every 15 s. Every
gunicorn worker starts a scheduler (lazily, on its first request, so `flask db` commands never
start one), and each job first takes `pg_try_advisory_lock` on its own AUTOCOMMIT connection.
Only the lock holder runs the job, so several workers and several instances never double-run it.

The outbox relay claims undelivered rows with `FOR UPDATE SKIP LOCKED`, "delivers" them (a log
line here; in production an HTTP call to the billing or softswitch system, which should dedupe on
`dedupe_key`) and stamps `delivered_at`. The console shows them as "Receipt issued" and "Calling
credit granted".

### Limits and the demo session

`POST /api/session` creates an anonymous customer and stores its id in Flask's signed session
cookie (HttpOnly, SameSite=Lax, Secure unless `PUBLIC_BASE_URL` is a plain-http loopback address
such as the `http://127.0.0.1:5001` in `.env.example`, or `PUBLIC_BASE_URL` is unset and
`FLASK_DEBUG` is on; `SESSION_COOKIE_SECURE` overrides it). Creating a top-up locks
the customer row (`SELECT ... FOR UPDATE`) while it checks the rules, so they hold across workers:
one open top-up per customer (lapses after `OPEN_TOPUP_LOCK_SECONDS`, default 600), at most
`PHONE_HOURLY_CAP` (5) per phone number per hour (serialised with a per-number advisory lock) and
`CUSTOMER_DAILY_CAP` (20) per customer per day. An `Idempotency-Key` header with the same body
returns the same top-up without calling pawaPay again; the same key with a different body is a 422.

### Tests

```
createdb ringwise_test
cd backend
.venv/bin/python -m pytest
```

The suite (95 tests) runs against `postgresql:///ringwise_test` (override with
`TEST_DATABASE_URL`). The session fixture drops and recreates the schema and runs the real Alembic
migration, so the constraints and the trigger under test are the production ones. pawaPay is
mocked with respx; nothing touches the network. Covered: amount formatting (no trailing zeros,
NONE decimals), exactly-once crediting (sequential, eight racing threads, and a forced row-lock
race), amount and currency mismatch holds, FAILED and unknown statuses, the append-only trigger,
signed callbacks with a locally generated P-256 key over a hand-built RFC 9421 base in DER (valid,
tampered body, wrong key, expired, raw `r||s`, `X-Forwarded-Host`), hint mode, duplicate
callbacks, forged and replayed callbacks (also with signatures required), a late COMPLETED after
a FAILED, NUL characters and absurd nesting in callback bodies, forced public-key refresh limits,
initiate REJECTED, initiate HTTP 500, read timeouts and connect failures followed by FOUND or
NOT_FOUND, reconciliation outcomes, the EB worker localhost check, the session cookie Secure
default, the open top-up rule, caps, idempotency keys, the config fallback, the scheduler lock
and the SPA routes.

### Deploying to AWS Elastic Beanstalk

These files are included and documented but this demo is not deployed to EB (it runs on Railway).
They target the Python platform on Amazon Linux 2023 (Python 3.11 or newer branch).

**Bundle.** The backend directory is the bundle root. `backend/scripts/build_eb_bundle.sh` builds
the frontend, copies `backend/` (without `.venv`, tests and `.env`) and `frontend/dist` into
`build/eb/`, and zips it to `build/ringwise-eb.zip`. `app/spa.py` finds the React build at
`frontend/dist` inside the bundle, so the same instance serves the API and the SPA. Deploy the zip
with `eb deploy` (after `eb init`) or upload it as a new application version.

**Procfile.** `web: gunicorn wsgi:app --bind :8000 --workers 3 --threads 4 --timeout 30`. EB's
nginx proxies to port 8000. Workers times `pool_size + max_overflow` (5 + 5) times instances must
stay under the RDS `max_connections`.

**Migrations.** `.platform/hooks/predeploy/01_migrate.sh` runs on every instance before the new
version serves traffic. It uses the platform venv (`/var/app/venv/staging-*/bin/python`, not the
system Python) and runs `scripts/migrate.py`, which takes `pg_advisory_lock` on an AUTOCOMMIT
connection and runs `flask db upgrade`. Instances deploying together take turns and every run after
the first is a no-op. This avoids `leader_only` container commands, which do not run on scale-out
and whose leader instance can disappear. With Immutable or rolling deploys, old code runs against
the new schema for a while, so write migrations expand then contract.

**Options.** `.ebextensions/01_options.config` sets the health check path to `/healthz` (ALB
process and application health URL) and enhanced health. It contains no values for environment
variables, only their names in comments. If you deploy with the Immutable policy, apply option
changes as a separate configuration update, because EB refuses an immutable deployment whose bundle
changes option settings.

**Secrets.** Keep `PAWAPAY_API_TOKEN`, `SECRET_KEY`, `DATABASE_URL` and `INTERNAL_TOKEN` in AWS
Secrets Manager (or SSM Parameter Store) and map them into the environment with the
`aws:elasticbeanstalk:application:environmentsecrets` namespace, which needs a platform version
released on or after 2025-03-26:

```
aws elasticbeanstalk update-environment --environment-name ringwise-prod --option-settings \
  Namespace=aws:elasticbeanstalk:application:environmentsecrets,OptionName=PAWAPAY_API_TOKEN,Value=arn:aws:secretsmanager:REGION:ACCOUNT:secret:ringwise/pawapay-token-AbCdEf
```

The instance profile needs `secretsmanager:GetSecretValue` (plus `kms:Decrypt` for a customer
managed key). Secrets are read when an instance boots, so after rotating the pawaPay token run
`aws elasticbeanstalk restart-app-server` (or update the environment). Plain settings such as
`PAWAPAY_BASE_URL`, `PAWAPAY_REQUIRE_SIGNATURE`, `RECONCILE_AFTER_SECONDS`, `PUBLIC_BASE_URL` and
`TRUST_PROXY_HOPS` go in normal environment properties.

**Scheduling.** Two options:

- Web tier only: leave `SCHEDULER_ENABLED` at its default. The advisory lock makes sure one
  process runs each job, however many instances there are.
- With a worker tier: set `SCHEDULER_ENABLED=false` on the web tier, deploy the same bundle to a
  worker environment with `EB_WORKER=true`, and `cron.yaml` (`reconcile`, `/internal/reconcile`,
  every 5 minutes) makes the SQS daemon POST to localhost, which `/internal/reconcile` accepts
  without a token on a worker. "Localhost" means the TCP peer, the ProxyFix-resolved client and
  every `X-Forwarded-For` entry are all loopback, so a caller who reaches gunicorn directly
  cannot pass by sending `X-Forwarded-For: 127.0.0.1`, and behind nginx (which appends the real
  peer) only local requests pass. Leader election means one POST per tick. The endpoint answers 200
  even when pawaPay is not configured so the daemon does not retry it. From anywhere else the
  endpoint needs the `X-Internal-Token` header.

**pawaPay side.** Register `https://<your-domain>/webhooks/pawapay` as the deposit callback URL in
each pawaPay dashboard (sandbox and production are separate), terminate HTTPS on the ALB with an
ACM certificate (pawaPay requires a trusted CA), turn on signed callbacks and then set
`PAWAPAY_REQUIRE_SIGNATURE=true`. As defence in depth, an AWS WAF rule on the ALB can block requests
to `/webhooks/pawapay` that do not come from pawaPay's published callback IPs; do not restrict the
ALB security group, because customers use the same load balancer. Behind the ALB and nginx,
`TRUST_PROXY_HOPS=1` makes ProxyFix trust one proxy hop for the client IP and scheme; confirm the
hop count on an instance before relying on client IPs.
