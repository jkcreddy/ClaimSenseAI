"""
Invocation handler — wires together:
  1. RBAC / access control  (DEC-005)
  2. Deterministic signal layer  (DEC-002)
  3. Agent pipeline (or rule-engine fallback)
  4. Response assembly matching the UI contract
"""
import time
import logging
from typing import Optional

from src.api.schemas_ui import (
    InvocationRequest,
    InvocationResponse,
    Assessment,
    ClaimDetail,
    PolicyDetail,
    TokenUsage,
    PipelineAgent,
    PolicyCitation,
)
from src.api.rbac import can_read_claim, AuditLogger
from src.api.fixtures import load_claim, load_policy
from src.api.signals import compute_signals
from src.agents.guardrails import quarantine_input_guardrail, citation_and_payout_guardrail

logger = logging.getLogger(__name__)
_audit = AuditLogger()

# Groups that map to "denied" statuses in the UI
_DENIED_STATUSES = {"denied", "not_found", "llm_unavailable"}

# Decision code → UI status mapping
_DECISION_TO_STATUS = {
    "approve": "approved",
    "deny": "denied",
    "flag_review": "flag_review",
    "route_specialist": "route_specialist",
}


async def handle_invocation(
    body: InvocationRequest, jwt_token: Optional[str]
) -> InvocationResponse:
    t0 = time.monotonic()

    # ── 1. RBAC ──────────────────────────────────────────────────────────────
    allowed, reason = can_read_claim(body.user_id, body.group, body.claim_id)
    _audit.log(
        user_id=body.user_id,
        group=body.group,
        claim_id=body.claim_id,
        result="allowed" if allowed else "denied",
        reason=reason,
    )
    if not allowed:
        return InvocationResponse(
            status="denied",
            response=f"Claim {body.claim_id} is not available to you.",
            data_backend="fixtures",
        )

    # ── 2. Load fixture data ──────────────────────────────────────────────────
    claim = load_claim(body.claim_id)
    if claim is None:
        return InvocationResponse(
            status="not_found",
            response=f"Claim {body.claim_id} not found in the data store.",
            data_backend="fixtures",
        )
    policy = load_policy(claim["policy_id"])

    # ── 3. Input Guardrail: Prompt Injection & XPIA Quarantine ───────────────
    input_scan_payload = {
        "prompt": body.prompt,
        "claim_id": body.claim_id,
        "incident_description": claim.get("incident_description", ""),
        "damage_description": claim.get("damage_description", ""),
    }
    input_guard_result = quarantine_input_guardrail(input_scan_payload)
    if not input_guard_result.passed:
        _audit.log(
            user_id=body.user_id,
            group=body.group,
            claim_id=body.claim_id,
            result="quarantined",
            reason=input_guard_result.reason,
        )
        total_ms = int((time.monotonic() - t0) * 1000)
        return InvocationResponse(
            status="flag_review",
            response=f"Security alert for {body.claim_id}: {input_guard_result.reason}. Flagged for manual review.",
            data_backend="fixtures",
            assessment=Assessment(
                decision="flag_review",
                validation_status="GUARDRAIL_BLOCKED",
                confidence=0.0,
                net_payable_estimate_usd=0.0,
                rationale=f"Quarantined by input guardrail: {input_guard_result.reason}",
                key_findings=["Prompt injection or adversarial attack signature detected."],
                required_actions=["Refer to CISO / SIU security review.", "Quarantine input files."],
                flags=["prompt_injection_detected", "guardrail_quarantined"],
                citations=[],
                baseline_rules_decision="flag_review",
                agrees_with_baseline=True,
                engine="guardrail-tripwire",
                fallback_reason=input_guard_result.reason,
                latency_ms=total_ms,
            ),
            claim=ClaimDetail(
                claim_id=claim["claim_id"],
                incident_type=claim["incident_type"],
                vehicle_year=int(claim["vehicle_year"]),
                vehicle_make=claim["vehicle_make"],
                vehicle_model=claim["vehicle_model"],
                claim_status=claim["claim_status"],
            ),
            policy=PolicyDetail(
                policy_id=policy["policy_id"],
                coverage_type=policy["coverage_type"],
                policy_status=policy["policy_status"],
            ) if policy else None,
        )

    # ── 4. Deterministic signal layer (DEC-002) ───────────────────────────────
    signals = compute_signals(claim, policy)

    # ── 5. Baseline rule decision (DEC-003) ───────────────────────────────────
    baseline_decision = _apply_rules(signals)

    # ── 6. Agent pipeline (or rule-engine fallback) ───────────────────────────
    assessment, pipeline_agents, policy_citations, used_llm = await _run_pipeline(
        body, claim, policy, signals, baseline_decision, jwt_token
    )

    # ── 7. Output Guardrail: Citation & Payout Verification ───────────────────
    out_guard_result = citation_and_payout_guardrail(assessment.model_dump(), signals)
    if not out_guard_result.passed:
        logger.warning("Output guardrail tripped: %s", out_guard_result.reason)
        assessment.validation_status = out_guard_result.status
        assessment.decision = "flag_review"
        assessment.flags.append("guardrail_tripwire_triggered")
        assessment.rationale += f" [Fail-Closed Guardrail: {out_guard_result.reason}]"

    total_ms = int((time.monotonic() - t0) * 1000)
    assessment.latency_ms = total_ms
    assessment.signals = signals
    assessment.baseline_rules_decision = baseline_decision
    assessment.agrees_with_baseline = assessment.decision == baseline_decision
    assessment.pipeline = pipeline_agents
    assessment.policy_citations = policy_citations

    status = _DECISION_TO_STATUS.get(assessment.decision, "flag_review")

    return InvocationResponse(
        status=status,
        response=_summary_sentence(assessment, claim),
        data_backend="fixtures",
        assessment=assessment,
        claim=ClaimDetail(
            claim_id=claim["claim_id"],
            incident_type=claim["incident_type"],
            vehicle_year=int(claim["vehicle_year"]),
            vehicle_make=claim["vehicle_make"],
            vehicle_model=claim["vehicle_model"],
            claim_status=claim["claim_status"],
        ),
        policy=PolicyDetail(
            policy_id=policy["policy_id"],
            coverage_type=policy["coverage_type"],
            policy_status=policy["policy_status"],
            collision_deductible_usd=signals.get("deductible_usd"),
        ) if policy else None,
    )


