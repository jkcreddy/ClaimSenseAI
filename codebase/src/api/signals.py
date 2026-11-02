"""
Deterministic signal layer (DEC-002).
Computes every figure the model will receive in its dossier.
No LLM calls here — pure Python, fully unit-testable.
"""
from datetime import date
from typing import Optional

# DEC-010: incident_type → loss_category mapping
_LOSS_CATEGORY_MAP = {
    "parking lot collision": "collision",
    "single-vehicle accident": "collision",
    "t-bone collision": "collision",
    "rear-end on highway": "collision",
    "hit and run": "collision",
    "low-speed collision": "collision",
    "side-swipe": "collision",
    "multi-vehicle pileup": "collision",
    "backing accident": "collision",
    "rollover": "collision",
    "underride accident": "collision",
    "hail damage": "comprehensive",
    "animal strike": "comprehensive",
    "flood damage": "comprehensive",
    "vandalism": "comprehensive",
    "theft recovery": "comprehensive",
    "storm damage": "comprehensive",
    "pothole damage": "collision",   # DEC-010: pothole is collision
    "garage fire": "comprehensive",
    "carjacking": "comprehensive",
}


def _loss_category(incident_type: str) -> str:
    return _LOSS_CATEGORY_MAP.get(incident_type.lower(), "unknown")


def _policy_in_force(claim: dict, policy: dict) -> bool:
    """Check incident_date falls within [policy_start, policy_end] and policy is Active."""
    if policy.get("policy_status") != "Active":
        return False
    try:
        incident = date.fromisoformat(claim["incident_date"])
        start = date.fromisoformat(policy["policy_start"])
        end = date.fromisoformat(policy["policy_end"])
        return start <= incident <= end
    except (ValueError, KeyError):
        return False


def _coverage_applies(loss_cat: str, policy: dict) -> bool:
    """Does the policy cover this loss category? (DEC-003 coverage gate)"""
    if policy.get("coverage_type") == "Liability Only":
        return False  # first-party physical damage not covered
    if loss_cat == "collision":
        return bool(policy.get("collision_coverage"))
    if loss_cat == "comprehensive":
        return bool(policy.get("comprehensive_coverage"))
    return False  # unknown loss category → flag_review


def _deductible(loss_cat: str, policy: dict) -> Optional[float]:
    """Pick deductible by loss category (DEC-010)."""
    if loss_cat == "collision":
        return policy.get("collision_deductible_usd")
    if loss_cat == "comprehensive":
        return policy.get("comprehensive_deductible_usd")
    return None


def _net_payable(estimate: float, acv: float, deductible: Optional[float]) -> float:
    """
    CLS-COLL-001 lesser-of rule + deductible (DEC-002):
    net = max(min(estimate, ACV) - deductible, 0)
    """
    if deductible is None:
        return 0.0
    return max(min(estimate, acv) - deductible, 0.0)


def compute_signals(claim: dict, policy: Optional[dict]) -> dict:
    """
    Returns a flat dict of all deterministic signals.
    This dict is passed to the model as the dossier and used by the rule engine.
    """
    estimate = claim["repair_estimate_usd"]
    acv = claim["vehicle_acv_usd"]
    loss_cat = _loss_category(claim["incident_type"])

    in_force = _policy_in_force(claim, policy) if policy else False
    coverage_ok = _coverage_applies(loss_cat, policy) if policy else False
    coverage_in_force = in_force and coverage_ok

    deductible = _deductible(loss_cat, policy) if policy else None
    net = _net_payable(estimate, acv, deductible) if coverage_in_force else 0.0
    tl_ratio = estimate / acv if acv > 0 else 0.0

    return {
        # Loss classification
        "loss_category": loss_cat,
        # Coverage
        "policy_in_force_on_incident_date": in_force,
        "coverage_applies": coverage_ok,
        "coverage_in_force": coverage_in_force,
        # Financials
        "repair_estimate_usd": round(estimate, 2),
        "vehicle_acv_usd": round(acv, 2),
        "deductible_usd": round(deductible, 2) if deductible is not None else None,
        "net_payable_usd": round(net, 2),
        "total_loss_ratio": round(tl_ratio, 4),
        "tl_ratio_pct": round(tl_ratio, 4),
        "senior_approval_required": net > 25_000,
        # Risk
        "fraud_risk_score": claim["fraud_risk_score"],
        "prior_claims_12mo": claim["prior_claims_12mo"],
        # Evidence
        "injuries_reported": claim["injuries_reported"],
        "police_report_filed": claim["police_report_filed"],
        "required_docs_complete": True,  # updated by Evidence Agent when live
    }
