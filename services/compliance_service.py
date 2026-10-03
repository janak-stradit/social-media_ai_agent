"""Applies a user's compliance profile (services/compliance_rules.py,
confirmed on the "My Brand Configuration" page) to generated content:

- build_compliance_block(): the user's active rules as a prompt section, so
  captions are written within them from the start.
- check_caption(): a post-generation review against those rules - flags
  issues, rewrites what can be fixed in the text, and marks what needs a
  human (e.g. a permit number only the business has).

The LLM may only cite rule ids from the active list (anything else is
dropped), and required disclaimers are appended verbatim by code - never
paraphrased by the model. Guidance only, not legal advice.
"""

import re

from services.compliance_rules import applicable_rules

COMPLIANCE_CHECK_SYSTEM_PROMPT = """You are a marketing compliance reviewer. You get a social media caption and
the ONLY advertising rules that apply to this business. Review the caption strictly against those rules.

For each rule the caption violates (or would violate without a disclaimer), return a flag:
- rule_id: exactly one of the provided rule ids - never invent one
- issue: what in the caption breaks the rule, quoting the exact offending words in double quotes
- fix: what was or should be changed
- auto_fixed: true if revised_caption no longer contains the problem; false only if it needs information
  only the business has (a licence/permit/registration number, a signed authorization) or a human decision

Then:
- revised_caption: the caption with every fixable issue fixed - change as little as possible, keep the
  tone, hook, emojis and length. If nothing needed fixing, return it unchanged. A claim the rules forbid
  or that needs evidence (curing/beating a disease, guaranteed results or returns, a named patient or
  client) must be REMOVED, not softened - "beat diabetes in 30 days" is still a disease claim, and
  "Meet John, one of our clients" still identifies a patient: never name or single out any individual
  patient/client, not even by first name.

Disclosure/endorsement rules (#ad, paid partnership) apply only if the caption is sponsored, paid,
gifted or influencer content - a company's own post about itself is not.
- disclaimer_rule_ids: ids of rules whose required disclaimer this post needs because it touches that
  rule's topic (e.g. an investment return mention -> the investment disclaimer). Do NOT write the
  disclaimer text yourself - it is appended exactly as required.

Only flag real problems in THIS caption - a rule that simply applies to the industry is not a flag.
Return ONLY JSON: {"flags": [{"rule_id": "", "issue": "", "fix": "", "auto_fixed": true}],
"revised_caption": "", "disclaimer_rule_ids": []}"""


def active_rules(profile: dict | None) -> list[dict]:
    """The user's enforceable rules - their industry and markets, minus any
    they marked not applicable. [] without a profile or any markets."""
    if not profile:
        return []
    regions = profile.get("compliance_regions") or profile.get("regions_detected") or []
    industry = profile.get("industry_category") or profile.get("industry_category_detected")
    rules = applicable_rules(industry, regions, profile.get("compliance_excluded_rules") or [])
    return [rule for rule in rules if not rule["excluded"]]


def active_rules_for_user(user_id: int | None) -> list[dict]:
    if not user_id:
        return []
    try:
        from db import get_user_brand_profile

        return active_rules(get_user_brand_profile(user_id))
    except Exception:
        return []


def build_compliance_block(rules: list[dict]) -> str:
    if not rules:
        return ""
    lines = ["\n--- COMPLIANCE RULES (advertising regulations for this business's industry and markets) ---"]
    for rule in rules:
        lines.append(f"[{rule['framework']} - {', '.join(rule['regions'])}]")
        lines.extend(f"  - {item}" for item in rule["content_rules"])
        if rule.get("required_disclaimer"):
            lines.append(f'  - When the post touches this topic, it must carry: "{rule["required_disclaimer"]}"')
    lines.append("Content that cannot satisfy a rule must not be generated in that form.")
    lines.append("--- END COMPLIANCE RULES ---\n")
    return "\n".join(lines)


_QUOTED_RE = re.compile(r"[\"“]([^\"”]{4,})[\"”]")


def _verify_fixed(issue: str, final_caption: str, claimed: bool) -> bool:
    """Don't trust the model's own auto_fixed label: the issue quotes the
    offending words, so check whether they're still in the final caption.
    With no quote to check, fall back to the model's claim."""
    quotes = [q.strip().rstrip(".!,") for q in _QUOTED_RE.findall(issue)]
    quotes = [q for q in quotes if len(q) >= 4]
    if not quotes:
        return claimed
    text = final_caption.lower()
    return not any(q.lower() in text for q in quotes)


def _rules_text(rules: list[dict]) -> str:
    return "\n".join(
        f"- {rule['id']} ({rule['framework']}, {', '.join(rule['regions'])}): " + " ".join(rule["content_rules"])
        + (" [has a required disclaimer]" if rule.get("required_disclaimer") else "")
        for rule in rules
    )


