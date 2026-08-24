"""
Pre-built compliance profile templates.

Each template is a dict that matches the script_sections_json shape stored in
compliance_profiles.  An org picks one at signup and can switch to another at
any time from the checklist page; applying one creates a new active profile
rather than overwriting the old, so nothing is lost.

These are STARTING POINTS, not legal advice.  The requirements below are drawn
from widely-published obligations (the FTC Telemarketing Sales Rule, the Credit
Repair Organizations Act, CMS marketing rules, state rights of rescission), but
every organisation's obligations differ and the checklist is meant to be edited.
The product's position is that the compliance logic belongs to the customer.
"""

DEBT_SETTLEMENT_TEMPLATE: dict = {
    "sections": [
        {
            "name": "Approval Script",
            "key": "approval_script",
            "items": [
                {
                    "name": "Approval amount stated",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Monthly payment stated",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Program term stated",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "No prepayment penalty disclosed",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Loan decline context explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Creditor agreement process explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Interest elimination mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Balance reduction mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Total payback amount stated",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Client savings explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "FDIC-insured account mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "No upfront fees stated",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Pause in payments explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Card usage pause explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Credit impact warning given",
                    "required": True,
                    "notes": "Must be clearly stated as a short-term impact.",
                },
                {
                    "name": "Credit healing mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Accelerator Loan mentioned",
                    "required": False,
                    "notes": "Optional — if mentioned, item 18 becomes required.",
                },
                {
                    "name": "Accelerator Loan not guaranteed",
                    "required": False,
                    "notes": "Conditional on item 17 being mentioned.",
                },
            ],
        },
        {
            "name": "Post-Enrollment Script",
            "key": "post_enrollment_script",
            "items": [
                {
                    "name": "Early months importance explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Payment pause reminder given",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Gradual process explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Auto-pays turn off instruction given",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Authorized users removal mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Welcome packet email mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Portal email mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Creditor calls explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "How to handle creditor calls explained",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Agent phone number provided",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Advisory team backup mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Quiet periods mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Next-day follow-up mentioned",
                    "required": True,
                    "notes": "",
                },
                {
                    "name": "Welcome team contact (light)",
                    "required": True,
                    "notes": "Light adherence — any reasonable mention counts.",
                },
                {
                    "name": "Welcome team primer (light)",
                    "required": True,
                    "notes": "Light adherence — expectation-setting counts.",
                },
            ],
        },
    ],
    "auto_fail_phrases": [
        {
            "phrase": "0% interest",
            "description": "Misrepresents program terms.",
        },
        {
            "phrase": "guaranteed",
            "description": "Cannot guarantee outcomes in debt settlement.",
        },
        {
            "phrase": "can't get sued",
            "description": "Legal outcome guarantee — prohibited.",
        },
        {
            "phrase": "consolidation loan",
            "description": "Incorrect program description.",
        },
        {
            "phrase": "our lawyers",
            "description": "Implies in-house legal representation.",
        },
        {
            "phrase": "tax free",
            "description": "Misrepresents tax implications of forgiven debt.",
        },
        {
            "phrase": "no impact on credit",
            "description": "False claim — program does affect credit.",
        },
        {
            "phrase": "fix your credit",
            "description": "Guarantee of credit repair — prohibited.",
        },
        {
            "phrase": "credit will recover in",
            "description": "Recovery timeline guarantee — prohibited.",
        },
        {
            "phrase": "we will make your payments",
            "description": "Incorrect — client makes payments to escrow.",
        },
        {
            "phrase": "locked in agreements",
            "description": "Pre-negotiated creditor deals — prohibited.",
        },
    ],
    # Debt-settlement-specific informational signals for the grader. Other
    # industry templates omit this so horizontal orgs are not scored against
    # settlement program-flip / ineligibility logic.
    "grader_extensions": ["program_flip", "ineligible_accounts"],
}

BLANK_TEMPLATE: dict = {
    "sections": [],
    "auto_fail_phrases": [],
}


def _sec(name, key, items):
    return {"name": name, "key": key,
            "items": [{"name": n, "required": r, "notes": t} for n, r, t in items]}


def _ph(pairs):
    return [{"phrase": p, "description": d} for p, d in pairs]


