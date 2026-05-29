"""Aptiro Phase 11 - real job-board providers (offline tests).

These exercise app.modules.jobs.real_providers without any network: httpx
is stubbed per-test. Assertions stick to deterministic, normalization-level
facts (provider routing, field mapping, HTML stripping, graceful fallback)
so they don't couple to the requirement-extraction heuristics.

Run as part of the normal suite:  cd backend && pytest -q
"""
import os
import types
import contextlib

import app.modules.jobs.real_providers as rp


# --------------------------------------------------------------------------
# fake httpx transport
# --------------------------------------------------------------------------
class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def json(self):
        return self._payload


class _Client:
    routes = {}
    raise_on = set()

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        for frag in _Client.raise_on:
            if frag in url:
                raise RuntimeError("boom")
        for frag, payload in _Client.routes.items():
            if frag in url:
                return _Resp(payload)
        return _Resp({}, status=404)


@contextlib.contextmanager
def fake_httpx(routes, raise_on=None):
    _Client.routes = routes
    _Client.raise_on = set(raise_on or [])
    old = rp._httpx
    rp._httpx = types.SimpleNamespace(Client=_Client)
    try:
        yield
    finally:
        rp._httpx = old


def _clear_env():
    for k in ("APTIRO_GREENHOUSE_BOARDS", "APTIRO_LEVER_BOARDS",
              "APTIRO_ASHBY_BOARDS", "APTIRO_ADZUNA_APP_ID",
              "APTIRO_ADZUNA_APP_KEY", "APTIRO_ADZUNA_COUNTRY"):
        os.environ.pop(k, None)


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def test_phase11_unconfigured_returns_empty_for_all():
    _clear_env()
    with fake_httpx({}):
        for p in ("greenhouse", "lever", "ashby", "adzuna"):
            assert rp.fetch_real(p, None, 10) == [], p


def test_phase11_unknown_and_remotive_return_empty():
    # remotive is intentionally not handled here (keeps its existing path)
    assert rp.fetch_real("remotive", None, 10) == []
    assert rp.fetch_real("bogus", None, 10) == []


def test_phase11_greenhouse_normalizes():
    _clear_env()
    os.environ["APTIRO_GREENHOUSE_BOARDS"] = "acme-corp"
    payload = {"jobs": [
        {"id": 11, "title": "Senior Product Manager, AI",
         "location": {"name": "Remote - US"},
         "content": "<p>Own the AI roadmap.</p><ul><li>6+ years</li></ul>",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
         "updated_at": "2026-05-20T10:00:00Z"}]}
    with fake_httpx({"boards-api.greenhouse.io": payload}):
        jobs = rp.fetch_real("greenhouse", None, 10)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Senior Product Manager, AI"
    assert j.company == "Acme Corp"
    assert j.provider_source == "greenhouse"
    assert j.source == "greenhouse"
    assert j.source_url.endswith("/jobs/11")
    assert j.work_mode.value == "remote"
    assert "<" not in j.description_text
    assert j.posted_at == "2026-05-20"
    assert j.provider_job_id == "11"
    assert isinstance(j.requirements, list)


def test_phase11_lever_merges_lists_and_epoch_date():
    _clear_env()
    os.environ["APTIRO_LEVER_BOARDS"] = "northwind"
    payload = [
        {"id": "abc", "text": "Staff PM, Platform",
         "categories": {"location": "Hybrid - Seattle"},
         "descriptionPlain": "Lead platform strategy.",
         "lists": [{"text": "Requirements",
                    "content": "<li>8+ years</li><li>API products</li>"}],
         "hostedUrl": "https://jobs.lever.co/northwind/abc",
         "createdAt": 1747740000000}]
    with fake_httpx({"api.lever.co": payload}):
        jobs = rp.fetch_real("lever", None, 10)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Staff PM, Platform"
    assert j.work_mode.value == "hybrid"
    assert "API products" in j.description_text
    assert j.posted_at and j.posted_at[:4].isdigit()
    assert j.provider_source == "lever"


def test_phase11_ashby_remote_and_salary():
    _clear_env()
    os.environ["APTIRO_ASHBY_BOARDS"] = "lumen"
    payload = {"jobs": [
        {"id": "x1", "title": "AI Product Manager",
         "location": "Remote", "isRemote": True,
         "descriptionPlain": "Own clinical AI workflows.",
         "jobUrl": "https://jobs.ashbyhq.com/lumen/x1",
         "publishedDate": "2026-05-15T00:00:00Z",
         "compensation": {"compensationTierSummary": "$170,000 - $220,000"}}]}
    with fake_httpx({"api.ashbyhq.com": payload}):
        jobs = rp.fetch_real("ashby", None, 10)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.work_mode.value == "remote"
    assert j.salary_min == 170000 and j.salary_max == 220000
    assert j.provider_source == "ashby"


def test_phase11_adzuna_search():
    _clear_env()
    os.environ["APTIRO_ADZUNA_APP_ID"] = "id"
    os.environ["APTIRO_ADZUNA_APP_KEY"] = "key"
    payload = {"results": [
        {"id": "9", "title": "Product Manager, Data",
         "description": "Own data products.",
         "company": {"display_name": "Gridpoint"},
         "location": {"display_name": "Austin, TX"},
         "salary_min": 150000.0, "salary_max": 190000.0,
         "redirect_url": "https://adzuna/9",
         "created": "2026-05-10T00:00:00Z"}]}
    with fake_httpx({"api.adzuna.com": payload}):
        jobs = rp.fetch_real("adzuna", "data", 10)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.company == "Gridpoint"
    assert j.salary_min == 150000 and j.salary_max == 190000
    assert j.provider_source == "adzuna"


def test_phase11_error_falls_back_to_empty():
    _clear_env()
    os.environ["APTIRO_GREENHOUSE_BOARDS"] = "acme"
    with fake_httpx({}, raise_on=["greenhouse"]):
        assert rp.fetch_real("greenhouse", None, 10) == []


def test_phase11_query_filters_per_company_results():
    _clear_env()
    os.environ["APTIRO_GREENHOUSE_BOARDS"] = "acme"
    payload = {"jobs": [
        {"id": 1, "title": "Product Manager", "location": {"name": "Remote"},
         "content": "PM role", "absolute_url": "u1",
         "updated_at": "2026-05-01"},
        {"id": 2, "title": "Data Engineer", "location": {"name": "Remote"},
         "content": "infra role", "absolute_url": "u2",
         "updated_at": "2026-05-01"}]}
    with fake_httpx({"greenhouse": payload}):
        jobs = rp.fetch_real("greenhouse", "product", 10)
    assert len(jobs) == 1 and jobs[0].title == "Product Manager"


def test_phase11_limit_is_respected():
    _clear_env()
    os.environ["APTIRO_GREENHOUSE_BOARDS"] = "acme"
    payload = {"jobs": [
        {"id": i, "title": "Role %d" % i, "location": {"name": "Remote"},
         "content": "x", "absolute_url": "u%d" % i,
         "updated_at": "2026-05-01"} for i in range(10)]}
    with fake_httpx({"greenhouse": payload}):
        jobs = rp.fetch_real("greenhouse", None, 3)
    assert len(jobs) == 3


def test_phase11_is_configured_and_configured_providers():
    _clear_env()
    assert rp.configured_providers() == []
    os.environ["APTIRO_LEVER_BOARDS"] = "x"
    os.environ["APTIRO_ASHBY_BOARDS"] = "y"
    assert rp.is_configured("lever") is True
    assert rp.is_configured("greenhouse") is False
    assert set(rp.configured_providers()) == {"lever", "ashby"}
