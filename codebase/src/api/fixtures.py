"""
Fixture loader — reads claimsense_claims.csv and claimsense_policies.csv.
Applies the SoW §6 type conversions: "Yes"/"No" → bool, "N/A" → None, money → float.
"""
import csv
import functools
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).parents[2] / "data"


def _to_bool(v: str) -> bool:
    return v.strip().lower() == "yes"


def _to_float_or_none(v: str) -> Optional[float]:
    v = v.strip()
    if v.upper() in ("N/A", "", "NONE"):
        return None
    return float(v)


@functools.lru_cache(maxsize=1)
def _load_claims() -> dict[str, dict]:
    path = _DATA_DIR / "claimsense_claims.csv"
    rows = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows[row["claim_id"]] = {
                "claim_id": row["claim_id"],
                "policy_id": row["policy_id"],
                "vehicle_vin": row["vehicle_vin"],
                "vehicle_year": int(row["vehicle_year"]),
                "vehicle_make": row["vehicle_make"],
                "vehicle_model": row["vehicle_model"],
                "vehicle_acv_usd": float(row["vehicle_acv_usd"]),
                "incident_date": row["incident_date"],
                "incident_type": row["incident_type"],
                "incident_description": row["incident_description"],
                "damage_description": row["damage_description"],
                "repair_estimate_usd": float(row["repair_estimate_usd"]),
                "injuries_reported": _to_bool(row["injuries_reported"]),
                "police_report_filed": _to_bool(row["police_report_filed"]),
                "assigned_adjuster": row["assigned_adjuster"],
                "prior_claims_12mo": int(row["prior_claims_12mo"]),
                "fraud_risk_score": float(row["fraud_risk_score"]),
                "claim_status": row["claim_status"],
                "created_at": row["created_at"],
            }
    return rows


@functools.lru_cache(maxsize=1)
def _load_policies() -> dict[str, dict]:
    path = _DATA_DIR / "claimsense_policies.csv"
    rows = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows[row["policy_id"]] = {
                "policy_id": row["policy_id"],
                "holder_first": row["holder_first"],
                "holder_last": row["holder_last"],
                "vehicle_vin": row["vehicle_vin"],
                "vehicle_year": int(row["vehicle_year"]),
                "vehicle_make": row["vehicle_make"],
                "vehicle_model": row["vehicle_model"],
                "vehicle_acv_usd": float(row["vehicle_acv_usd"]),
                "coverage_type": row["coverage_type"],
                "collision_coverage": _to_bool(row["collision_coverage"]),
                "comprehensive_coverage": _to_bool(row["comprehensive_coverage"]),
                "collision_deductible_usd": _to_float_or_none(row["collision_deductible_usd"]),
                "comprehensive_deductible_usd": _to_float_or_none(row["comprehensive_deductible_usd"]),
                "liability_limit": row["liability_limit"],
                "medpay_limit_usd": _to_float_or_none(row["medpay_limit_usd"]),
                "policy_status": row["policy_status"],
                "policy_start": row["policy_start"],
                "policy_end": row["policy_end"],
            }
    return rows


def load_claim(claim_id: str) -> Optional[dict]:
    return _load_claims().get(claim_id)


def load_policy(policy_id: str) -> Optional[dict]:
    return _load_policies().get(policy_id)