# ── Tax relief / resolution ───────────────────────────────────────────────────
TAX_RELIEF_TEMPLATE: dict = {
    "sections": [
        _sec("Qualification & disclosure", "qualification", [
            ("Agent identifies company and states this is a paid tax resolution service", True,
             "Must be clear the caller is a private company, not the IRS or a government program."),
            ("No affiliation with the IRS or any government agency stated", True,
             "Any implication of government affiliation is a serious violation."),
            ("Total fee and what it covers stated", True,
             "A specific dollar figure or clear fee basis, not 'it depends'."),
            ("Explains that penalties and interest continue to accrue during resolution", True,
             "Clients routinely assume enrolling pauses accrual."),
            ("No promise of a specific settlement amount or outcome", True,
             "Eligibility for any IRS program is determined by the IRS, never by the seller."),
            ("Explains that eligibility depends on IRS review of the client's finances", True, ""),
        ]),
        _sec("Program mechanics", "mechanics", [
            ("Explains what the client must provide (returns, financials)", True, ""),
            ("Explains realistic timelines rather than a fixed promise", True,
             "Ranges are fine; a guaranteed date is not."),
            ("Refund or cancellation policy stated", True, ""),
            ("Client confirms understanding before payment is taken", True, ""),
        ]),
    ],
    "auto_fail_phrases": _ph([
        ("pennies on the dollar", "Classic prohibited outcome claim in tax resolution."),
        ("guaranteed settlement", "No outcome can be guaranteed; the IRS decides."),
        ("we work for the IRS", "False claim of government affiliation."),
        ("government program", "Implies a federal program where none exists."),
        ("stop all penalties", "Penalties and interest continue during resolution."),
        ("erase your tax debt", "Misrepresents what resolution can achieve."),
        ("special relationship with the IRS", "False claim of privileged access."),
        ("you qualify", "Only the IRS determines qualification."),
    ]),
}

# ── Residential solar sales ───────────────────────────────────────────────────
SOLAR_TEMPLATE: dict = {
    "sections": [
        _sec("Identification & offer", "identification", [
            ("Agent states company name and that this is a sales call", True, ""),
            ("No claim of affiliation with the utility or any government agency", True,
             "'The utility sent me' and similar are serious violations."),
            ("States clearly whether this is a loan, lease, or PPA", True,
             "Clients frequently believe a financed system is a government program."),
            ("Total system cost or total financed amount stated", True, ""),
            ("Monthly payment amount and term stated", True, ""),
            ("Explains that the tax credit depends on the client's tax liability", True,
             "It is not a rebate and not everyone can use it."),
        ]),
        _sec("Savings & obligations", "savings", [
            ("Savings presented as an estimate, with assumptions stated", True,
             "Any utility-rate escalation assumption must be spoken, not implied."),
            ("No guarantee of a specific bill amount or savings figure", True, ""),
            ("Discloses any lien or UCC filing against the property", True, ""),
            ("Explains what happens on sale of the home", True, ""),
            ("Explains the cancellation right and its deadline", True,
             "Most jurisdictions provide a three-day right of rescission."),
            ("Roof condition and any required work discussed", False, ""),
        ]),
    ],
    "auto_fail_phrases": _ph([
        ("free solar", "Nothing about a financed or leased system is free."),
        ("government program", "Implies a state or federal program where none exists."),
        ("the utility sent us", "False claim of utility affiliation."),
        ("guaranteed savings", "Savings depend on usage and utility rates."),
        ("no cost to you", "Misrepresents a loan, lease or PPA."),
        ("your bill will be zero", "A specific bill guarantee cannot be made."),
        ("everyone gets the tax credit", "The credit depends on the client's tax liability."),
        ("rates always go up", "Presents an assumption as a certainty."),
    ]),
}

# ── Credit repair (CROA) ──────────────────────────────────────────────────────
CREDIT_REPAIR_TEMPLATE: dict = {
    "sections": [
        _sec("Required CROA disclosures", "croa", [
            ("Discloses the client's right to dispute items themselves for free", True,
             "The Credit Repair Organizations Act requires this."),
            ("Discloses the right to cancel the contract within three business days", True,
             "CROA cancellation right; must be stated, not just printed."),
            ("States that no fee is charged before services are performed", True,
             "Advance-fee collection is prohibited under CROA."),
            ("Explains that accurate negative information cannot be removed", True,
             "Only inaccurate or unverifiable items can be disputed."),
            ("No guarantee of a specific score increase", True, ""),
            ("Explains a realistic timeframe rather than a promised date", True, ""),
        ]),
        _sec("Scope of service", "scope", [
            ("Explains what the service will actually do on the client's behalf", True, ""),
            ("Monthly fee and total expected cost stated", True, ""),
            ("Never advises the client to misrepresent anything to a bureau or creditor", True,
             "Advising a false statement is a CROA violation and an automatic fail."),
            ("Client confirms understanding before enrolment", True, ""),
        ]),
    ],
    "auto_fail_phrases": _ph([
        ("guaranteed score increase", "No score outcome can be guaranteed."),
        ("remove accurate", "Accurate information cannot be removed."),
        ("new credit identity", "Describes CPN fraud — illegal."),
        ("credit privacy number", "CPNs are fraudulent; serious violation."),
        ("dispute everything", "Blanket disputes of accurate items are improper."),
        ("we can delete anything", "False claim about dispute outcomes."),
        ("pay us first", "Advance fees are prohibited under CROA."),
        ("tell them you didn't", "Advising a false statement to a bureau or creditor."),
    ]),
}