def _finish(plan: dict, caption: str, rules: list[dict]) -> dict:
    """One caption's result from the model's review: flags limited to the
    provided rules, fixes verified, required disclaimers appended verbatim."""
    by_id = {rule["id"]: rule for rule in rules}
    plan = plan if isinstance(plan, dict) else {}
    final = (plan.get("revised_caption") or "").strip() or caption

    flags = []
    for flag in plan.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        rule = by_id.get(str(flag.get("rule_id")))
        if not rule:  # the model may only cite provided rules
            continue
        issue = str(flag.get("issue") or "").strip()
        flags.append(
            {
                "rule_id": rule["id"],
                "framework": rule["framework"],
                "severity": rule["severity"],
                "issue": issue,
                "fix": str(flag.get("fix") or "").strip(),
                "auto_fixed": _verify_fixed(issue, final, bool(flag.get("auto_fixed"))),
            }
        )
    disclaimers_added = []
    for rule_id in dict.fromkeys(plan.get("disclaimer_rule_ids") or []):
        disclaimer = (by_id.get(str(rule_id)) or {}).get("required_disclaimer")
        if disclaimer and disclaimer.lower() not in final.lower():
            final = f"{final}\n\n{disclaimer}"
            disclaimers_added.append(disclaimer)

    return {
        "caption": final,
        "rules_checked": len(rules),
        "flags": flags,
        "disclaimers_added": disclaimers_added,
        "needs_attention": sum(1 for f in flags if not f["auto_fixed"]),
    }


def check_caption(caption: str, platform: str, rules: list[dict], llm) -> tuple[dict | None, dict]:
    """Reviews one caption against the active rules. Returns (result, usage);
    result is None when there are no rules or no caption to check.

    result: {caption (final text, fixes + disclaimers applied), rules_checked,
    flags [{rule_id, framework, severity, issue, fix, auto_fixed}],
    disclaimers_added [text], needs_attention (count of flags not auto-fixed)}"""
    if not rules or not (caption or "").strip():
        return None, {}
    # Rules first: the unchanging part leads, so providers that cache repeated
    # prompt prefixes can reuse it
    user_prompt = f"RULES:\n{_rules_text(rules)}\n\nPLATFORM: {platform}\n\nCAPTION:\n{caption}"
    plan, usage = llm.generate_json(
        COMPLIANCE_CHECK_SYSTEM_PROMPT, user_prompt, temperature=0.1, max_tokens=1500, return_usage=True
    )
    return _finish(plan, caption, rules), usage or {}


_BATCH_ADDENDUM = """

You will receive SEVERAL captions, one per platform. Review each one on its own, exactly as described
above - a problem in one caption is never a flag on another. Return ONLY JSON with one key per platform:
{"<platform>": {"flags": [...], "revised_caption": "", "disclaimer_rule_ids": []}}"""


def check_captions(captions: dict[str, str], rules: list[dict], llm) -> tuple[dict[str, dict | None], dict]:
    """Every platform's caption reviewed in ONE call (same rules, same review
    as check_caption). Each AI call carries ~4k tokens of fixed provider
    overhead and the rules text, so this saves both for every extra platform.
    A platform missing from the answer is checked on its own. Returns
    ({platform: result or None}, usage)."""
    captions = {p: c for p, c in captions.items() if (c or "").strip()}
    if not rules or not captions:
        return {p: None for p in captions}, {}
    if len(captions) == 1:
        (p, c), = captions.items()
        result, usage = check_caption(c, p, rules, llm)
        return {p: result}, usage
    blocks = "\n\n".join(f"=== PLATFORM: {p} ===\nCAPTION:\n{c}" for p, c in captions.items())
    try:
        plans, usage = llm.generate_json(
            COMPLIANCE_CHECK_SYSTEM_PROMPT + _BATCH_ADDENDUM, f"RULES:\n{_rules_text(rules)}\n\n{blocks}",
            temperature=0.1, max_tokens=1500 * len(captions), return_usage=True,
        )
    except Exception:  # noqa: BLE001 - fall back to one check per platform below
        plans, usage = {}, {}
    plans = {str(k).lower(): v for k, v in plans.items()} if isinstance(plans, dict) else {}
    usage = dict(usage or {})
    results = {}
    for platform, caption in captions.items():
        plan = plans.get(platform)
        if isinstance(plan, dict) and ("revised_caption" in plan or "flags" in plan):
            results[platform] = _finish(plan, caption, rules)
            continue
        result, single_usage = check_caption(caption, platform, rules, llm)
        results[platform] = result
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
            usage[key] = (usage.get(key) or 0) + ((single_usage or {}).get(key) or 0)
    return results, usage


