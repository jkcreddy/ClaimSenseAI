"""
Tests for deterministic math tools and policy manual retriever.
Validates:
  - Lesser-of rule capping by ACV (CLS-COLL-001 / DEC-002)
  - Deductible application and non-negative net payable floors (CLS-DED-004)
  - Total loss ratio evaluation at 75% threshold (CLS-TL-005)
  - Senior adjuster authorization requirement at >$25k (CLS-PAY-012)
  - Closed-book policy retrieval and citation verification (CLS-001..012 / DEC-004)
  - MCP door tool integration
"""
import pytest
from src.tools.math_tools import compute_settlement_payout
from src.tools.policy_retriever import (
    get_clause,
    list_all_clauses,
    search_policy_clauses,
    validate_citations,
)
from src.mcp.server import GovernedMCPDoor
from src.mcp.auth_context import set_jwt, reset_jwt
from src.api.auth import _issue_token


# ── Deterministic Math Tests ──────────────────────────────────────────────────

def test_compute_payout_standard_collision():
    """Under-ACV repair: net payable = estimate - deductible."""
    res = compute_settlement_payout(
        claimed_amount=3200.0,
        vehicle_acv=24500.0,
        deductible=500.0,
        claim_id="CLM-TEST-001",
    )
    assert res["net_payable"] == 2700.0
    assert res["capped_by_acv"] is False
    assert res["is_total_loss_candidate"] is False
    assert res["senior_approval_required"] is False
    assert res["deductible_applied"] == 500.0
    assert res["claimed_amount"] == 3200.0


def test_compute_payout_lesser_of_acv_cap():
    """
    DEC-002 test case: estimate ($21,400) > ACV ($16,800).
    Payment MUST be capped at ACV ($16,800) - deductible ($500) = $16,300.
    Must NOT calculate $21,400 - $500 = $20,900.
    """
    res = compute_settlement_payout(
        claimed_amount=21400.0,
        vehicle_acv=16800.0,
        deductible=500.0,
        claim_id="CLM-2024-1946",
    )
    assert res["net_payable"] == 16300.0
    assert res["capped_by_acv"] is True
    assert res["net_loss_before_deductible"] == 16800.0


def test_compute_payout_deductible_exceeds_loss():
    """Net payable must floor at 0.0, never negative."""
    res = compute_settlement_payout(
        claimed_amount=350.0,
        vehicle_acv=12000.0,
        deductible=500.0,
    )
    assert res["net_payable"] == 0.0
    assert res["capped_by_acv"] is False


def test_compute_payout_total_loss_threshold():
    """Repair estimate / ACV >= 75% flags total loss candidate (CLS-TL-005)."""
    # 75% exactly
    res_exact = compute_settlement_payout(claimed_amount=7500.0, vehicle_acv=10000.0, deductible=500.0)
    assert res_exact["total_loss_ratio"] == 0.75
    assert res_exact["is_total_loss_candidate"] is True

    # 85% (severe damage)
    res_over = compute_settlement_payout(claimed_amount=17000.0, vehicle_acv=20000.0, deductible=500.0)
    assert res_over["total_loss_ratio"] == 0.85
    assert res_over["is_total_loss_candidate"] is True

    # 74.9% (below threshold)
    res_under = compute_settlement_payout(claimed_amount=7490.0, vehicle_acv=10000.0, deductible=500.0)
    assert res_under["total_loss_ratio"] == 0.749
    assert res_under["is_total_loss_candidate"] is False


def test_compute_payout_senior_approval_gate():
    """Net payable > $25,000 requires senior approval (CLS-PAY-012)."""
    # High-value claim ($28,000 payout)
    res_high = compute_settlement_payout(claimed_amount=29000.0, vehicle_acv=50000.0, deductible=1000.0)
    assert res_high["net_payable"] == 28000.0
    assert res_high["senior_approval_required"] is True

    # Under threshold ($25,000 exactly)
    res_edge = compute_settlement_payout(claimed_amount=26000.0, vehicle_acv=50000.0, deductible=1000.0)
    assert res_edge["net_payable"] == 25000.0
    assert res_edge["senior_approval_required"] is False


