"""Aptiro - Phase 11: real job-board provider implementations.

Live, ToS-friendly fetch for Greenhouse, Lever, Ashby (per-company board
APIs) and Adzuna (keyed search API). Remotive already has a live path in
the jobs module; this extends the same env-gated, mock-default,
graceful-fallback pattern to the rest.

NON-NEGOTIABLES honored:
  - Public board APIs only. No scraping, no LinkedIn, no auth-walled
    sources. These are the vendors' own public posting APIs.
  - Mock stays the DEFAULT. Every function returns [] on ANY error or when
    unconfigured, so the caller always falls back to deterministic mock
    samples and the offline test suite is unaffected.
  - The deterministic weighted score stays the single source of truth;
    this module only produces normalized JobPosting rows.

Per-company vs search:
  - Greenhouse / Lever / Ashby expose ONE company's postings per call,
    keyed by that company's board token/slug. Configure tokens via env
    (comma-separated). `query` is applied as a text filter over the
    returned postings since those APIs take no search term.
  - Adzuna is a true search API (keyed) and takes the query directly.

Env configuration (all optional; absent -> provider yields nothing and the
caller falls back to mock):
  APTIRO_GREENHOUSE_BOARDS   e.g. "stripe,airbnb,figma"
  APTIRO_LEVER_BOARDS        e.g. "netflix,plaid"
  APTIRO_ASHBY_BOARDS        e.g. "ramp,linear"
  APTIRO_ADZUNA_APP_ID
  APTIRO_ADZUNA_APP_KEY
  APTIRO_ADZUNA_COUNTRY      default "us"
  APTIRO_JOB_FETCH_TIMEOUT   seconds, default 15

All imports of the app's own models/helpers are LAZY (inside functions) so
this module can be imported during legacy.py load without any circular
import. At module load it touches only the stdlib and httpx.
"""
import os
import re
import html as _html
import datetime as _dt
from typing import List, Optional

try:
    import httpx as _httpx
except Exception:                     # pragma: no cover
    _httpx = None


# ---------------------------------------------------------------------------
# small stdlib helpers
# ---------------------------------------------------------------------------
def _txt(html_str: Optional[str]) -> str:
    """Reduce HTML to plain text using only the stdlib."""
    if not html_str:
        return ""
    s = _html.unescape(html_str)
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def _timeout() -> float:
    try:
        return float(os.getenv("APTIRO_JOB_FETCH_TIMEOUT", "15") or "15")
    except ValueError:
        return 15.0


def _boards(env_key: str) -> List[str]:
    raw = os.getenv(env_key, "") or ""
    return [b.strip() for b in raw.split(",") if b.strip()]


def _filter(jobs: list, query: Optional[str]) -> list:
    if not query:
        return jobs
    ql = query.lower()
    return [j for j in jobs
            if ql in (j.title or "").lower()
            or ql in (j.description_text or "").lower()]


