"""Aptiro backend module: `app.modules.scoring` (Phase 9 PR-11).

The deterministic scoring function(s) extracted verbatim from
`legacy.py`. `legacy.py` re-imports these names so the test contract
(`import app as A; A.score_job`, `A._structured_requirements`) and
PR-10's `modules/strategies` (which imports `score_job` from
`app.legacy`) are unchanged.

Every helper/constant these functions depend on still lives in
`legacy.py` and is imported below. legacy triggers
`from app.modules.scoring import ...` only AFTER all of those are
defined, so there is no circular-import or ordering hazard.

# APTIRO_PHASE9_PR11_SCORING_MARKER
"""

from app.legacy import (
    DEFAULT_WEIGHTS,
    WorkMode,
    _AI_TERMS,
    _LEADERSHIP,
    _MUST_HDR,
    _NICE_HDR,
    _NICE_INLINE,
    _REQ_LINE,
    _YEARS,
    _candidate_profile,
    _domains_in,
    _extract_skills,
    _has_term,
    _seniority_rank,
    _tokens,
    _weight,
    embeddings,
)

# ═══════════════════════════════════════════════════════════════
#  Extracted verbatim from legacy.py (Phase 9 PR-11)
# ═══════════════════════════════════════════════════════════════

def _structured_requirements(text):
    """Split requirements into must-have vs nice-to-have and pull out
    seniority / skills / domains. Purely additive: the flat list from
    _extract_requirements is still produced and stored separately."""
    must, nice = [], []
    section = None  # None | "must" | "nice"
    for raw in (text or "").splitlines():
        low = raw.strip().lower()
        if low:
            if any(h in low for h in _NICE_HDR) and len(low) < 60:
                section = "nice"
            elif any(h in low for h in _MUST_HDR) and len(low) < 60:
                section = "must"
        m = _REQ_LINE.match(raw)
        if not m:
            continue
        item = m.group(1).strip()
        if not item or len(item) < 6:
            continue
        bucket = nice if (section == "nice"
                          or _NICE_INLINE.search(item)) else must
        if item not in must and item not in nice:
            bucket.append(item)
    blob = (text or "").lower()
    yrs = [int(y) for y in _YEARS.findall(blob)]
    seniority = _seniority_rank(blob)
    skills = sorted(set(_extract_skills(blob)))
    domains = sorted(set(_domains_in(blob)))
    return {
        "must_have": must[:20],
        "nice_to_have": nice[:20],
        "min_years": min(yrs) if yrs else None,
        "seniority_rank": seniority,
        "skills": skills,
        "domains": domains,
    }


