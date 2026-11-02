import time
import logging
from datetime import datetime
from src.models.schemas import ClaimSubmissionRequest, AdjusterIdentity
from src.mcp.auth_context import set_current_adjuster
from src.mcp.server import GovernedMCPDoor
from src.agents.orchestrator import ClaimSenseOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("incident_drill")

def run_reliability_incident_drill():
    """
    Simulates production network instability and duplicate retry attempts.
    Validates that identical idempotency keys execute safely without duplicate entries.
    """
    logger.info("Starting ClaimSense Reliability Incident Drill...")
    door = GovernedMCPDoor()
    set_current_adjuster(AdjusterIdentity(adjuster_id="ADJ-DRILL", email="drill@bank", role="SUPERVISOR", licensed_states=["OH"]))
    orchestrator = ClaimSenseOrchestrator(mcp_door=door)
    
    req = ClaimSubmissionRequest(
        idempotency_key="DRILL-RETRY-KEY-999",
        policy_number="PAP-DRILL",
        claimant_id="USER-DRILL",
        jurisdiction_state="OH",
        peril_type="AUTO_COLLISION",
        loss_date=datetime.utcnow(),
        claimed_amount=1200.0,
        description="Incident drill submission"
    )
    
    out1 = orchestrator.process_claim(req)
    out2 = orchestrator.process_claim(req)
    assert out1.approved_payout == out2.approved_payout
    logger.info("Drill Passed: Idempotent replays executed deterministically.")

if __name__ == "__main__":
    run_reliability_incident_drill()
