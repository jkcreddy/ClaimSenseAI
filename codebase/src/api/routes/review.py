from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ReviewDecision(BaseModel):
    adjuster_id: str
    decision: str        # confirm_approve | confirm_deny | escalate
    notes: str = ""


@router.get("/queue")
async def review_queue(user_id: str, group: str = "adjuster"):
    """Returns claims in MANUAL_REVIEW status assigned to this adjuster."""
    from src.api.fixtures import _load_claims
    from src.api.rbac import can_read_claim
    claims = _load_claims()
    queue = []
    for c in claims.values():
        allowed, _ = can_read_claim(user_id, group, c["claim_id"])
        if allowed and c["claim_status"] in ("Pending Review", "Escalated", "Under Investigation"):
            queue.append({"claim_id": c["claim_id"], "status": c["claim_status"]})
    return queue


@router.post("/{claim_id}/decision")
async def record_decision(claim_id: str, body: ReviewDecision):
    """
    Human confirmation step (DEC-001 — co-pilot, not autopilot).
    In the pilot this is read-only; write path is a future change request.
    """
    return {
        "claim_id": claim_id,
        "adjuster_id": body.adjuster_id,
        "decision": body.decision,
        "message": "Decision recorded. Human adjuster confirmation is required before any CMS write.",
    }
