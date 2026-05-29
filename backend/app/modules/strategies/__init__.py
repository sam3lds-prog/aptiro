"""Aptiro backend module: `app.modules.strategies` (Phase 9 PR-10).

The multi-strategy domain — the six tuned presets, the strategy Pydantic
schemas, the plural `/api/strategies` router with all of its endpoints,
the preview helpers, and the seed-presets route — extracted verbatim
from `legacy.py`. The dead duplicate copy legacy.py had accumulated was
removed in the same change.

The `Strategy` table model and the singular `StrategyUpsert` /
`StrategyRead` schemas stay in `legacy.py`; this module imports them.
legacy imports this module only AFTER those symbols (and `score_job`)
are defined, so every dependency below resolves at import time — no lazy
bridges, no circular-import hazard.
"""

from datetime import datetime  # noqa: F401
from typing import List, Optional  # noqa: F401

from fastapi import APIRouter, Depends, HTTPException  # noqa: F401
from pydantic import BaseModel  # noqa: F401
from sqlmodel import Session, select  # noqa: F401

from app.core.identity import _uid  # noqa: F401
from app.db.engine import get_session  # noqa: F401
from app.modules.jobs import JobPosting  # noqa: F401
from app.legacy import (  # noqa: F401
    Strategy, StrategyUpsert, StrategyRead,
    Aggressiveness, WorkMode, DEFAULT_WEIGHTS, score_job, _now,
    _active_strategy, _strategy_read,
)


# ═══════════════════════════════════════════════════════════════════════
#  Extracted verbatim from legacy.py (Phase 9 PR-10)
# ═══════════════════════════════════════════════════════════════════════
STRATEGY_PRESETS = [
    {"name": "AI PM",
     "target_roles": ["AI Product Manager", "AI/ML Product Manager", "Senior Product Manager, AI"],
     "aggressiveness": "balanced", "score_threshold": 55,
     "weights": {"role_alignment": 18, "seniority_alignment": 10, "core_skills": 22,
                 "domain": 6, "leadership_scope": 7, "ai_technical": 18,
                 "evidence_strength": 10, "preferences": 6, "strategy_boost": 3},
     "targeting_notes": "AI-native product roles. LLM, agentic, applied ML, embeddings, evals."},
    {"name": "Healthcare AI PM",
     "target_roles": ["Healthcare AI Product Manager", "Clinical AI Product Manager", "Health Tech PM"],
     "aggressiveness": "balanced", "score_threshold": 60,
     "weights": {"role_alignment": 16, "seniority_alignment": 10, "core_skills": 18,
                 "domain": 16, "leadership_scope": 6, "ai_technical": 16,
                 "evidence_strength": 11, "preferences": 4, "strategy_boost": 3},
     "targeting_notes": "Healthcare, clinical AI, EMR/EHR-integrated AI, lab diagnostics."},
    {"name": "Senior Product Leadership",
     "target_roles": ["Director of Product", "Group Product Manager", "Head of Product"],
     "aggressiveness": "conservative", "score_threshold": 65,
     "weights": {"role_alignment": 14, "seniority_alignment": 18, "core_skills": 14,
                 "domain": 10, "leadership_scope": 18, "ai_technical": 8,
                 "evidence_strength": 12, "preferences": 4, "strategy_boost": 2},
     "targeting_notes": "Leadership scope, cross-org influence, P&L responsibility."},
    {"name": "Nonprofit / Mission Tech",
     "target_roles": ["Product Manager", "Senior Product Manager", "Director of Product"],
     "aggressiveness": "balanced", "score_threshold": 50,
     "weights": {"role_alignment": 14, "seniority_alignment": 10, "core_skills": 16,
                 "domain": 14, "leadership_scope": 12, "ai_technical": 10,
                 "evidence_strength": 12, "preferences": 6, "strategy_boost": 6},
     "include_companies": ["FamilySearch", "Khan Academy", "Code for America"],
     "targeting_notes": "Mission-driven, public-benefit, faith-based, civic, genealogy."},
    {"name": "Enterprise SaaS PM",
     "target_roles": ["Senior Product Manager", "Principal Product Manager", "Platform PM"],
     "aggressiveness": "balanced", "score_threshold": 55,
     "weights": {"role_alignment": 15, "seniority_alignment": 12, "core_skills": 20,
                 "domain": 12, "leadership_scope": 12, "ai_technical": 8,
                 "evidence_strength": 11, "preferences": 6, "strategy_boost": 4},
     "targeting_notes": "Enterprise SaaS, platform, B2B, workflow automation, integrations."},
    {"name": "Adjacent Stretch",
     "target_roles": ["Product Manager", "Senior Product Manager", "Technical PM"],
     "aggressiveness": "opportunistic", "score_threshold": 35,
     "weights": {"role_alignment": 10, "seniority_alignment": 8, "core_skills": 16,
                 "domain": 8, "leadership_scope": 10, "ai_technical": 10,
                 "evidence_strength": 16, "preferences": 12, "strategy_boost": 10},
     "targeting_notes": "Stretch roles; transferable skills and strong evidence beat title match."},
]


