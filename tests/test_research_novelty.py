from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from ai_quant_os.research_novelty import ResearchNoveltyReservations


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
CONTEXT_A = "agent-context:" + "a" * 64
CONTEXT_B = "agent-context:" + "b" * 64
SIG_A = "a" * 64
SIG_B = "b" * 64


def test_context_reservations_are_durable_and_exclude_other_contexts(tmp_path):
    path = tmp_path / "novelty.sqlite3"
    with ResearchNoveltyReservations(path) as reservations:
        assert (
            reservations.claim(CONTEXT_A, (SIG_A,), now=NOW, valid_until=NOW + timedelta(seconds=5))
            == ()
        )
    with ResearchNoveltyReservations(path) as reopened:
        assert reopened.active_signatures(NOW) == (SIG_A,)
        assert reopened.claim(
            CONTEXT_B, (SIG_A, SIG_B), now=NOW, valid_until=NOW + timedelta(seconds=5)
        ) == (SIG_A,)
        assert reopened.active_signatures(NOW) == (SIG_A,)


def test_same_context_can_retry_its_reservation_idempotently(tmp_path):
    with ResearchNoveltyReservations(tmp_path / "novelty.sqlite3") as reservations:
        expiry = NOW + timedelta(seconds=5)
        assert reservations.claim(CONTEXT_A, (SIG_A,), now=NOW, valid_until=expiry) == ()
        assert reservations.claim(CONTEXT_A, (SIG_A,), now=NOW, valid_until=expiry) == ()
        assert reservations.active_signatures(NOW) == (SIG_A,)


def test_expired_reservations_stop_suppressing_new_contexts(tmp_path):
    with ResearchNoveltyReservations(tmp_path / "novelty.sqlite3") as reservations:
        expiry = NOW + timedelta(seconds=5)
        reservations.claim(CONTEXT_A, (SIG_A,), now=NOW, valid_until=expiry)
        assert reservations.active_signatures(expiry) == ()
        assert (
            reservations.claim(
                CONTEXT_B, (SIG_A,), now=expiry, valid_until=expiry + timedelta(seconds=5)
            )
            == ()
        )


def test_concurrent_contexts_cannot_claim_the_same_signature(tmp_path):
    path = tmp_path / "novelty.sqlite3"
    with ResearchNoveltyReservations(path) as first, ResearchNoveltyReservations(path) as second:
        expiry = NOW + timedelta(seconds=5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(
                    lambda args: args[0].claim(args[1], (SIG_A,), now=NOW, valid_until=expiry),
                    ((first, CONTEXT_A), (second, CONTEXT_B)),
                )
            )
        assert sorted(outcomes) == [(), (SIG_A,)]


@pytest.mark.parametrize(
    "context_ref,signatures,expiry",
    [
        ("bad", (SIG_A,), NOW + timedelta(seconds=5)),
        (CONTEXT_A, ("not-a-digest",), NOW + timedelta(seconds=5)),
        (CONTEXT_A, (SIG_A, SIG_A), NOW + timedelta(seconds=5)),
        (CONTEXT_A, (SIG_A,) * 5, NOW + timedelta(seconds=5)),
        (CONTEXT_A, (SIG_A,), NOW + timedelta(seconds=6)),
    ],
)
def test_invalid_reservation_requests_are_rejected(tmp_path, context_ref, signatures, expiry):
    with ResearchNoveltyReservations(tmp_path / "novelty.sqlite3") as reservations:
        with pytest.raises(ValueError):
            reservations.claim(context_ref, signatures, now=NOW, valid_until=expiry)
        assert reservations.active_signatures(NOW) == ()