def _ms_to_date(ms) -> Optional[str]:
    try:
        return _dt.datetime.fromtimestamp(
            int(ms) / 1000, _dt.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _intor(v) -> Optional[int]:
    try:
        return int(float(v)) if v is not None else None
    except Exception:
        return None


def _infer_mode(loc: Optional[str], desc: Optional[str]):
    from app.legacy import WorkMode
    blob = ((loc or "") + " " + (desc or "")).lower()
    if "hybrid" in blob:
        return WorkMode.hybrid
    if "remote" in blob:
        return WorkMode.remote
    if any(w in blob for w in ("on-site", "onsite", "in office",
                               "in-office")):
        return WorkMode.onsite
    return WorkMode.any


def _ashby_salary(comp: dict):
    try:
        summary = (comp or {}).get("compensationTierSummary") or ""
        nums = re.findall(r"\$?\s*([0-9][0-9,]{3,})", summary)
        vals = [int(n.replace(",", "")) for n in nums[:2]]
        if len(vals) == 2:
            return vals[0], vals[1]
        if len(vals) == 1:
            return vals[0], None
    except Exception:
        pass
    return None, None


def _company_label(token: str) -> str:
    return (token or "").replace("-", " ").replace("_", " ").title()[:120]


# ---------------------------------------------------------------------------
# providers - each returns list[JobPosting]; [] on any failure
# ---------------------------------------------------------------------------
def _fetch_greenhouse(query: Optional[str], limit: int) -> list:
    if _httpx is None:
        return []
    boards = _boards("APTIRO_GREENHOUSE_BOARDS")
    if not boards:
        return []
    from app.legacy import (JobPosting, _now,
                            _extract_requirements, _structured_requirements)
    out: list = []
    with _httpx.Client(timeout=_timeout(),
                       headers={"User-Agent": "Aptiro/1.0"}) as client:
        for token in boards:
            try:
                r = client.get(
                    "https://boards-api.greenhouse.io/v1/boards/%s/jobs"
                    % token, params={"content": "true"})
                r.raise_for_status()
                data = r.json()
            except Exception:
                continue
            for item in data.get("jobs", []):
                desc = _txt(item.get("content", ""))
                raw_loc = item.get("location")
                loc = (raw_loc.get("name") if isinstance(raw_loc, dict)
                       else raw_loc) or ""
                out.append(JobPosting(
                    title=(item.get("title") or "")[:160],
                    company=_company_label(token),
                    location=loc or None,
                    work_mode=_infer_mode(loc, desc),
                    salary_min=None, salary_max=None,
                    source="greenhouse",
                    source_url=item.get("absolute_url"),
                    description_text=desc,
                    requirements=_extract_requirements(desc),
                    structured_requirements=_structured_requirements(desc),
                    posted_at=(item.get("updated_at") or "")[:10] or None,
                    provider_source="greenhouse",
                    provider_job_id=str(item.get("id", "")),
                    last_seen_at=_now(), is_stale=False))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
    return _filter(out, query)[:limit]


def _fetch_lever(query: Optional[str], limit: int) -> list:
    if _httpx is None:
        return []
    boards = _boards("APTIRO_LEVER_BOARDS")
    if not boards:
        return []
    from app.legacy import (JobPosting, _now,
                            _extract_requirements, _structured_requirements)
    out: list = []
    with _httpx.Client(timeout=_timeout(),
                       headers={"User-Agent": "Aptiro/1.0"}) as client:
        for token in boards:
            try:
                r = client.get("https://api.lever.co/v0/postings/%s" % token,
                               params={"mode": "json"})
                r.raise_for_status()
                data = r.json()
            except Exception:
                continue
            items = data if isinstance(data, list) else []
            for item in items:
                cats = item.get("categories") or {}
                loc = cats.get("location") or ""
                desc = (item.get("descriptionPlain")
                        or _txt(item.get("description", "")))
                extra = " ".join(
                    _txt(l.get("content", ""))
                    for l in (item.get("lists") or []))
                full = (desc + "\n" + extra).strip()
                out.append(JobPosting(
                    title=(item.get("text") or "")[:160],
                    company=_company_label(token),
                    location=loc or None,
                    work_mode=_infer_mode(loc, desc),
                    salary_min=None, salary_max=None,
                    source="lever",
                    source_url=item.get("hostedUrl") or item.get("applyUrl"),
                    description_text=full,
                    requirements=_extract_requirements(full),
                    structured_requirements=_structured_requirements(full),
                    posted_at=_ms_to_date(item.get("createdAt")),
                    provider_source="lever",
                    provider_job_id=str(item.get("id", "")),
                    last_seen_at=_now(), is_stale=False))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
    return _filter(out, query)[:limit]


def _fetch_ashby(query: Optional[str], limit: int) -> list:
    if _httpx is None:
        return []
    boards = _boards("APTIRO_ASHBY_BOARDS")
    if not boards:
        return []
    from app.legacy import (JobPosting, WorkMode, _now,
                            _extract_requirements, _structured_requirements)
    out: list = []
    with _httpx.Client(timeout=_timeout(),
                       headers={"User-Agent": "Aptiro/1.0"}) as client:
        for token in boards:
            try:
                r = client.get(
                    "https://api.ashbyhq.com/posting-api/job-board/%s"
                    % token, params={"includeCompensation": "true"})
                r.raise_for_status()
                data = r.json()
            except Exception:
                continue
            for item in data.get("jobs", []):
                desc = (item.get("descriptionPlain")
                        or _txt(item.get("descriptionHtml", "")))
                loc = item.get("location") or ""
                mode = (WorkMode.remote if item.get("isRemote")
                        else _infer_mode(loc, desc))
                smin, smax = _ashby_salary(item.get("compensation") or {})
                out.append(JobPosting(
                    title=(item.get("title") or "")[:160],
                    company=_company_label(token),
                    location=loc or None,
                    work_mode=mode,
                    salary_min=smin, salary_max=smax,
                    source="ashby",
                    source_url=item.get("jobUrl") or item.get("applyUrl"),
                    description_text=desc,
                    requirements=_extract_requirements(desc),
                    structured_requirements=_structured_requirements(desc),
                    posted_at=(item.get("publishedDate") or "")[:10] or None,
                    provider_source="ashby",
                    provider_job_id=str(item.get("id", "")),
                    last_seen_at=_now(), is_stale=False))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
    return _filter(out, query)[:limit]


def _fetch_adzuna(query: Optional[str], limit: int) -> list:
    if _httpx is None:
        return []
    app_id = os.getenv("APTIRO_ADZUNA_APP_ID", "")
    app_key = os.getenv("APTIRO_ADZUNA_APP_KEY", "")
    if not (app_id and app_key):
        return []
    from app.legacy import (JobPosting, _now,
                            _extract_requirements, _structured_requirements)
    country = (os.getenv("APTIRO_ADZUNA_COUNTRY", "us") or "us").lower()
    out: list = []
    try:
        with _httpx.Client(timeout=_timeout(),
                           headers={"User-Agent": "Aptiro/1.0"}) as client:
            r = client.get(
                "https://api.adzuna.com/v1/api/jobs/%s/search/1" % country,
                params={"app_id": app_id, "app_key": app_key,
                        "results_per_page": min(limit, 50),
                        "what": query or "product manager",
                        "content-type": "application/json"})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []
    for item in data.get("results", []):
        desc = item.get("description", "")
        loc = ((item.get("location") or {}).get("display_name") or "")
        out.append(JobPosting(
            title=(item.get("title") or "")[:160],
            company=((item.get("company") or {}).get("display_name")
                     or "")[:120],
            location=loc or None,
            work_mode=_infer_mode(loc, desc),
            salary_min=_intor(item.get("salary_min")),
            salary_max=_intor(item.get("salary_max")),
            source="adzuna",
            source_url=item.get("redirect_url"),
            description_text=desc,
            requirements=_extract_requirements(desc),
            structured_requirements=_structured_requirements(desc),
            posted_at=(item.get("created") or "")[:10] or None,
            provider_source="adzuna",
            provider_job_id=str(item.get("id", "")),
            last_seen_at=_now(), is_stale=False))
        if len(out) >= limit:
            break
    return out


_REAL = {
    "greenhouse": _fetch_greenhouse,
    "lever": _fetch_lever,
    "ashby": _fetch_ashby,
    "adzuna": _fetch_adzuna,
}


def is_configured(provider: Optional[str]) -> bool:
    """True when the env credentials/tokens for a real provider are set."""
    p = (provider or "").lower()
    if p == "greenhouse":
        return bool(_boards("APTIRO_GREENHOUSE_BOARDS"))
    if p == "lever":
        return bool(_boards("APTIRO_LEVER_BOARDS"))
    if p == "ashby":
        return bool(_boards("APTIRO_ASHBY_BOARDS"))
    if p == "adzuna":
        return bool(os.getenv("APTIRO_ADZUNA_APP_ID")
                    and os.getenv("APTIRO_ADZUNA_APP_KEY"))
    return False


def configured_providers() -> List[str]:
    return [p for p in _REAL if is_configured(p)]


def fetch_real(provider: Optional[str], query: Optional[str],
               limit: int = 20) -> list:
    """Return list[JobPosting] from the live provider, or [] to signal the
    caller should fall back to mock. NEVER raises. Remotive is intentionally
    NOT handled here - it keeps its existing tested path in the jobs module.
    """
    fn = _REAL.get((provider or "").lower())
    if fn is None or _httpx is None:
        return []
    try:
        return fn(query, max(1, int(limit or 20)))
    except Exception:
        return []
