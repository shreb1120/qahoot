"""
Pre-built compliance profile templates.

Each template is a dict that matches the script_sections_json shape stored in
compliance_profiles.  New orgs can start from one of these or from a blank
profile.  In Phase 2 the Claude prompt builder reads this structure directly.
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
}

BLANK_TEMPLATE: dict = {
    "sections": [],
    "auto_fail_phrases": [],
}
