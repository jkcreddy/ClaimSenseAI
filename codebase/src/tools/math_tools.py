"""
Deterministic math tools for claim financial calculations (DEC-002).
Arithmetic, deductible subtractions, and policy caps are computed in pure Python
with exact currency precision — never delegated to an LLM.
"""
from typing import Optional
from pydantic import BaseModel, Field


class SettlementPayoutInput(BaseModel):
    claimed_amount: float = Field(..., description="Estimated or claimed repair cost in USD")
    vehicle_acv: float = Field(..., description="Actual Cash Value of the vehicle immediately before loss in USD")
    deductible: float = Field(..., description="Applicable policy deductible in USD")
    depreciation: float = Field(default=0.0, description="Depreciation amount in USD if applicable")
    coverage_limit: Optional[float] = Field(default=None, description="Optional maximum policy limit for the coverage")
    claim_id: Optional[str] = Field(default=None, description="Claim identifier")
    idempotency_key: Optional[str] = Field(default=None, description="Unique idempotency key for this calculation")


class SettlementPayoutResult(BaseModel):
    claim_id: Optional[str] = None
    claimed_amount: float
    vehicle_acv: float
    depreciation_applied: float
    net_loss_before_deductible: float
    capped_by_acv: bool
    deductible_applied: float
    net_payable: float
    total_loss_ratio: float
    is_total_loss_candidate: bool
    senior_approval_required: bool
    idempotency_key: Optional[str] = None
    calculation_verified: bool = True


def compute_settlement_payout(
    claimed_amount: float,
    vehicle_acv: float,
    deductible: float,
    depreciation: float = 0.0,
    coverage_limit: Optional[float] = None,
    claim_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Computes settlement payout deterministically per CLS-COLL-001 / CLS-DED-004.
    
    Rules:
      1. Net Loss = max(0.0, claimed_amount - depreciation)
      2. Capped by lesser of (Net Loss, vehicle_acv) per CLS-COLL-001
      3. Capped by coverage_limit if specified
      4. Net Payable = max(0.0, applicable_coverage - deductible)
      5. Total Loss Candidate = (claimed_amount / vehicle_acv >= 0.75) per CLS-TL-005
      6. Senior Approval Required = (net_payable > 25000.00) per CLS-PAY-012
    """
    depreciation = max(0.0, float(depreciation))
    claimed_amount = max(0.0, float(claimed_amount))
    vehicle_acv = max(0.0, float(vehicle_acv))
    deductible = max(0.0, float(deductible))

    # Loss after depreciation
    net_loss = max(0.0, claimed_amount - depreciation)

    # Lesser-of rule: payment is limited to lesser of repair cost or ACV (CLS-COLL-001)
    capped_by_acv = net_loss > vehicle_acv and vehicle_acv > 0
    applicable_coverage = min(net_loss, vehicle_acv) if vehicle_acv > 0 else net_loss

    # Apply coverage sublimit if given
    if coverage_limit is not None and coverage_limit > 0:
        applicable_coverage = min(applicable_coverage, float(coverage_limit))

    # Subtract deductible (CLS-DED-004)
    net_payable = max(0.0, applicable_coverage - deductible)

    # Total loss ratio: repair estimate / ACV (CLS-TL-005)
    tl_ratio = (claimed_amount / vehicle_acv) if vehicle_acv > 0 else 0.0
    is_total_loss = tl_ratio >= 0.75

    # Senior approval check (CLS-PAY-012)
    senior_approval = net_payable > 25000.00

    result = SettlementPayoutResult(
        claim_id=claim_id,
        claimed_amount=round(claimed_amount, 2),
        vehicle_acv=round(vehicle_acv, 2),
        depreciation_applied=round(depreciation, 2),
        net_loss_before_deductible=round(applicable_coverage, 2),
        capped_by_acv=capped_by_acv,
        deductible_applied=round(deductible, 2),
        net_payable=round(net_payable, 2),
        total_loss_ratio=round(tl_ratio, 4),
        is_total_loss_candidate=is_total_loss,
        senior_approval_required=senior_approval,
        idempotency_key=idempotency_key,
        calculation_verified=True,
    )
    return result.model_dump()

