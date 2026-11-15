"""
Fail-Closed Guardrails for ClaimSense AI (DEC-001, DEC-002, DEC-004).

Defends against:
1. Input Prompt Injections (XPIA): In user prompts, free-text incident descriptions,
   and extracted invoice/repair estimate line items.
2. Authority & Decision Hijacking: Attempts to force auto-approval without licensed adjuster review.
3. Citation-or-Block Tripwire: Every coverage decision or payout must cite genuine policy clauses.
4. Financial Drift: Disallows model output that alters deterministically computed payout figures.
"""
import re
from typing import Any, Optional
from pydantic import BaseModel, Field

from src.tools.policy_retriever import validate_citations

# ── 1. Prompt Injection & XPIA Signature Catalog ──────────────────────────────

# Category 1: Direct instruction overrides & reset attacks
_INSTRUCTION_OVERRIDE_PATTERNS = [
    r"ignore\s+(all\s+)?(prior|previous|above|system)\s+instructions",
    r"disregard\s+(all\s+)?(prior|previous|the\s+above)?\s*(rules|policy|instructions|guidelines)",
    r"forget\s+(all\s+)?(prior|previous|earlier)\s+(instructions|context|rules)",
    r"reset\s+(to\s+)?(default|factory|system)\s+instructions",
    r"new\s+(system\s+)?instruction\s*:",
    r"system\s+prompt\s*:",
    r"admin\s+override\s*:",
    r"developer\s+mode\s+(enabled|on|activate)",
]

# Category 2: Authority spoofing & forced decision hijacking (DEC-001)
_DECISION_HIJACKING_PATTERNS = [
    r"approve\s+(this\s+)?(claim\s+)?immediately\s+without\s+(review|adjuster|confirmation)",
    r"auto-?approve\s+without\s+(human|adjuster|confirmation)",
    r"skip\s+(human|adjuster|licensed\s+adjuster)\s+(review|confirmation|approval)",
    r"bypass\s+(all\s+)?(guardrails|checks|rules|thresholds|deductibles)",
    r"override\s+decision\s+(to\s+)?(approve|payment)",
    r"act\s+as\s+(the\s+)?(senior\s+adjuster|ciso|claims\s+director|administrator|supervisor)",
    r"you\s+are\s+now\s+(the\s+)?(senior\s+adjuster|system\s+admin|root)",
]

# Category 3: Delimiter, prompt framing & markdown injection (XPIA)
_DELIMITER_INJECTION_PATTERNS = [
    r"<\s*system\s*>",
    r"<\s*/\s*system\s*>",
    r"\[\s*SYSTEM\s*\]",
    r"\[\s*ADMIN\s*\]",
    r"\[\s*INSTRUCTION\s*\]",
    r"###\s*System\s*Instruction",
    r"===\s*SYSTEM\s*OVERRIDE\s*===",
]

# Category 4: Exfiltration & boundary probe attacks
_EXFILTRATION_PATTERNS = [
    r"(reveal|print|display|dump|leak)\s+(the\s+)?(system\s+prompt|hidden\s+instructions|secret\s+key)",
    r"(dump|list|show)\s+(all\s+)?(claims|policy\s+numbers|other\s+claimants|database\s+records)",
    r"select\s+.*\s+from\s+(claims|users|policies|audit)",
]

# Compile comprehensive regex suite
_INJECTION_RULES: list[tuple[str, re.Pattern]] = [
    ("INSTRUCTION_OVERRIDE", re.compile("|".join(_INSTRUCTION_OVERRIDE_PATTERNS), re.IGNORECASE)),
    ("DECISION_HIJACKING", re.compile("|".join(_DECISION_HIJACKING_PATTERNS), re.IGNORECASE)),
    ("DELIMITER_INJECTION", re.compile("|".join(_DELIMITER_INJECTION_PATTERNS), re.IGNORECASE)),
    ("DATA_EXFILTRATION", re.compile("|".join(_EXFILTRATION_PATTERNS), re.IGNORECASE)),
]


