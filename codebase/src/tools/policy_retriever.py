"""
Closed-Book Policy Manual Retriever (DEC-004).
Indexes the 12 canonical clauses from the AutoSure Claims Policy Manual (v2024.1).
Guarantees zero-hallucination citation validation — every model judgment must cite
clauses existing strictly within this governed corpus.
"""
import re
from typing import Optional
from pydantic import BaseModel, Field


class PolicyClause(BaseModel):
    clause_id: str
    title: str
    text: str
    category: str
    keywords: list[str] = []
    applicable_perils: list[str] = []


# ── The 12 Canonical Clauses from AutoSure_Policy_Manual_CLS-001-012.pdf ──────

_POLICY_MANUAL_CLAUSES: dict[str, dict] = {
    "CLS-COLL-001": {
        "clause_id": "CLS-COLL-001",
        "title": "Collision coverage — insuring agreement",
        "category": "collision",
        "text": (
            "Under Full Coverage policies, the insurer pays for direct and accidental loss to "
            "the covered vehicle caused by collision with another vehicle or object, or by upset "
            "of the vehicle, regardless of fault. Payment is limited to the lesser of the reasonable "
            "cost of repair or the actual cash value (ACV) of the vehicle immediately before the loss, "
            "reduced by the applicable collision deductible shown on the declarations page."
        ),
        "keywords": [
            "collision", "impact", "crash", "t-bone", "rear-end", "hit and run", "rollover",
            "backing", "lesser of", "acv", "upset", "pothole"
        ],
        "applicable_perils": [
            "parking lot collision", "single-vehicle accident", "t-bone collision",
            "rear-end on highway", "hit and run", "low-speed collision", "side-swipe",
            "multi-vehicle pileup", "backing accident", "rollover", "underride accident",
            "pothole damage"
        ],
    },
    "CLS-COMP-002": {
        "clause_id": "CLS-COMP-002",
        "title": "Comprehensive (other-than-collision) coverage",
        "category": "comprehensive",
        "text": (
            "Under Full Coverage policies, loss caused by events other than collision — including "
            "theft, vandalism, fire, flood, falling objects, glass breakage, and animal strikes — "
            "is covered under the comprehensive section, subject to the comprehensive deductible on "
            "the declarations page. Pothole impact is treated as collision, not comprehensive."
        ),
        "keywords": [
            "comprehensive", "hail", "animal strike", "deer", "flood", "vandalism",
            "theft", "fire", "glass", "falling objects", "weather", "storm"
        ],
        "applicable_perils": [
            "hail damage", "animal strike", "flood damage", "vandalism", "theft recovery",
            "storm damage", "garage fire", "carjacking"
        ],
    },
    "CLS-LIAB-003": {
        "clause_id": "CLS-LIAB-003",
        "title": "Liability Only — scope and exclusion of own-vehicle damage",
        "category": "liability",
        "text": (
            "A Liability Only policy covers bodily injury and property damage the insured becomes "
            "legally obligated to pay to third parties. It provides NO coverage for damage to the "
            "insured's own vehicle from any cause. First-party repair estimates, total-loss valuations, "
            "and rental reimbursement are excluded under Liability Only policies."
        ),
        "keywords": [
            "liability only", "exclusion", "third party", "first party", "uncovered",
            "no physical damage", "denial", "own-vehicle damage"
        ],
        "applicable_perils": [],
    },
    "CLS-DED-004": {
        "clause_id": "CLS-DED-004",
        "title": "Deductible application",
        "category": "deductible",
        "text": (
            "The deductible shown on the declarations page is subtracted from the covered loss "
            "amount before payment. The deductible applies once per occurrence, to first-party physical "
            "damage claims only. Deductibles are never applied by the adjudication system itself: "
            "the platform computes the net payable estimate deterministically and the adjuster confirms it."
        ),
        "keywords": ["deductible", "declarations", "subtracted", "occurrence", "first-party"],
        "applicable_perils": [],
    },
    "CLS-TL-005": {
        "clause_id": "CLS-TL-005",
        "title": "Total loss determination",
        "category": "total_loss",
        "text": (
            "A vehicle is a total-loss candidate when the reasonable cost of repair equals or exceeds "
            "seventy-five percent (75%) of the vehicle's actual cash value (ACV) immediately before the "
            "loss. Total-loss candidates must be referred to a licensed total-loss appraiser for valuation "
            "before any repair authorization. Settlement on a total loss is ACV less the applicable deductible "
            "and salvage where retained."
        ),
        "keywords": [
            "total loss", "total-loss", "75%", "seventy-five percent", "threshold",
            "appraiser", "salvage", "valuation", "acv ratio"
        ],
        "applicable_perils": [],
    },
    "CLS-PER-006": {
        "clause_id": "CLS-PER-006",
        "title": "Policy period and lapse",
        "category": "period",
        "text": (
            "Coverage applies only to losses occurring during the policy period shown on the declarations "
            "page, from the effective date through the expiration date inclusive. A loss occurring after the "
            "expiration date is not covered even if a renewal was offered, unless the renewal premium was "
            "received before the loss. Administrative status fields do not extend coverage: the controlling "
            "facts are the policy period dates and the incident date."
        ),
        "keywords": ["policy period", "effective date", "expiration date", "lapse", "expired", "in force"],
        "applicable_perils": [],
    },
    "CLS-FRD-007": {
        "clause_id": "CLS-FRD-007",
        "title": "Fraud investigation and referral",
        "category": "fraud",
        "text": (
            "The insurer may refer any claim presenting indicators of misrepresentation or a staged loss to "
            "the Special Investigation Unit (SIU) before payment. Indicators include, without limitation: "
            "unwitnessed late-night single-vehicle losses, absence of a police report where injury or airbag "
            "deployment is reported, repair estimates inconsistent with the described damage, and multiple "
            "prior claims within twelve months. SIU referral suspends repair authorization until the "
            "investigation resolves."
        ),
        "keywords": [
            "fraud", "siu", "special investigation", "unwitnessed", "late-night", "airbag",
            "inconsistent", "staged", "misrepresentation", "prior claims"
        ],
        "applicable_perils": [],
    },
    "CLS-INJ-008": {
        "clause_id": "CLS-INJ-008",
        "title": "Bodily injury reporting and routing",
        "category": "injury",
        "text": (
            "Claims reporting bodily injury require a filed police report where required by law and route "
            "to an injury-qualified adjuster. A medical report present on a claim whose FNOL reports no "
            "injuries is an inconsistency that must be resolved before payment."
        ),
        "keywords": ["bodily injury", "injury", "medical", "police report", "injury-qualified"],
        "applicable_perils": [],
    },
    "CLS-DOC-009": {
        "clause_id": "CLS-DOC-009",
        "title": "Duties after loss — documentation",
        "category": "documentation",
        "text": (
            "The claimant must submit a completed First Notice of Loss (FNOL), a repair estimate from a "
            "licensed shop, and reasonable proof of the loss. The insurer may delay adjudication while "
            "required documents remain unprocessed or unreadable. Estimates still pending document processing "
            "(OCR) cannot support payment authorization."
        ),
        "keywords": ["duties after loss", "fnol", "repair estimate", "ocr", "documentation", "proof of loss"],
        "applicable_perils": [],
    },
    "CLS-EXC-010": {
        "clause_id": "CLS-EXC-010",
        "title": "General exclusions",
        "category": "exclusions",
        "text": (
            "No coverage applies to: loss while the vehicle is used for racing or speed contests; loss while "
            "used as a public or livery conveyance unless declared; intentional loss caused by any insured; "
            "wear and tear, freezing, or mechanical breakdown absent a covered collision; and loss occurring "
            "outside the policy territory."
        ),
        "keywords": ["exclusions", "racing", "livery", "uber", "lyft", "intentional", "wear and tear", "mechanical"],
        "applicable_perils": [],
    },
    "CLS-RNT-011": {
        "clause_id": "CLS-RNT-011",
        "title": "Rental reimbursement",
        "category": "endorsements",
        "text": (
            "Where the endorsement is present on a Full Coverage policy, reasonable rental costs are reimbursed "
            "while the covered vehicle is unusable due to a covered loss, up to the daily and per-occurrence "
            "limits on the declarations page. Not available under Liability Only policies."
        ),
        "keywords": ["rental reimbursement", "rental", "endorsement", "daily limit"],
        "applicable_perils": [],
    },
    "CLS-PAY-012": {
        "clause_id": "CLS-PAY-012",
        "title": "Payment authority",
        "category": "authority",
        "text": (
            "No automated system output constitutes payment authorization. Every adjudication — including "
            "first-pass approvals — is a recommendation requiring confirmation by a licensed human adjuster "
            "before any financial transaction. High-value payments (net payable above $25,000) require "
            "senior-adjuster confirmation."
        ),
        "keywords": ["payment authority", "human confirmation", "licensed adjuster", "$25,000", "senior-adjuster"],
        "applicable_perils": [],
    },
}