class StrategyCreate(StrategyUpsert):
    activate: bool = False


class StrategyListItem(BaseModel):
    id: str
    name: str
    is_active: bool
    aggressiveness: Aggressiveness
    score_threshold: int
    target_roles: List[str]
    work_mode: WorkMode
    updated_at: datetime


class StrategyPreviewCounts(BaseModel):
    jobs_considered: int
    above_threshold: int
    strong: int
    moderate: int
    weak: int
    excluded: int
    avg_score: float
    top_score: int
    score_threshold: int
    threshold_passing_titles: List[str] = []


class StrategyPreviewOut(BaseModel):
    strategy_id: Optional[str] = None
    strategy_name: str
    current: StrategyPreviewCounts
    active: Optional[StrategyPreviewCounts] = None
    summary: str


class SeedPresetsOut(BaseModel):
    created: List[StrategyListItem]
    skipped_existing: List[str]
    note: str = "Seeding is idempotent: existing presets are left untouched."


strategies_router = APIRouter(prefix="/api/strategies", tags=["strategies"])


def _list_strategies(session) -> List[Strategy]:
    rows = session.exec(select(Strategy).where(
        Strategy.owner_id == _uid())).all()
    rows.sort(key=lambda s: (0 if s.is_active else 1,
                              -(s.updated_at.timestamp() if s.updated_at else 0)))
    return rows


def _get_owned_strategy(session, sid: str):
    s = session.get(Strategy, sid)
    if s is None or s.owner_id != _uid():
        return None
    return s


def _strategy_list_item(s: Strategy) -> StrategyListItem:
    return StrategyListItem(
        id=s.id, name=s.name, is_active=s.is_active,
        aggressiveness=s.aggressiveness,
        score_threshold=getattr(s, "score_threshold", 0) or 0,
        target_roles=s.target_roles, work_mode=s.work_mode,
        updated_at=s.updated_at)


def _ensure_one_active(session) -> None:
    rows = session.exec(select(Strategy).where(
        Strategy.owner_id == _uid(),
        Strategy.is_active == True)).all()  # noqa: E712
    if len(rows) <= 1:
        return
    rows.sort(key=lambda s: s.updated_at or _now(), reverse=True)
    for r in rows[1:]:
        r.is_active = False
        session.add(r)
    session.commit()


def _apply_body_to_strategy(s, body) -> None:
    s.name = body.name
    s.target_roles = body.target_roles
    s.region = body.region
    s.work_mode = body.work_mode
    s.salary_min = body.salary_min
    s.salary_max = body.salary_max
    s.aggressiveness = body.aggressiveness
    if body.weights:
        merged = dict(DEFAULT_WEIGHTS)
        merged.update({k: int(v) for k, v in body.weights.items()
                       if k in DEFAULT_WEIGHTS})
        s.weights = merged
    s.include_companies = body.include_companies
    s.exclude_companies = body.exclude_companies
    s.targeting_notes = body.targeting_notes
    st = getattr(body, "score_threshold", None)
    if st is not None:
        s.score_threshold = max(0, min(100, int(st)))
    s.updated_at = _now()