# ── Insurance: final expense and Medicare-adjacent ────────────────────────────
INSURANCE_TEMPLATE: dict = {
    "sections": [
        _sec("Identification & consent", "identification", [
            ("Agent states full name and the agency they represent", True, ""),
            ("States this is a call about insurance and may lead to a sale", True,
             "CMS requires the sales nature of the call to be clear up front."),
            ("Discloses the call is recorded, where required", True, ""),
            ("States no affiliation with Medicare, Social Security or any government agency", True,
             "A serious and frequently cited violation."),
            ("Confirms speaking with the intended person before discussing details", True, ""),
        ]),
        _sec("Product & underwriting", "product", [
            ("Names the carrier and the specific product", True, ""),
            ("Premium amount and payment frequency stated", True, ""),
            ("Explains any waiting or graded-benefit period", True,
             "Beneficiaries are often surprised by a two-year graded period."),
            ("No guarantee of acceptance where underwriting applies", True, ""),
            ("Explains what happens if a payment is missed", True, ""),
            ("Health questions asked as written, without coaching the answer", True,
             "Coaching an answer is an automatic fail."),
            ("Free-look or cancellation period stated", False, ""),
        ]),
    ],
    "auto_fail_phrases": _ph([
        ("from medicare", "False claim of government affiliation."),
        ("government benefit", "Misrepresents a private insurance product."),
        ("everyone is approved", "Cannot promise acceptance where underwriting applies."),
        ("no health questions", "False where the product is underwritten."),
        ("just say no to that one", "Coaching a health answer — automatic fail."),
        ("this is free", "Misrepresents a premium-bearing product."),
        ("your benefits will be cut", "Fear-based misrepresentation."),
        ("social security sent me", "False claim of government affiliation."),
    ]),
}

# ── Registry ──────────────────────────────────────────────────────────────────
# Ordered; the picker renders them in this order.
TEMPLATE_LIBRARY: list[dict] = [
    {
        "key": "debt_settlement",
        "name": "Debt settlement",
        "industry": "Debt relief",
        "summary": "Approval script and post-enrolment disclosures for a debt "
                   "settlement programme, including FTC-driven outcome-claim limits.",
        "checklist": DEBT_SETTLEMENT_TEMPLATE,
    },
    {
        "key": "tax_relief",
        "name": "Tax relief",
        "industry": "Tax resolution",
        "summary": "Fee and eligibility disclosures for IRS resolution work, with the "
                   "outcome and government-affiliation claims that draw enforcement.",
        "checklist": TAX_RELIEF_TEMPLATE,
    },
    {
        "key": "solar",
        "name": "Solar sales",
        "industry": "Residential solar",
        "summary": "Financing type, true cost, savings assumptions, liens and the "
                   "cancellation right — the areas solar complaints concentrate in.",
        "checklist": SOLAR_TEMPLATE,
    },
    {
        "key": "credit_repair",
        "name": "Credit repair",
        "industry": "Credit services",
        "summary": "The Credit Repair Organizations Act disclosures, advance-fee "
                   "prohibition, and the claims that make a call unlawful.",
        "checklist": CREDIT_REPAIR_TEMPLATE,
    },
    {
        "key": "insurance",
        "name": "Insurance sales",
        "industry": "Final expense / Medicare-adjacent",
        "summary": "Identification, recording and sales-nature disclosure, plus "
                   "underwriting and graded-benefit explanations.",
        "checklist": INSURANCE_TEMPLATE,
    },
    {
        "key": "blank",
        "name": "Start from scratch",
        "industry": "Any",
        "summary": "An empty checklist. Add your own sections, requirements and "
                   "auto-fail phrases from nothing.",
        "checklist": BLANK_TEMPLATE,
    },
]

TEMPLATES_BY_KEY: dict[str, dict] = {t["key"]: t for t in TEMPLATE_LIBRARY}


def template_stats(checklist: dict) -> dict:
    """Counts for the picker, derived rather than hand-maintained."""
    sections = checklist.get("sections", []) or []
    # NOT "items": Jinja resolves stats.items to dict.items (the method) before
    # the key. The same collision shipped a Python repr onto the upload page.
    return {
        "sections": len(sections),
        "item_count": sum(len(s.get("items", []) or []) for s in sections),
        "required": sum(1 for s in sections for i in (s.get("items") or [])
                        if i.get("required", True)),
        "phrases": len(checklist.get("auto_fail_phrases", []) or []),
    }


def get_template(key: str) -> dict | None:
    return TEMPLATES_BY_KEY.get(key)
