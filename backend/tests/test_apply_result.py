"""The guarded transition: exactly-once crediting, mismatch holds, append-only ledger."""
import threading
import uuid

import pytest
from conftest import deposit_data, make_topup
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import LedgerEntry, Outbox, PaymentEvent, Topup, WalletAccount
from app.services.topups import apply_result


def balance(customer_id, currency="ZMW"):
    value = db.session.execute(
        select(WalletAccount.balance_minor).where(
            WalletAccount.customer_id == customer_id, WalletAccount.currency == currency
        )
    ).scalar_one_or_none()
    db.session.rollback()
    return value or 0


def count(model, *where):
    n = db.session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()
    db.session.rollback()
    return n


def status_of(topup):
    s = db.session.execute(select(Topup.status).where(Topup.id == topup.id)).scalar_one()
    db.session.rollback()
    return s


def test_completed_twice_credits_once(ctx):
    t = make_topup(amount_minor=1050)
    data = deposit_data(t, amount="10.50")
    first = apply_result(t.deposit_id, "COMPLETED", data, "callback")
    second = apply_result(t.deposit_id, "COMPLETED", data, "callback")
    assert first.outcome == "applied" and first.credited
    assert second.outcome == "duplicate_ignored" and not second.credited
    assert status_of(t) == "COMPLETED"
    assert count(LedgerEntry, LedgerEntry.topup_id == t.id) == 1
    assert balance(t.customer_id) == 1050
    assert count(Outbox) == 2
    keys = set(db.session.execute(select(Outbox.dedupe_key)).scalars())
    assert keys == {f"receipt.issued:{t.deposit_id}", f"voip_credit.granted:{t.deposit_id}"}
    assert count(PaymentEvent, PaymentEvent.topup_id == t.id) == 2


def test_amount_formats_are_compared_as_decimal(ctx):
    t = make_topup(amount_minor=1000)
    result = apply_result(t.deposit_id, "COMPLETED", deposit_data(t, amount="10.00"), "callback")
    assert result.outcome == "applied"
    assert balance(t.customer_id) == 1000


def test_concurrent_completed_from_two_threads_credits_exactly_once(app):
    with app.app_context():
        t = make_topup(amount_minor=2500)
        db.session.remove()
    data = deposit_data(t, amount="25")
    n_threads = 8
    barrier = threading.Barrier(n_threads)
    outcomes, errors = [], []

    def worker():
        with app.app_context():
            try:
                barrier.wait()
                outcomes.append(apply_result(t.deposit_id, "COMPLETED", data, "callback").outcome)
            except Exception as exc:  # pragma: no cover - surfaced by the assert below
                errors.append(exc)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    assert outcomes.count("applied") == 1
    assert outcomes.count("duplicate_ignored") == n_threads - 1
    with app.app_context():
        assert count(LedgerEntry, LedgerEntry.topup_id == t.id) == 1
        assert balance(t.customer_id) == 2500


def test_amount_mismatch_is_held_and_not_credited(ctx):
    t = make_topup(amount_minor=1000)
    result = apply_result(t.deposit_id, "COMPLETED", deposit_data(t, amount="100.00"), "callback")
    assert result.outcome == "held_for_review"
    assert status_of(t) == "NEEDS_ATTENTION"
    assert count(LedgerEntry) == 0
    assert balance(t.customer_id) == 0
    row = db.session.get(Topup, t.id, populate_existing=True)
    assert row.failure_code == "AMOUNT_MISMATCH"


def test_currency_mismatch_is_held(ctx):
    t = make_topup(amount_minor=1000)
    result = apply_result(
        t.deposit_id, "COMPLETED", deposit_data(t, amount="10", currency="KES"), "callback"
    )
    assert result.outcome == "held_for_review"
    assert db.session.get(Topup, t.id, populate_existing=True).failure_code == "CURRENCY_MISMATCH"
    assert count(LedgerEntry) == 0


def test_failed_path_stores_failure_and_does_not_credit(ctx):
    t = make_topup()
    data = deposit_data(
        t,
        status="FAILED",
        failureReason={"failureCode": "PAYMENT_NOT_APPROVED", "failureMessage": "PIN timed out"},
    )
    result = apply_result(t.deposit_id, "FAILED", data, "callback")
    assert result.outcome == "applied"
    row = db.session.get(Topup, t.id, populate_existing=True)
    assert row.status == "FAILED"
    assert row.failure_code == "PAYMENT_NOT_APPROVED"
    assert row.failure_message == "PIN timed out"
    assert row.finalized_at is not None
    assert count(LedgerEntry) == 0
    # A repeated FAILED is a plain duplicate.
    again = apply_result(t.deposit_id, "FAILED", data, "callback")
    assert again.outcome == "duplicate_ignored"
    assert count(LedgerEntry) == 0


def test_late_completed_after_failed_is_held_not_ignored(ctx):
    """Conflicting final states are not a duplicate: the customer may have paid. The top-up moves
    to NEEDS_ATTENTION (held_for_review) and is never credited automatically."""
    t = make_topup(amount_minor=1000)
    not_found = {"failureReason": {"failureCode": "NOT_FOUND_AT_PROVIDER", "failureMessage": "no record"}}
    assert apply_result(t.deposit_id, "FAILED", not_found, "status_check").outcome == "applied"

    late = apply_result(t.deposit_id, "COMPLETED", deposit_data(t, providerTransactionId="PP-LATE"), "callback")
    assert late.outcome == "held_for_review"
    assert late.status == "NEEDS_ATTENTION" and late.previous_status == "FAILED"
    assert late.credited is False
    row = db.session.get(Topup, t.id, populate_existing=True)
    assert row.status == "NEEDS_ATTENTION"
    assert row.failure_code == "LATE_COMPLETED_AFTER_FAILURE"
    assert "NOT_FOUND_AT_PROVIDER" in row.failure_message  # the original failure stays visible
    assert row.provider_txn_id == "PP-LATE"
    db.session.rollback()
    assert count(LedgerEntry) == 0 and count(Outbox) == 0
    assert balance(t.customer_id) == 0
    ev = db.session.get(PaymentEvent, late.event_id)
    assert ev.outcome == "held_for_review"
    db.session.rollback()

    # Once held, further COMPLETED results are duplicates and still credit nothing.
    again = apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    assert again.outcome == "duplicate_ignored"
    assert status_of(t) == "NEEDS_ATTENTION"
    assert count(LedgerEntry) == 0