def test_compute_payout_idempotency_and_depreciation():
    """Depreciation decreases loss before ACV comparison; idempotency preserved."""
    key = "IDEM-TEST-KEY-42"
    res = compute_settlement_payout(
        claimed_amount=6000.0,
        vehicle_acv=10000.0,
        deductible=500.0,
        depreciation=1000.0,
        idempotency_key=key,
    )
    # net loss = 6000 - 1000 = 5000; payout = 5000 - 500 = 4500
    assert res["net_payable"] == 4500.0
    assert res["depreciation_applied"] == 1000.0
    assert res["idempotency_key"] == key


# ── Policy Retriever Tests (Closed-Book DEC-004) ──────────────────────────────

def test_list_all_clauses_returns_twelve():
    """Governed policy manual must contain exactly the 12 canonical clauses."""
    clauses = list_all_clauses()
    assert len(clauses) == 12
    ids = {c["clause_id"] for c in clauses}
    expected = {
        "CLS-COLL-001", "CLS-COMP-002", "CLS-LIAB-003", "CLS-DED-004",
        "CLS-TL-005", "CLS-PER-006", "CLS-FRD-007", "CLS-INJ-008",
        "CLS-DOC-009", "CLS-EXC-010", "CLS-RNT-011", "CLS-PAY-012"
    }
    assert ids == expected


def test_get_clause_exact():
    c = get_clause("CLS-COLL-001")
    assert c is not None
    assert "Collision coverage" in c["title"]
    assert "actual cash value (ACV)" in c["text"]


def test_search_policy_clauses_by_peril():
    # Hail damage maps to Comprehensive
    hail_results = search_policy_clauses("severe hail damage")
    assert any(c["clause_id"] == "CLS-COMP-002" for c in hail_results)

    # T-bone collision maps to Collision
    tbone_results = search_policy_clauses("t-bone collision with truck")
    assert any(c["clause_id"] == "CLS-COLL-001" for c in tbone_results)

    # Liability only query
    liab_results = search_policy_clauses("liability only third party coverage")
    assert any(c["clause_id"] == "CLS-LIAB-003" for c in liab_results)

    # Total loss ratio search
    tl_results = search_policy_clauses("vehicle repair cost exceeds 75 percent")
    assert any(c["clause_id"] == "CLS-TL-005" for c in tl_results)


def test_validate_citations_valid_and_invalid():
    # Valid citations
    valid, bad = validate_citations(["CLS-COLL-001", "CLS-PAY-012"])
    assert valid is True
    assert len(bad) == 0

    # Section citations (e.g. 'CLS-COLL-001 §4.2')
    valid, bad = validate_citations(["CLS-COLL-001 §4.2", "CLS-DED-004"])
    assert valid is True

    # Hallucinated / non-existent citation (Closed-Book check)
    valid, bad = validate_citations(["CLS-COLL-001", "SECTION-99-EXCLUSION"])
    assert valid is False
    assert "SECTION-99-EXCLUSION" in bad

    # Empty list
    valid, bad = validate_citations([])
    assert valid is False


# ── MCP Governed Tool Integration Tests ───────────────────────────────────────

@pytest.fixture
def mcp_door():
    token = _issue_token("alice", "adjuster")
    ctx = set_jwt(token)
    yield GovernedMCPDoor()
    reset_jwt(ctx)


def test_mcp_lookup_policy_clause(mcp_door):
    res = mcp_door.lookup_policy_clause("CLS-TL-005")
    assert res.error is None
    assert res.data["clause_id"] == "CLS-TL-005"
    assert res.caller_id == "alice"


def test_mcp_search_policy_manual(mcp_door):
    res = mcp_door.search_policy_manual("animal strike deer collision")
    assert res.error is None
    assert res.data["count"] > 0
    clause_ids = [c["clause_id"] for c in res.data["clauses"]]
    assert "CLS-COMP-002" in clause_ids


def test_mcp_compute_payout(mcp_door):
    res = mcp_door.compute_payout(
        claimed_amount=8800.0,
        vehicle_acv=38200.0,
        deductible=500.0,
        claim_id="CLM-2024-2019",
    )
    assert res.error is None
    assert res.data["net_payable"] == 8300.0
    assert res.data["claim_id"] == "CLM-2024-2019"
    assert res.caller_id == "alice"