def score_job(session, job, strategy):
    w = strategy.weights or dict(DEFAULT_WEIGHTS)
    prof = _candidate_profile(session)
    job_low = " ".join([job.title or "", job.description_text or "",
                        " ".join(job.requirements or [])]).lower()
    comps = []

    def comp(key, label, frac, detail, ev=None):
        weight = _weight(w, key)
        earned = round(weight * max(0.0, min(1.0, frac)), 2)
        evidence = []
        seen = set()
        for cid in (ev or []):
            if cid in seen or cid not in prof["by_claim"]:
                continue
            seen.add(cid)
            evidence.append({"claim_id": cid,
                             "snippet": prof["by_claim"][cid]})
            if len(evidence) >= 6:
                break
        comps.append({"key": key, "label": label, "weight": weight,
                      "earned": earned, "detail": detail,
                      "evidence": evidence})

    inc = [c.lower() for c in (strategy.include_companies or [])]
    exc = [c.lower() for c in (strategy.exclude_companies or [])]
    excluded = job.company.lower() in exc

    targets = [t.lower() for t in (strategy.target_roles or [])]
    title_low = (job.title or "").lower()
    if targets:
        best = max((len(_tokens(t) & _tokens(title_low)) /
                    max(1, len(_tokens(t))) for t in targets), default=0)
        comp("role_alignment", "Role alignment", best,
             ("Title overlaps your target role(s)." if best > 0.4 else
              "Limited overlap with your target role(s)."))
    else:
        comp("role_alignment", "Role alignment", 0.5,
             "No target roles set - neutral credit.")

    job_sr = _seniority_rank(job_low)
    diff = abs(job_sr - prof["seniority"])
    comp("seniority_alignment", "Seniority alignment",
         max(0.0, 1.0 - diff / 4.0),
         "Role seniority ~%d vs your ~%d." % (job_sr, prof["seniority"]),
         prof["senior_src"])

    job_sk = set(_extract_skills(job_low))
    prof_sk = set(prof["skills"])
    matched = sorted(job_sk & prof_sk)
    skill_ev = []
    for sk in matched:
        skill_ev.extend(prof["skill_src"].get(sk, []))
    comp("core_skills", "Core skills coverage",
         (len(matched) / len(job_sk)) if job_sk else 0.5,
         ("Matched: %s." % ", ".join(matched)) if matched else
         "No overlapping skills detected from approved claims.",
         skill_ev)

    jd = _domains_in(job_low)
    shared = [d for d in prof["domains"] if d in jd]
    dom_ev = []
    for d in shared:
        dom_ev.extend(prof["domain_src"].get(d, []))
    comp("domain", "Domain fit",
         (len(shared) / len(jd)) if jd else 0.5,
         ("Shared domain(s): %s." % ", ".join(shared)) if shared else
         (("Posting domain(s) %s not in your profile." % ", ".join(jd))
          if jd else "No clear domain signal - neutral credit."),
         dom_ev)

    job_lead = any(k in job_low for k in _LEADERSHIP) or job_sr >= 4
    if prof["leadership"] and job_lead:
        lf, ld = 1.0, "Your leadership scope matches a leadership role."
    elif prof["leadership"]:
        lf, ld = 0.6, "You show leadership scope; role is more IC."
    elif job_lead:
        lf, ld = 0.3, "Role expects leadership; limited evidence."
    else:
        lf, ld = 0.5, "Neither side emphasizes leadership scope."
    comp("leadership_scope", "Leadership scope", lf, ld,
         prof["lead_src"])

    cand_ai = [t for t in _AI_TERMS if _has_term(prof["text"], t)]
    job_ai = [t for t in _AI_TERMS if _has_term(job_low, t)]
    shared_ai = sorted({t for t in cand_ai if t in job_ai})
    ai_ev = []
    for t in shared_ai:
        ai_ev.extend(prof["ai_src"].get(t, []))
    comp("ai_technical", "AI / technical depth",
         min(1.0, len(shared_ai) / 3.0) if job_ai else 0.4,
         ("Shared AI/technical signals: %s." % ", ".join(shared_ai[:6]))
         if shared_ai else
         ("Role is AI/technical but profile shows little overlap."
          if job_ai else "Role is not strongly AI/technical."),
         ai_ev)

    comp("evidence_strength", "Evidence strength", prof["evidence"],
         ("Backed by %d usable claim(s), %d grounded resume truth; "
          "avg evidence quality %.2f."
          % (prof["n"], prof["grounded"], prof["evidence"]))
         if prof["n"] else
         "No usable approved claims yet - approve claims in Profile "
         "Vault.",
         prof["all_ids"])

    pref = 1.0
    pbits = []
    if strategy.work_mode != WorkMode.any and \
            job.work_mode != strategy.work_mode:
        pref -= 0.4
        pbits.append("work mode differs")
    if strategy.salary_min and job.salary_max and \
            job.salary_max < strategy.salary_min:
        pref -= 0.4
        pbits.append("below salary floor")
    comp("preferences", "Preference match", max(0.0, pref),
         ("Mismatch: %s." % ", ".join(pbits)) if pbits else
         "Work mode and salary fit your preferences.")

    notes_kw = [t for t in _tokens(strategy.targeting_notes or "")
                if len(t) > 3 and t in job_low]
    if excluded:
        comp("strategy_boost", "Strategy boost", 0.0,
             "%s is on your exclude list." % job.company)
    elif any(cc == title_low or cc in job.company.lower() for cc in inc):
        comp("strategy_boost", "Strategy boost", 1.0,
             "%s is on your include/priority list." % job.company)
    elif notes_kw:
        comp("strategy_boost", "Strategy boost",
             min(1.0, len(notes_kw) / 3.0),
             "Targeting-note keywords present: %s."
             % ", ".join(sorted(set(notes_kw))[:5]))
    else:
        comp("strategy_boost", "Strategy boost", 0.3,
             "No include-list or targeting-note signal; base credit.")

    earned = round(sum(c["earned"] for c in comps), 2)
    maxp = sum(c["weight"] for c in comps) or 1
    pct = 0 if excluded else max(0, min(100, round(100 * earned / maxp)))
    prof_tok = _tokens(prof["text"]) | _tokens(" ".join(prof["skills"]))
    missing = [r for r in (job.requirements or [])
               if _tokens(r) and not (_tokens(r) & prof_tok)]
    if excluded:
        summary = ("Excluded: %s is on your exclude list. Shown for "
                   "transparency at score 0." % job.company)
    else:
        band = ("strong" if pct >= 75 else
                "moderate" if pct >= 50 else "weak")
        top = max(comps, key=lambda c: c["earned"])
        summary = "%d/100 - %s match. Strongest on: %s." % (
            pct, band, top["label"])
    # --- Secondary semantic signal (Phase 2) ---------------------------
    # A clearly-labelled SECONDARY indicator only. It is computed from a
    # deterministic mock embedding by default and NEVER feeds `pct` or
    # changes ranking - the weighted score above stays the single source
    # of truth. It exists so the UI can show whether an opaque vector
    # model "agrees" with the explainable score.
    ep = embeddings.get_embedding_provider()
    prof_vec = ep.embed(" ".join(
        [prof["text"], " ".join(prof["skills"])]))
    job_vec = ep.embed(" ".join(
        [job.title or "", job.description_text or "",
         " ".join(job.requirements or [])]))
    sim = embeddings.cosine(prof_vec, job_vec)
    semantic = {
        "provider": ep.name,
        "similarity": sim,
        "label": "secondary signal",
        "affects_score": False,
        "note": ("Embedding cosine similarity between your approved "
                 "evidence and this posting. Secondary only - it does "
                 "not change the %d/100 score or the ranking." % pct),
        "agreement": ("aligned" if abs(sim * 100 - pct) <= 20 else
                      "diverges"),
    }

    return {"job_id": job.id, "score": pct, "earned_points": earned,
            "max_points": maxp, "components": comps,
            "matched_skills": matched, "missing_requirements": missing[:8],
            "excluded": excluded, "summary": summary,
            "structured_requirements": (
                job.structured_requirements or {}),
            "semantic": semantic}