def test_late_completed_after_rejected_is_held(ctx):
    t = make_topup(status="CREATED")
    apply_result(t.deposit_id, "REJECTED", {"failureReason": {"failureCode": "HTTP_400"}}, "initiate_response")
    late = apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "status_check")
    assert late.outcome == "held_for_review"
    assert status_of(t) == "NEEDS_ATTENTION"
    assert count(LedgerEntry) == 0


def test_late_completed_after_completed_is_still_a_duplicate(ctx):
    t = make_topup(amount_minor=1000)
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    assert apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback").outcome == "duplicate_ignored"
    # A FAILED after COMPLETED never un-credits or flags anything.
    assert apply_result(t.deposit_id, "FAILED", {}, "callback").outcome == "duplicate_ignored"
    assert status_of(t) == "COMPLETED"
    assert balance(t.customer_id) == 1000


def test_unknown_status_goes_to_needs_attention(ctx):
    t = make_topup()
    result = apply_result(t.deposit_id, "SOMETHING_NEW", {"status": "SOMETHING_NEW"}, "status_check")
    assert result.outcome == "held_for_review"
    row = db.session.get(Topup, t.id, populate_existing=True)
    assert row.status == "NEEDS_ATTENTION"
    assert row.failure_code == "UNKNOWN_STATUS"


def test_open_states_only_move_forward(ctx):
    t = make_topup(status="CREATED")
    assert apply_result(t.deposit_id, "PROCESSING", {}, "status_check").outcome == "applied"
    assert apply_result(t.deposit_id, "ACCEPTED", {}, "initiate_response").outcome == "no_change"
    assert status_of(t) == "PROCESSING"
    assert apply_result(t.deposit_id, "IN_RECONCILIATION", {}, "status_check").outcome == "applied"
    assert status_of(t) == "IN_RECONCILIATION"


def test_created_to_completed_is_legal(ctx):
    """A callback can beat the initiate response; CREATED -> COMPLETED must work."""
    t = make_topup(status="CREATED")
    result = apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    assert result.outcome == "applied"
    # The late initiate response must not move it back.
    assert apply_result(t.deposit_id, "ACCEPTED", {}, "initiate_response").outcome == "duplicate_ignored"
    assert status_of(t) == "COMPLETED"


def test_unknown_deposit_id_is_recorded_not_applied(ctx):
    dep = uuid.uuid4()
    result = apply_result(dep, "COMPLETED", {"depositId": str(dep)}, "callback")
    assert result.outcome == "no_change"
    assert count(PaymentEvent, PaymentEvent.deposit_id == dep) == 1


def test_ledger_trigger_blocks_update_and_delete(ctx):
    t = make_topup()
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    with pytest.raises(DBAPIError) as update_err:
        db.session.execute(text("UPDATE ledger_entries SET amount_minor = 999999"))
    db.session.rollback()
    assert "append-only" in str(update_err.value)
    with pytest.raises(DBAPIError) as delete_err:
        db.session.execute(text("DELETE FROM ledger_entries"))
    db.session.rollback()
    assert "append-only" in str(delete_err.value)
    assert count(LedgerEntry) == 1


def test_wallet_balance_cannot_go_negative(ctx):
    t = make_topup()
    apply_result(t.deposit_id, "COMPLETED", deposit_data(t), "callback")
    with pytest.raises(DBAPIError):
        db.session.execute(text("UPDATE wallet_accounts SET balance_minor = -1"))
    db.session.rollback()


def test_second_writer_waits_for_row_lock_then_sees_final_state(app, monkeypatch):
    """Force the race: writer A holds the row lock (sleeps inside the credit) while writer B's
    guarded UPDATE waits, re-evaluates the WHERE under Read Committed, and matches nothing."""
    import time as _time

    from app.services import topups as topups_mod

    with app.app_context():
        t = make_topup(amount_minor=700)
        db.session.remove()
    original = topups_mod._credit_wallet
    a_inside = threading.Event()

    def slow_credit(row, dep):
        a_inside.set()
        _time.sleep(0.5)
        original(row, dep)

    monkeypatch.setattr(topups_mod, "_credit_wallet", slow_credit)
    results = {}

    def run(name):
        with app.app_context():
            try:
                results[name] = apply_result(t.deposit_id, "COMPLETED", deposit_data(t, amount="7"), "callback")
            finally:
                db.session.remove()

    a = threading.Thread(target=run, args=("a",))
    a.start()
    assert a_inside.wait(5)
    started = _time.monotonic()
    run("b")  # blocks on the row lock until A commits
    waited = _time.monotonic() - started
    a.join()
    assert results["a"].outcome == "applied"
    assert results["b"].outcome == "duplicate_ignored"
    assert waited > 0.2
    with app.app_context():
        assert count(LedgerEntry, LedgerEntry.topup_id == t.id) == 1
        assert balance(t.customer_id) == 700