# ── Rule engine (DEC-003 gate order) ─────────────────────────────────────────

def _apply_rules(signals: dict) -> str:
    """Deterministic gate order per DEC-009 / DEC-003."""
    if signals.get("fraud_risk_score", 0) >= 0.70:
        return "route_specialist"
    if not signals.get("coverage_in_force", True):
        return "deny"
    if signals.get("total_loss_ratio", 0) >= 0.75:
        return "route_specialist"
    if signals.get("injuries_reported", False):
        return "flag_review"
    if not signals.get("required_docs_complete", True):
        return "flag_review"
    # First-pass band (DEC-003 Proposed values)
    if (
        signals.get("tl_ratio_pct", 1.0) <= 0.35
        and signals.get("fraud_risk_score", 1.0) < 0.30
        and signals.get("coverage_in_force", False)
    ):
        return "approve"
    return "flag_review"


# ── Pipeline runner ───────────────────────────────────────────────────────────

async def _run_pipeline(body, claim, policy, signals, baseline_decision, jwt_token):
    """
    Attempt the 3-stage LLM pipeline. Falls back to rule engine if unavailable.
    Returns (Assessment, pipeline_agents, policy_citations, used_llm).
    """
    # In local dev / console UI, if no token is explicitly pasted into the UI box,
    # issue a development badge for the active user so the multi-agent pipeline runs seamlessly
    if not jwt_token:
        from src.api.auth import _issue_token
        jwt_token = _issue_token(body.user_id, body.group)

    try:
        from src.mcp.auth_context import set_jwt, reset_jwt
        from src.mcp.server import GovernedMCPDoor

        # Bind JWT to async context so every MCP tool call can verify the caller
        ctx_token = set_jwt(jwt_token)
        try:
            door = GovernedMCPDoor()
            # Eagerly verify the MCP door can resolve the caller before agent runs
            caller = door._caller()
            logger.info("MCP door ready: caller=%s group=%s", caller.user_id, caller.group)

            # ── Multi-Agent 3-Stage Pipeline ──────────────────────────────────
            from src.agents.orchestrator import ClaimSenseOrchestrator
            orch = ClaimSenseOrchestrator(mcp_door=door)
            orch_result = await orch.process_claim_async(
                claim_input=claim,
                prompt=body.prompt,
                signals=signals,
                baseline_decision=baseline_decision,
            )
            return orch_result.assessment, orch_result.pipeline, orch_result.policy_citations, True
        finally:
            reset_jwt(ctx_token)

    except Exception as exc:
        logger.warning("LLM pipeline unavailable, using rule engine: %s", exc)
        return _rule_engine_assessment(signals, baseline_decision, str(exc)), [], [], False


