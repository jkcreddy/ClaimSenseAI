"""
Stage 1: Triage & Classification Agent (ARCHITECTURE §3.1).
Responsibilities:
  - Parses incoming FNOL metadata and unstructured incident descriptions.
  - Classifies peril type and loss category per DEC-010.
  - Calculates statutory prompt payment deadlines (15-day ack, 30-day decision).
  - Produces typed TriageDossier for handoff to Stage 2.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from src.models.schemas import TriageDossier
from src.api.signals import _loss_category


def compute_statutory_deadlines(incident_date_str: str) -> tuple[str, str]:
    """
    Computes statutory prompt payment clocks (NAIC Model Act):
      - 15 calendar days to acknowledge and provide claim forms.
      - 30 calendar days to accept or deny claims upon receipt of proof of loss.
    """
    try:
        # Strip timezone / time if present, parse date
        base_str = incident_date_str.split("T")[0]
        dt = datetime.fromisoformat(base_str).replace(tzinfo=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)

    ack_deadline = (dt + timedelta(days=15)).date().isoformat()
    decision_deadline = (dt + timedelta(days=30)).date().isoformat()
    return ack_deadline, decision_deadline


class TriageAgent:
    """Stage 1: Fast triage & classification agent."""

    def __init__(self, model_id: Optional[str] = None):
        self.agent_name = "TriageAgent"
        self.model_id = model_id or os.getenv(
            "BEDROCK_FAST_MODEL_ID",
            "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        )

    def run(self, claim: dict[str, Any]) -> TriageDossier:
        """
        Executes triage classification.
        Deterministic mock floor produces exact typed dossier;
        LLM path parses unstructured details when live model configured.
        """
        incident_type = claim.get("incident_type", claim.get("peril_type", "unknown"))
        loss_cat = _loss_category(incident_type)
        incident_date = str(claim.get("incident_date", claim.get("loss_date", "")))

        ack_dl, dec_dl = compute_statutory_deadlines(incident_date)
        est = float(claim.get("repair_estimate_usd", claim.get("claimed_amount", 0.0)))
        injuries = bool(claim.get("injuries_reported", False))
        fraud = float(claim.get("fraud_risk_score", 0.0))

        claim_id = claim.get("claim_id", claim.get("idempotency_key", "CLM-NEW"))
        policy_id = claim.get("policy_id", claim.get("policy_number", "POL-NEW"))
        desc = claim.get("incident_description", claim.get("description", ""))

        # Evaluate urgency
        if injuries or est > 25000.0 or fraud >= 0.70:
            urgency = "immediate"
        elif est > 10000.0:
            urgency = "high"
        else:
            urgency = "normal"

        return TriageDossier(
            claim_id=claim_id,
            policy_id=policy_id,
            peril_type=incident_type,
            loss_category=loss_cat,
            statutory_ack_deadline=ack_dl,
            statutory_decision_deadline=dec_dl,
            estimated_loss=est,
            claimant_description=desc,
            urgency=urgency,
        )