def _preview_counts(session, strat) -> StrategyPreviewCounts:
    jobs = session.exec(select(JobPosting).where(
        JobPosting.owner_id == _uid(),
        JobPosting.is_archived == False)).all()  # noqa: E712
    threshold = max(0, min(100, getattr(strat, "score_threshold", 0) or 0))
    rows = [(j, score_job(session, j, strat)) for j in jobs]
    excluded = sum(1 for _, sc in rows if sc["excluded"])
    scored = [(j, sc) for j, sc in rows if not sc["excluded"]]
    scores = [int(sc["score"]) for _, sc in scored]
    above = [s for s in scores if s >= threshold]
    top = max(scores, default=0)
    avg = round(sum(scores) / len(scores), 1) if scores else 0.0
    titles = [j.title for j, sc in sorted(scored, key=lambda t: int(t[1]["score"]), reverse=True)
              if int(sc["score"]) >= threshold][:5]
    return StrategyPreviewCounts(
        jobs_considered=len(jobs), above_threshold=len(above),
        strong=sum(1 for s in scores if s >= 75),
        moderate=sum(1 for s in scores if 50 <= s < 75),
        weak=sum(1 for s in scores if s < 50),
        excluded=excluded, avg_score=avg, top_score=top,
        score_threshold=threshold, threshold_passing_titles=titles)


def _preview_summary(name, c, a):
    if c.jobs_considered == 0:
        return "No jobs imported yet — preview counts will populate once you have jobs."
    base = ("%d job(s) | %d above threshold (%d/100) | %d strong, %d moderate, %d weak, %d excluded | avg %.1f"
            % (c.jobs_considered, c.above_threshold, c.score_threshold,
               c.strong, c.moderate, c.weak, c.excluded, c.avg_score))
    if not a or not a.jobs_considered:
        return base
    d_strong = c.strong - a.strong
    d_above = c.above_threshold - a.above_threshold
    bits = []
    if d_strong: bits.append("%+d strong" % d_strong)
    if d_above: bits.append("%+d above threshold" % d_above)
    if bits:
        return "%s — '%s': %s vs active." % (base, name, ", ".join(bits))
    return "%s — '%s' matches active volume." % (base, name)


@strategies_router.get("", response_model=List[StrategyListItem])
def list_strategies(session: Session = Depends(get_session)):
    if not session.exec(select(Strategy).where(Strategy.owner_id == _uid())).first():
        s = Strategy(owner_id=_uid())
        session.add(s)
        session.commit()
        session.refresh(s)
    return [_strategy_list_item(s) for s in _list_strategies(session)]


@strategies_router.post("", response_model=StrategyRead, status_code=201)
def create_strategy(body: StrategyCreate, session: Session = Depends(get_session)):
    has_any = bool(session.exec(select(Strategy).where(Strategy.owner_id == _uid())).first())
    activate = bool(body.activate) or (not has_any)
    s = Strategy(owner_id=_uid(), is_active=activate)
    _apply_body_to_strategy(s, body)
    session.add(s)
    session.commit()
    session.refresh(s)
    if activate:
        others = session.exec(select(Strategy).where(
            Strategy.owner_id == _uid(), Strategy.is_active == True,  # noqa: E712
            Strategy.id != s.id)).all()
        for o in others:
            o.is_active = False
            session.add(o)
        session.commit()
        session.refresh(s)
    _ensure_one_active(session)
    return _strategy_read(s)


@strategies_router.get("/{sid}", response_model=StrategyRead)
def get_strategy_by_id(sid: str, session: Session = Depends(get_session)):
    s = _get_owned_strategy(session, sid)
    if not s:
        raise HTTPException(404, "Strategy not found")
    return _strategy_read(s)


@strategies_router.put("/{sid}", response_model=StrategyRead)
def update_strategy_by_id(sid: str, body: StrategyUpsert, session: Session = Depends(get_session)):
    s = _get_owned_strategy(session, sid)
    if not s:
        raise HTTPException(404, "Strategy not found")
    _apply_body_to_strategy(s, body)
    session.add(s)
    session.commit()
    session.refresh(s)
    return _strategy_read(s)