def detect_prompt_injection(text: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Scans text for prompt injection signatures across all threat categories.
    Returns (is_injection, threat_category, matched_text).
    """
    if not text or not isinstance(text, str):
        return False, None, None

    for category, regex in _INJECTION_RULES:
        match = regex.search(text)
        if match:
            return True, category, match.group(0)

    return False, None, None


# ── 2. Guardrail Types & Data Structures ──────────────────────────────────────

class GuardrailResult(BaseModel):
    passed: bool
    tripwire_triggered: bool
    status: str                         # PASSED | QUARANTINED | CITATION_MISSING | FINANCIAL_DRIFT
    reason: str
    threat_category: Optional[str] = None
    matched_pattern: Optional[str] = None
    sanitized_output: Optional[dict[str, Any]] = None


# ── 3. Input Guardrail: Quarantine Malicious Content ──────────────────────────

def quarantine_input_guardrail(input_data: dict[str, Any]) -> GuardrailResult:
    """
    Fail-closed input quarantine screening incoming requests.
    Examines:
      - Prompt / user input
      - Claimant incident narrative
      - Damage description
      - Extracted invoices or repair shop free-text
    """
    fields_to_scan = [
        ("prompt", input_data.get("prompt", "")),
        ("description", input_data.get("description", "")),
        ("incident_description", input_data.get("incident_description", "")),
        ("damage_description", input_data.get("damage_description", "")),
        ("invoice_text", input_data.get("invoice_text", "")),
        ("notes", input_data.get("notes", "")),
    ]

    for field_name, value in fields_to_scan:
        if isinstance(value, str) and value.strip():
            is_bad, category, match = detect_prompt_injection(value)
            if is_bad:
                return GuardrailResult(
                    passed=False,
                    tripwire_triggered=True,
                    status="QUARANTINED",
                    reason=f"Prompt injection signature detected in '{field_name}' [{category}]: '{match}'",
                    threat_category=category,
                    matched_pattern=match,
                )

    # Check for duplicate claim submission if flagged
    if input_data.get("is_duplicate_submission", False):
        return GuardrailResult(
            passed=False,
            tripwire_triggered=True,
            status="QUARANTINED",
            reason="Duplicate claim detected within statutory submission window.",
            threat_category="DUPLICATE_SUBMISSION",
        )

    return GuardrailResult(
        passed=True,
        tripwire_triggered=False,
        status="PASSED",
        reason="Input passed all prompt injection and quarantine checks.",
    )


# ── 4. Output Guardrail: Citation & Financial Verification ────────────────────

def citation_and_payout_guardrail(
    assessment_data: dict[str, Any],
    deterministic_signals: Optional[dict[str, Any]] = None,
) -> GuardrailResult:
    """
    Fail-closed output guardrail.
    Enforces:
      1. Mandatory policy citation (DEC-004): Every decision recommending approval or denial
         must cite real clauses from the 12-clause manual (validated by policy_retriever).
      2. Deterministic payout fidelity (DEC-002): Stated payout cannot drift from pure-Python math.
      3. Human confirmation disclaimer (DEC-001): Auto-approvals must carry the disclaimer.
    """
    decision = assessment_data.get("decision", "")
    citations = assessment_data.get("citations", []) or assessment_data.get("policy_citations", [])
    raw_payout = assessment_data.get("net_payable_estimate_usd")
    if raw_payout is None:
        raw_payout = assessment_data.get("net_payable", 0.0)
    net_payable = float(raw_payout or 0.0)
    rationale = assessment_data.get("rationale", "")

    # Normalise citations list if objects were passed
    string_citations = []
    for c in citations:
        if isinstance(c, str):
            string_citations.append(c)
        elif isinstance(c, dict) and "clause_id" in c:
            string_citations.append(c["clause_id"])

    # ── Rule 1: Closed-Book Citation Verification (DEC-004) ───────────────────
    # If decision is approve, deny, or payout > 0, valid citations are required.
    if decision in ("approve", "deny") or net_payable > 0:
        valid, invalid_ids = validate_citations(string_citations)
        if not valid:
            return GuardrailResult(
                passed=False,
                tripwire_triggered=True,
                status="CITATION_MISSING",
                reason=(
                    f"Decision '{decision}' failed closed: "
                    f"missing or invalid policy clause citations {invalid_ids} (DEC-004)."
                ),
            )

    # ── Rule 2: Deterministic Financial Fidelity (DEC-002) ────────────────────
    if deterministic_signals:
        expected_payout = float(deterministic_signals.get("net_payable_usd", 0.0) or 0.0)
        if abs(net_payable - expected_payout) > 0.01:
            return GuardrailResult(
                passed=False,
                tripwire_triggered=True,
                status="FINANCIAL_DRIFT",
                reason=(
                    f"Decision output failed financial fidelity: "
                    f"model net payable ${net_payable:,.2f} != deterministic ${expected_payout:,.2f} (DEC-002)."
                ),
            )

    # ── Rule 3: Human Adjuster Confirmation Disclaimer (DEC-001) ─────────────
    # Any approval output must state human confirmation is required
    response_sentence = assessment_data.get("response", "") or rationale
    if decision == "approve" and "Human adjuster confirmation is required" not in response_sentence:
        # Rather than failing completely, inject the mandatory disclaimer
        return GuardrailResult(
            passed=True,
            tripwire_triggered=False,
            status="PASSED",
            reason="Output passed citation and math checks. Appending required confirmation disclaimer.",
            sanitized_output={"disclaimer_appended": True},
        )

    return GuardrailResult(
        passed=True,
        tripwire_triggered=False,
        status="PASSED",
        reason="Output passed citation, financial, and regulatory confirmation checks.",
    )

