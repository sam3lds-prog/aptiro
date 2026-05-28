#!/usr/bin/env python3
"""Aptiro — Phase 9 hotfix: restore `claim_read` in `backend/app/legacy.py`.

Context
-------
The previous PR-7 commit (ab9461e — "Phase 9 PR-7: extract
modules/sources/__init__.py") inadvertently removed the module-level
`claim_read(session, c)` helper while moving `_source_read` into
`modules/sources`. Three routes in legacy.py still call it:

    * GET    /api/claims         -> list_claims
    * GET    /api/claims/{id}    -> get_claim
    * PATCH  /api/claims/{id}    -> update_claim

The result is `NameError: name 'claim_read' is not defined` on every
/api/claims call, which cascades into 71 failing tests (KeyError: 0
and "unhashable type: 'slice'" because tests do `claims[0]["id"]` /
`claims[:3]` against the error-dict body the routes return after the
NameError).

What this script does
---------------------
1. Reads `backend/app/legacy.py`.
2. Returns early if `claim_read` is already defined OR imported from
   `app.modules.sources` (idempotent — safe to re-run).
3. Otherwise, inserts the original `claim_read` definition immediately
   before `@claims_router.get("", response_model=List[ClaimRead])`,
   which is the first caller in the file.
4. Verifies the file still byte-compiles.

It does NOT:
  - Touch any other file.
  - Re-run PR-6 / PR-7 work that's already in HEAD.
  - Change any behavior of /api/claims beyond restoring the original
    implementation (verbatim from pre-PR-7).

Run from project root:
    python3 phase9_restore_claim_read.py
"""
from __future__ import annotations

import pathlib
import py_compile
import re
import sys


LEGACY = pathlib.Path("backend/app/legacy.py")

CLAIM_READ_DEF = '''def claim_read(session, c):
    """Build ClaimRead DTO for a ProfileClaim, with provenance + refs.

    Restored after Phase 9 PR-7 accidentally removed it while
    extracting modules/sources. Verbatim behavior to pre-PR-7.
    """
    cat = claim_provenance(session, c)
    refs = session.exec(
        select(SourceRef).where(SourceRef.claim_id == c.id)).all()
    return ClaimRead(
        id=c.id, source_id=c.source_id, claim_text=c.claim_text,
        claim_type=c.claim_type, company=c.company, role=c.role,
        date_range=c.date_range, skills=c.skills, metrics=c.metrics,
        confidence=c.confidence, approval_status=c.approval_status,
        user_note=c.user_note, provenance_category=cat,
        provenance_color=provenance_color(cat),
        source_refs=[SourceRefRead(
            id=r.id, source_id=r.source_id, source_type=r.source_type,
            section=r.section, snippet=r.snippet, page=r.page,
            confidence=r.confidence) for r in refs])


'''


def main() -> int:
    if not LEGACY.exists():
        print(f"ERROR: {LEGACY} not found. Run from project root "
              f"(the directory containing backend/).")
        return 1

    content = LEGACY.read_text()

    # ---- Idempotency check #1: function already defined? ----
    if re.search(r'^def\s+claim_read\s*\(', content, re.MULTILINE):
        print("✓ claim_read is already defined in legacy.py — nothing to do.")
        return 0

    # ---- Idempotency check #2: imported from modules.sources? ----
    # Handles either single-line or multi-line import forms.
    import_pat = re.compile(
        r'from\s+app\.modules\.sources\s+import\s*\(?[^)]*\bclaim_read\b',
        re.DOTALL)
    if import_pat.search(content):
        print("✓ claim_read is imported from app.modules.sources "
              "— nothing to do.")
        return 0

    # ---- Find the insertion point: first @claims_router decorator. ----
    anchor = re.compile(
        r'^@claims_router\.get\("",\s*response_model=List\[ClaimRead\]\)',
        re.MULTILINE)
    m = anchor.search(content)
    if not m:
        # Fall back to the broader pattern (handles formatting drift).
        anchor_fallback = re.compile(r'^@claims_router\.get\("",',
                                     re.MULTILINE)
        m = anchor_fallback.search(content)
    if not m:
        print("ERROR: Could not find `@claims_router.get(\"\", ...)` "
              "in legacy.py. Aborting — please share the file so I can "
              "produce a tailored fix.")
        return 2

    insert_at = m.start()

    # Sanity guard: the surrounding context should mention list_claims
    # within ~200 chars so we know we're at the right spot.
    nearby = content[insert_at: insert_at + 400]
    if "list_claims" not in nearby:
        print("WARNING: Anchor located but `def list_claims` not found "
              "nearby. Inserting anyway, but please verify manually.")

    new_content = content[:insert_at] + CLAIM_READ_DEF + content[insert_at:]

    # Write atomically: write to tmp then rename.
    tmp = LEGACY.with_suffix(".py.tmp")
    tmp.write_text(new_content)

    # Byte-compile sanity check before swapping in.
    try:
        py_compile.compile(str(tmp), doraise=True)
    except py_compile.PyCompileError as exc:
        tmp.unlink(missing_ok=True)
        print("ERROR: New legacy.py failed to compile; "
              "no changes written.\n", exc)
        return 3

    tmp.replace(LEGACY)
    print(f"✓ Inserted `def claim_read` at offset {insert_at} in {LEGACY}")
    print("✓ legacy.py still compiles cleanly.")
    print("\nNext: cd backend && . .venv/bin/activate && pytest -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