@strategies_router.delete("/{sid}", status_code=204)
def delete_strategy_by_id(sid: str, session: Session = Depends(get_session)):
    s = _get_owned_strategy(session, sid)
    if not s:
        raise HTTPException(404, "Strategy not found")
    was_active = s.is_active
    session.delete(s)
    session.commit()
    if was_active:
        remaining = session.exec(select(Strategy).where(Strategy.owner_id == _uid())).all()
        if remaining:
            remaining.sort(key=lambda r: r.updated_at or _now(), reverse=True)
            remaining[0].is_active = True
            session.add(remaining[0])
            session.commit()
    _ensure_one_active(session)
    return None


@strategies_router.post("/{sid}/activate", response_model=StrategyRead)
def activate_strategy(sid: str, session: Session = Depends(get_session)):
    s = _get_owned_strategy(session, sid)
    if not s:
        raise HTTPException(404, "Strategy not found")
    others = session.exec(select(Strategy).where(
        Strategy.owner_id == _uid(), Strategy.id != s.id)).all()
    for o in others:
        if o.is_active:
            o.is_active = False
            session.add(o)
    s.is_active = True
    s.updated_at = _now()
    session.add(s)
    session.commit()
    session.refresh(s)
    return _strategy_read(s)


@strategies_router.get("/{sid}/preview", response_model=StrategyPreviewOut)
def preview_strategy(sid: str, session: Session = Depends(get_session)):
    s = _get_owned_strategy(session, sid)
    if not s:
        raise HTTPException(404, "Strategy not found")
    current = _preview_counts(session, s)
    active_counts = None
    if not s.is_active:
        act = _active_strategy(session)
        active_counts = _preview_counts(session, act)
    return StrategyPreviewOut(strategy_id=s.id, strategy_name=s.name,
                              current=current, active=active_counts,
                              summary=_preview_summary(s.name, current, active_counts))


class StrategyDraftPreviewIn(StrategyUpsert):
    score_threshold: int = 0


@strategies_router.post("/preview", response_model=StrategyPreviewOut)
def preview_draft_strategy(body: StrategyDraftPreviewIn, session: Session = Depends(get_session)):
    draft = Strategy(owner_id=_uid())
    _apply_body_to_strategy(draft, body)
    current = _preview_counts(session, draft)
    act = _active_strategy(session)
    active_counts = _preview_counts(session, act)
    return StrategyPreviewOut(strategy_id=None, strategy_name=draft.name or "Draft",
                              current=current, active=active_counts,
                              summary=_preview_summary(draft.name or "Draft", current, active_counts))


@strategies_router.post("/seed-presets", response_model=SeedPresetsOut, status_code=201)
def seed_presets(session: Session = Depends(get_session)):
    existing_names = {s.name for s in session.exec(select(Strategy).where(
        Strategy.owner_id == _uid())).all()}
    created = []
    skipped = []
    has_any = bool(existing_names)
    for preset in STRATEGY_PRESETS:
        if preset["name"] in existing_names:
            skipped.append(preset["name"])
            continue
        weights = dict(DEFAULT_WEIGHTS)
        weights.update({k: int(v) for k, v in (preset.get("weights") or {}).items()
                        if k in DEFAULT_WEIGHTS})
        s = Strategy(
            owner_id=_uid(), name=preset["name"],
            target_roles=list(preset.get("target_roles") or []),
            aggressiveness=Aggressiveness(preset.get("aggressiveness") or "balanced"),
            weights=weights,
            include_companies=list(preset.get("include_companies") or []),
            exclude_companies=list(preset.get("exclude_companies") or []),
            targeting_notes=preset.get("targeting_notes") or "",
            is_active=(not has_any and not created),
            score_threshold=int(preset.get("score_threshold") or 0))
        session.add(s)
        created.append(s)
    if created:
        session.commit()
        for s in created:
            session.refresh(s)
    _ensure_one_active(session)
    return SeedPresetsOut(created=[_strategy_list_item(s) for s in created],
                          skipped_existing=skipped)
