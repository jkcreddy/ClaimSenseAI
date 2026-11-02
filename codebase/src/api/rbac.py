"""
RBAC — access control enforced at the data boundary before any agent runs.
Identity comes from the verified JWT / request context, never from model input (DEC-005).
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Groups allowed to read any claim (no state/tier filter)
_UNRESTRICTED_GROUPS = {"senior-adjuster", "fraud-investigator", "total-loss-appraiser"}

# Claim-to-adjuster assignment from the fixture data
_CLAIM_ASSIGNMENTS = {
    "CLM-2024-1800": "alice",
    "CLM-2024-1873": "carol",
    "CLM-2024-1946": "eve",
    "CLM-2024-2019": "david",
    "CLM-2024-2092": "alice",
    "CLM-2024-2165": "alice",
    "CLM-2024-2238": "david",
    "CLM-2024-2311": "david",
    "CLM-2024-2384": "alice",
    "CLM-2024-2457": "bob",
    "CLM-2024-2530": "david",
    "CLM-2024-2603": "carol",
    "CLM-2024-2676": "bob",
    "CLM-2024-2749": "alice",
    "CLM-2024-2822": "david",
    "CLM-2024-2895": "david",
    "CLM-2024-2968": "carol",
    "CLM-2024-3041": "alice",
    "CLM-2024-3114": "bob",
    "CLM-2024-3187": "eve",
}


def can_read_claim(user_id: str, group: str, claim_id: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Reason is written to the audit log only — never returned to the caller (DEC-013).
    """
    if group in _UNRESTRICTED_GROUPS:
        return True, "unrestricted group"

    assigned = _CLAIM_ASSIGNMENTS.get(claim_id)
    if assigned is None:
        return False, f"claim {claim_id} not found"

    if assigned == user_id:
        return True, "assigned adjuster"

    return False, f"assigned to {assigned}, not {user_id}"


class AuditLogger:
    """Append-only audit trail for every read and denial (DEC-005)."""

    def log(
        self,
        user_id: str,
        group: str,
        claim_id: str,
        result: str,
        reason: str,
    ) -> None:
        # In production this writes to CloudWatch / claim_audit_log table.
        # For the pilot it logs to stdout in structured JSON format.
        logger.info(
            '{"ts":"%s","user_id":"%s","group":"%s","claim_id":"%s","result":"%s","reason":"%s"}',
            datetime.now(timezone.utc).isoformat(),
            user_id,
            group,
            claim_id,
            result,
            reason,
        )
