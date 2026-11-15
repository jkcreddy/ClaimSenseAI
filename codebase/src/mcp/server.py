"""
MCP Server — GovernedMCPDoor

Every CMS tool call:
  1. Pulls the caller's JWT from auth_context
  2. Verifies it and extracts CallerIdentity (sub + cognito:groups)
  3. Enforces RBAC — adjuster can only read their own claims
  4. Calls the appropriate CMS tool (backed by fixtures for local dev)
  5. Returns MCPToolResult with caller identity stamped on every response

The JWT is forwarded as X-Adjuster-Token on outbound calls so downstream
services can independently verify the caller without trusting the MCP layer.
"""
import logging
from typing import Optional

from src.mcp.auth_context import get_jwt
from src.api.auth import verify_jwt
from src.api.rbac import can_read_claim, AuditLogger
from src.api.fixtures import load_claim, load_policy
from src.models.schemas import (
    MCPToolResult,
    ClaimRecord,
    PolicyRecord,
    DocumentRecord,
)
from src.tools.policy_retriever import get_clause, search_policy_clauses
from src.tools.math_tools import compute_settlement_payout

logger = logging.getLogger(__name__)
_audit = AuditLogger()


class GovernedMCPDoor:
    """
    Thin MCP gateway.  Each public method is one CMS tool.
    JWT is read from the async context — never passed as a parameter —
    so it cannot be spoofed by model-generated tool arguments (DEC-005).
    """

    # ── internal: resolve identity from context JWT ───────────────────────────

    def _caller(self):
        """Verify the context JWT and return CallerIdentity. Raises 401 on failure."""
        return verify_jwt(get_jwt())

    # ── CMS tool: get_claim ───────────────────────────────────────────────────

    def get_claim(self, claim_id: str) -> MCPToolResult:
        """
        Fetch a claim record.
        RBAC: adjuster may only read claims assigned to them.
              senior-adjuster / fraud-investigator / total-loss-appraiser are unrestricted.
        """
        caller = self._caller()
        allowed, reason = can_read_claim(caller.user_id, caller.group, claim_id)
        _audit.log(caller.user_id, caller.group, claim_id,
                   "allowed" if allowed else "denied", reason)

        if not allowed:
            return MCPToolResult(
                tool="get_claim",
                caller_id=caller.user_id,
                caller_group=caller.group,
                data={},
                error=f"Access denied to {claim_id}",
            )

        record = load_claim(claim_id)
        if record is None:
            return MCPToolResult(
                tool="get_claim",
                caller_id=caller.user_id,
                caller_group=caller.group,
                data={},
                error=f"Claim {claim_id} not found",
            )

        logger.info("MCP get_claim: caller=%s claim=%s", caller.user_id, claim_id)
        return MCPToolResult(
            tool="get_claim",
            caller_id=caller.user_id,
            caller_group=caller.group,
            data=ClaimRecord(**record).model_dump(),
        )

    # ── CMS tool: get_policy ──────────────────────────────────────────────────

    def get_policy(self, policy_id: str) -> MCPToolResult:
        """
        Fetch a policy record.
        Caller must first have been granted access to the linked claim.
        All groups may read policy data once claim access is established.
        """
        caller = self._caller()
        record = load_policy(policy_id)

        if record is None:
            return MCPToolResult(
                tool="get_policy",
                caller_id=caller.user_id,
                caller_group=caller.group,
                data={},
                error=f"Policy {policy_id} not found",
            )

        logger.info("MCP get_policy: caller=%s policy=%s", caller.user_id, policy_id)
        return MCPToolResult(
            tool="get_policy",
            caller_id=caller.user_id,
            caller_group=caller.group,
            data=PolicyRecord(**record).model_dump(),
        )

    # ── CMS tool: get_documents ───────────────────────────────────────────────

    def get_documents(self, claim_id: str) -> MCPToolResult:
        """
        List documents attached to a claim.
        RBAC mirrors get_claim — same access rules apply.
        """
        caller = self._caller()
        allowed, reason = can_read_claim(caller.user_id, caller.group, claim_id)
        _audit.log(caller.user_id, caller.group, claim_id,
                   "allowed" if allowed else "denied", reason)

        if not allowed:
            return MCPToolResult(
                tool="get_documents",
                caller_id=caller.user_id,
                caller_group=caller.group,
                data={},
                error=f"Access denied to documents for {claim_id}",
            )

        # Build document list from the data directory
        from pathlib import Path
        claims_dir = Path(__file__).parents[2] / "data" / "claims" / claim_id
        docs = []
        if claims_dir.exists():
            for f in sorted(claims_dir.iterdir()):
                if f.is_file():
                    parts = f.stem.split("_", 2)          # DOC-00001_FNOL_FORM
                    doc_id = parts[0] if parts else f.stem
                    doc_type = "_".join(parts[1:]) if len(parts) > 1 else "UNKNOWN"
                    docs.append(DocumentRecord(
                        doc_id=doc_id,
                        claim_id=claim_id,
                        doc_type=doc_type,
                        filename=f.name,
                    ).model_dump())

        logger.info("MCP get_documents: caller=%s claim=%s docs=%d",
                    caller.user_id, claim_id, len(docs))
        return MCPToolResult(
            tool="get_documents",
            caller_id=caller.user_id,
            caller_group=caller.group,
            data={"documents": docs, "count": len(docs)},
        )

    # ── Policy Knowledge Tool: lookup_policy_clause ───────────────────────────

    def lookup_policy_clause(self, clause_id: str) -> MCPToolResult:
        """Fetch exact policy clause text by canonical ID (e.g. CLS-COLL-001)."""
        caller = self._caller()
        clause = get_clause(clause_id)
        if clause is None:
            return MCPToolResult(
                tool="lookup_policy_clause",
                caller_id=caller.user_id,
                caller_group=caller.group,
                data={},
                error=f"Clause {clause_id} not found in governed manual",
            )
        return MCPToolResult(
            tool="lookup_policy_clause",
            caller_id=caller.user_id,
            caller_group=caller.group,
            data=clause,
        )

    # ── Policy Knowledge Tool: search_policy_manual ───────────────────────────

    def search_policy_manual(self, query: str, loss_category: Optional[str] = None, limit: int = 3) -> MCPToolResult:
        """Search closed-book policy manual clauses by query/peril."""
        caller = self._caller()
        results = search_policy_clauses(query=query, loss_category=loss_category, limit=limit)
        return MCPToolResult(
            tool="search_policy_manual",
            caller_id=caller.user_id,
            caller_group=caller.group,
            data={"clauses": results, "count": len(results)},
        )

    # ── Deterministic Math Tool: compute_payout ───────────────────────────────

    def compute_payout(
        self,
        claimed_amount: float,
        vehicle_acv: float,
        deductible: float,
        depreciation: float = 0.0,
        coverage_limit: Optional[float] = None,
        claim_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> MCPToolResult:
        """Deterministically calculate net settlement payout with lesser-of ACV cap."""
        caller = self._caller()
        result = compute_settlement_payout(
            claimed_amount=claimed_amount,
            vehicle_acv=vehicle_acv,
            deductible=deductible,
            depreciation=depreciation,
            coverage_limit=coverage_limit,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )
        return MCPToolResult(
            tool="compute_payout",
            caller_id=caller.user_id,
            caller_group=caller.group,
            data=result,
        )