def _rule_engine_assessment(signals: dict, decision: str, reason: str) -> Assessment:
    net = signals.get("net_payable_usd", 0.0)
    flags = _build_flags(signals)
    citations = _citations_for_decision(decision, signals)
    return Assessment(
        decision=decision,
        validation_status="PASSED",
        confidence=None,
        net_payable_estimate_usd=net,
        rationale=_rule_rationale(decision, signals),
        coverage_assessment=_coverage_text(signals),
        fraud_assessment=f"Vendor fraud score: {signals.get('fraud_risk_score', 'N/A')}",
        key_findings=_key_findings(signals),
        required_actions=_required_actions(decision, signals),
        evidence_gaps=_evidence_gaps(signals),
        flags=flags,
        citations=citations,
        baseline_rules_decision=decision,
        agrees_with_baseline=True,
        disputes_computed_payout=False,
        engine="rules-fallback",
        fallback_reason=reason,
    )


# ── Text helpers ──────────────────────────────────────────────────────────────

def _summary_sentence(assessment: Assessment, claim: dict) -> str:
    d = assessment.decision
    cid = claim["claim_id"]
    net = assessment.net_payable_estimate_usd
    if d == "approve":
        return f"Recommend first-pass approval of {cid} at computed net payable ${net:,.2f}. Human adjuster confirmation is required."
    if d == "deny":
        return f"Recommend denial of {cid}. Human adjuster confirmation is required."
    if d == "route_specialist":
        return f"Route {cid} to specialist (SIU or appraiser). Human adjuster confirmation is required."
    return f"Flag {cid} for adjuster review. Human adjuster confirmation is required."


def _rule_rationale(decision: str, signals: dict) -> str:
    parts = []
    if signals.get("fraud_risk_score", 0) >= 0.70:
        parts.append(f"Fraud score {signals['fraud_risk_score']:.2f} ≥ 0.70 (SIU threshold, CLS-FRD-007).")
    if not signals.get("coverage_in_force", True):
        parts.append("Policy not in force on incident date or coverage type excludes this loss.")
    if signals.get("total_loss_ratio", 0) >= 0.75:
        parts.append(f"Total-loss ratio {signals['total_loss_ratio']:.0%} ≥ 0.75 (CLS-TL-005).")
    if signals.get("injuries_reported"):
        parts.append("Injuries reported — injury-qualified adjuster required (CLS-INJ-008).")
    if not parts:
        parts.append("Claim meets first-pass criteria per DEC-003 rule table.")
    return " ".join(parts)


def _coverage_text(signals: dict) -> str:
    if not signals.get("coverage_in_force", True):
        return "Coverage not in force: policy inactive or loss type not covered under this policy."
    return f"Policy active. Loss category: {signals.get('loss_category', 'unknown')}. Deductible: ${signals.get('deductible_usd', 0):,.0f}."


def _build_flags(signals: dict) -> list[str]:
    flags = []
    if signals.get("fraud_risk_score", 0) >= 0.70:
        flags.append("high_fraud_score")
    if not signals.get("coverage_in_force", True):
        flags.append("coverage_not_in_force")
    if signals.get("total_loss_ratio", 0) >= 0.75:
        flags.append("total_loss")
    if signals.get("injuries_reported"):
        flags.append("injuries_reported")
    if not signals.get("required_docs_complete", True):
        flags.append("docs_incomplete")
    return flags or ["no_flags"]


def _citations_for_decision(decision: str, signals: dict) -> list[str]:
    cites = []
    if signals.get("loss_category") in ("collision", "comprehensive"):
        cites.append("CLS-COLL-001 §4.2")
    if not signals.get("coverage_in_force", True):
        cites.append("CLS-LIAB-003")
    if signals.get("total_loss_ratio", 0) >= 0.75:
        cites.append("CLS-TL-005")
    if signals.get("net_payable_usd", 0) > 25000:
        cites.append("CLS-PAY-012")
    return cites


def _key_findings(signals: dict) -> list[str]:
    findings = []
    if signals.get("prior_claims_12mo", 0) > 0:
        findings.append(f"{signals['prior_claims_12mo']} prior claim(s) in last 12 months.")
    if signals.get("police_report_filed"):
        findings.append("Police report on file.")
    if signals.get("injuries_reported"):
        findings.append("Injuries reported — medical documentation required.")
    return findings


def _required_actions(decision: str, signals: dict) -> list[str]:
    actions = []
    if decision == "route_specialist" and signals.get("fraud_risk_score", 0) >= 0.70:
        actions.append("Assign to SIU for fraud investigation.")
    if decision == "route_specialist" and signals.get("total_loss_ratio", 0) >= 0.75:
        actions.append("Assign to total-loss appraiser.")
    if signals.get("injuries_reported"):
        actions.append("Assign injury-qualified adjuster.")
    return actions


def _evidence_gaps(signals: dict) -> list[str]:
    gaps = []
    if not signals.get("required_docs_complete", True):
        gaps.append("One or more required documents pending OCR or missing.")
    return gaps