# ── Retriever API ─────────────────────────────────────────────────────────────

def get_clause(clause_id: str) -> Optional[dict]:
    """Retrieve an exact clause by ID (e.g. 'CLS-COLL-001')."""
    return _POLICY_MANUAL_CLAUSES.get(clause_id.upper().strip())


def list_all_clauses() -> list[dict]:
    """List all 12 clauses in canonical order."""
    return list(_POLICY_MANUAL_CLAUSES.values())


def search_policy_clauses(
    query: str,
    loss_category: Optional[str] = None,
    limit: int = 3,
) -> list[dict]:
    """
    Search clauses by query text, keywords, and optional loss category.
    Closed-book: results only ever come from the 12 canonical clauses.
    """
    if not query and not loss_category:
        return list_all_clauses()[:limit]

    scored: list[tuple[float, dict]] = []
    tokens = set(re.findall(r"\w+", query.lower()))

    # Direct clause ID query (e.g. "CLS-COLL-001")
    for clause_id, data in _POLICY_MANUAL_CLAUSES.items():
        if clause_id.lower() in query.lower():
            scored.append((100.0, data))

    for clause_id, data in _POLICY_MANUAL_CLAUSES.items():
        if any(clause_id == s[1]["clause_id"] for s in scored):
            continue

        score = 0.0

        # Match category if specified
        if loss_category and data["category"] == loss_category.lower():
            score += 15.0

        # Perils match
        for peril in data.get("applicable_perils", []):
            if peril.lower() in query.lower():
                score += 25.0

        # Keywords match
        for kw in data.get("keywords", []):
            if kw.lower() in query.lower():
                score += 10.0

        # Title and text token overlap
        title_tokens = set(re.findall(r"\w+", data["title"].lower()))
        text_tokens = set(re.findall(r"\w+", data["text"].lower()))
        score += len(tokens.intersection(title_tokens)) * 4.0
        score += len(tokens.intersection(text_tokens)) * 1.5

        if score > 0:
            scored.append((score, data))

    # Sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:limit]]


def validate_citations(citations: list[str]) -> tuple[bool, list[str]]:
    """
    Validates that every cited clause ID exists in the official manual (DEC-004).
    Returns (all_valid, invalid_or_missing_ids).
    Accepts raw IDs ('CLS-COLL-001') or section citations ('CLS-COLL-001 §4.2').
    """
    if not citations:
        return False, ["NO_CITATIONS_PROVIDED"]

    invalid: list[str] = []
    for cite in citations:
        # Extract clause ID part (e.g. CLS-COLL-001 from 'CLS-COLL-001 §4.2')
        match = re.search(r"CLS-[A-Z]+-\d{3}", cite.upper())
        if not match or match.group(0) not in _POLICY_MANUAL_CLAUSES:
            invalid.append(cite)

    return (len(invalid) == 0, invalid)

