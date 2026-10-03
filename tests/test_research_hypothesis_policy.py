from decimal import Decimal
import importlib.util

import pytest


def policy():
    assert importlib.util.find_spec("ai_quant_os.research_hypothesis_policy") is not None
    from ai_quant_os import research_hypothesis_policy

    return research_hypothesis_policy


@pytest.mark.parametrize(
    "closes, expected",
    [
        (tuple(Decimal(i) for i in range(1, 13)), (10, 10)),
        ((Decimal(1), Decimal(2)) * 6, (10, 0)),
        ((Decimal(1),) * 12, (0, 0)),
        (tuple(Decimal(i) for i in (1, 2, 2, 1, 2)), (1, 0)),
    ],
)
def test_direction_evidence_uses_only_consecutive_nonzero_return_pairs(closes, expected):
    assert policy().direction_counts(closes) == expected


@pytest.mark.parametrize(
    "pairs, same, expected",
    [
        (7, 7, None),
        (8, 6, "FOLLOW_PREVIOUS_CLOSE_DIRECTION"),
        (8, 2, "FADE_PREVIOUS_CLOSE_DIRECTION"),
        (8, 4, None),
    ],
)
def test_predeclared_screen_selects_test_rules_without_alpha_acceptance(pairs, same, expected):
    assert policy().research_rule(pairs, same) == expected


@pytest.mark.parametrize("pairs, same", [(-1, 0), (8, 9), (True, 0), (8, False)])
def test_invalid_direction_counts_are_refused(pairs, same):
    with pytest.raises(ValueError):
        policy().research_rule(pairs, same)
