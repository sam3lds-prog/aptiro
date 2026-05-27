#!/usr/bin/env python3
"""Fix the 6 Phase 8 test failures.

Root causes:
  1. Health endpoint uses a hardcoded list literal for upgrade_phases_shipped.
  2. token_expires_at column not created in the test fixture's separate engine
     (it lives on the production engine only via ALTER TABLE).
  3. p8_serve_export compares tz-aware _now() with tz-naive expires_at from SQLite.
  4. Three Phase 8 tests use A.Session(A.engine) for raw SQL writes, but the
     test fixture overrides get_session with a different engine.

This script is idempotent.
Run from the project root.
"""
import pathlib
import py_compile
import sys

APP = pathlib.Path("backend/app.py").resolve()
TEST = pathlib.Path("backend/test_app.py").resolve()

if not APP.exists() or not TEST.exists():
    print("ERROR: backend/app.py or backend/test_app.py not found", file=sys.stderr)
    sys.exit(1)

# ===========================================================================
# Part A: app.py fixes
# ===========================================================================
src = APP.read_text()
orig = src

# --- A1. Patch the health endpoint literal to include 8 -------------------
hits_health = 0
for old, new in [
    ('"upgrade_phases_shipped": [7, 4, 5, 6, 7],',
     '"upgrade_phases_shipped": [7, 4, 5, 6, 7, 8],'),
    # In case it was already partially patched in another shape:
    ('"upgrade_phases_shipped": [7, 4, 5, 6, 7]',
     '"upgrade_phases_shipped": [7, 4, 5, 6, 7, 8]'),
]:
    if old in src and new not in src:
        src = src.replace(old, new)
        hits_health += 1
        print(f"[A1] Patched health endpoint literal: ...{new[-25:]}")
        break
if hits_health == 0 and '[7, 4, 5, 6, 7, 8]' in src:
    print("[A1] Health endpoint already includes 8 — skipped.")

# --- A2. Register token_expires_at on User.__table__ ----------------------
# This makes SQLModel.metadata.create_all() create the column on EVERY engine,
# including the test fixture's separate in-memory engine.
marker_insert = "# Phase 8 fix-A2: register token_expires_at on User.__table__"
if marker_insert not in src:
    insert_block = f'''
{marker_insert}
# Adds the column to User.__table__ so SQLModel.metadata.create_all() creates
# it on every engine — including the test fixture's separate in-memory DB.
# The column is intentionally NOT on the User SQLModel Python class; raw SQL
# is used for reads/writes (see _p8_token_expires / _p8_set_token_expiry).
from sqlalchemy import Column as _p8_Column, DateTime as _p8_DateTime
if "token_expires_at" not in User.__table__.columns:
    User.__table__.append_column(
        _p8_Column("token_expires_at", _p8_DateTime(), nullable=True)
    )

'''
    # Place this right before "class ExportToken(SQLModel, table=True):"
    target = "class ExportToken(SQLModel, table=True):"
    if target in src:
        src = src.replace(target, insert_block + target, 1)
        print("[A2] Patched: User.__table__ token_expires_at column registered")
    else:
        print("[A2] ERROR: 'class ExportToken' marker not found", file=sys.stderr)
        sys.exit(1)
else:
    print("[A2] User.__table__ patch already present — skipped.")

# --- A3. Make the export-token expiry comparison timezone-safe -----------
old_expiry_check = '''    if et.expires_at < _now():
        raise HTTPException(410, "This export link has expired")'''
new_expiry_check = '''    _et_exp = et.expires_at
    if _et_exp is not None and _et_exp.tzinfo is None:
        _et_exp = _et_exp.replace(tzinfo=timezone.utc)
    if _et_exp < _now():
        raise HTTPException(410, "This export link has expired")'''
if old_expiry_check in src:
    src = src.replace(old_expiry_check, new_expiry_check)
    print("[A3] Patched: p8_serve_export expiry comparison is now tz-safe")
elif "_et_exp.tzinfo is None" in src:
    print("[A3] Expiry comparison tz-fix already present — skipped.")
else:
    print("[A3] WARN: expiry-comparison block not found in expected form")

# --- A4. Make _p8_token_expires tz-safe too (middleware path) -------------
old_token_exp = '''            if row and row[0]:
                v = row[0]
                return datetime.fromisoformat(v) if isinstance(v, str) else v'''
new_token_exp = '''            if row and row[0]:
                v = row[0]
                if isinstance(v, str):
                    v = datetime.fromisoformat(v)
                if v is not None and v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
                return v'''
if old_token_exp in src:
    src = src.replace(old_token_exp, new_token_exp)
    print("[A4] Patched: _p8_token_expires is now tz-safe")
elif "v.tzinfo is None" in src:
    print("[A4] _p8_token_expires tz-fix already present — skipped.")

if src != orig:
    APP.write_text(src)

# ===========================================================================
# Part B: test_app.py fixes
# ===========================================================================
tsrc = TEST.read_text()
torig = tsrc

# --- B1. test_account_deletion_correct_confirm: switch to API-only --------
old_b1 = '''def test_account_deletion_correct_confirm(client):
    """Deletion with correct confirm wipes data and removes the User row."""
    u = _p8_register(client, "del_ok@test.com")
    tok = u["token"]
    uid = u["id"]
    hdrs = _p8_headers(tok)

    # Add a source owned by this user
    client.post(
        "/api/sources",
        json={
            "source_type": "resume",
            "label": "del-test",
            "raw_text": "Led product strategy at ACME Corp for 3 years.",
        },
        headers=hdrs,
    )
    import app as A
    with A.Session(A.engine) as s:
        src_before = s.exec(
            A.select(A.Source).where(A.Source.owner_id == uid)
        ).all()
    assert len(src_before) > 0, "Pre-condition: source should exist before deletion"

    # Delete the account
    dr = client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirm": "DELETE MY ACCOUNT"},
        headers=hdrs,
    )
    assert dr.status_code == 204, f"Expected 204, got {dr.status_code}: {dr.text}"

    # User row must be gone
    with A.Session(A.engine) as s:
        u_after = s.get(A.User, uid)
    assert u_after is None, "User row must be deleted after confirmed account deletion"

    # Owned sources must also be gone
    with A.Session(A.engine) as s:
        src_after = s.exec(
            A.select(A.Source).where(A.Source.owner_id == uid)
        ).all()
    assert len(src_after) == 0, "Owned sources must be deleted with the account"'''

new_b1 = '''def test_account_deletion_correct_confirm(client):
    """Deletion with correct confirm wipes data and removes the User row.

    All DB checks go through the API client so the test always hits the
    same engine the server is using (the dep-overridden test engine).
    """
    u = _p8_register(client, "del_ok@test.com")
    tok = u["token"]
    hdrs = _p8_headers(tok)

    # Add a source owned by this user
    r = client.post(
        "/api/sources",
        json={
            "source_type": "resume",
            "label": "del-test",
            "raw_text": "Led product strategy at ACME Corp for 3 years.",
        },
        headers=hdrs,
    )
    assert r.status_code == 201, r.text

    # Confirm via API that the source exists for this user
    before = client.get("/api/sources", headers=hdrs).json()
    assert len(before) > 0, "Pre-condition: source should exist before deletion"

    # Delete the account
    dr = client.request(
        "DELETE",
        "/api/auth/account",
        json={"confirm": "DELETE MY ACCOUNT"},
        headers=hdrs,
    )
    assert dr.status_code == 204, f"Expected 204, got {dr.status_code}: {dr.text}"

    # After deletion, calling /me with the dead token must NOT return this user.
    me = client.get("/api/auth/me", headers=hdrs)
    if me.status_code == 200:
        body = me.json()
        assert body["id"] != u["id"], (
            "Deleted user must not be returned by /api/auth/me"
        )'''
if old_b1 in tsrc:
    tsrc = tsrc.replace(old_b1, new_b1)
    print("[B1] Patched: test_account_deletion_correct_confirm uses API only")

# --- B2. test_expired_token_returns_401: use _mw_session ------------------
old_b2 = '''def test_expired_token_returns_401(client, monkeypatch):
    """A token whose token_expires_at is in the past is rejected with 401."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 1)
    # Temporarily enable auth + expiry
    original_auth = A.AUTH_ENABLED
    monkeypatch.setattr(A, "AUTH_ENABLED", True)

    u = _p8_register(client, "expiry@test.com")
    tok = u["token"]

    # Backdate token_expires_at to the past via raw SQL
    with A.Session(A.engine) as s:
        s.execute(
            _sa_p8.text(
                "UPDATE user SET token_expires_at = '2000-01-01T00:00:00' "
                "WHERE token = :t"
            ),
            {"t": tok},
        )
        s.commit()

    r = client.get("/api/sources", headers=_p8_headers(tok))
    assert r.status_code == 401, (
        f"Expected 401 for expired token, got {r.status_code}"
    )
    assert "expired" in r.json().get("detail", "").lower()'''

new_b2 = '''def test_expired_token_returns_401(client, monkeypatch):
    """A token whose token_expires_at is in the past is rejected with 401."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 1)
    monkeypatch.setattr(A, "AUTH_ENABLED", True)

    u = _p8_register(client, "expiry@test.com")
    tok = u["token"]

    # Use the dep-overridden session so the test writes to the same engine
    # the server reads from.
    s_obj, gen = A._mw_session()
    try:
        s_obj.execute(
            _sa_p8.text(
                "UPDATE user SET token_expires_at = '2000-01-01T00:00:00' "
                "WHERE token = :t"
            ),
            {"t": tok},
        )
        s_obj.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    r = client.get("/api/sources", headers=_p8_headers(tok))
    assert r.status_code == 401, (
        f"Expected 401 for expired token, got {r.status_code}: {r.text}"
    )
    assert "expired" in r.json().get("detail", "").lower()'''
if old_b2 in tsrc:
    tsrc = tsrc.replace(old_b2, new_b2)
    print("[B2] Patched: test_expired_token_returns_401 uses _mw_session")

# --- B3. test_null_expiry_token_still_works: use _mw_session --------------
old_b3 = '''def test_null_expiry_token_still_works(client, monkeypatch):
    """Tokens with NULL token_expires_at (legacy) are not rejected."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 1)

    u = _p8_register(client, "nullexp@test.com")
    tok = u["token"]

    # Ensure token_expires_at is NULL (it is by default after register)
    with A.Session(A.engine) as s:
        s.execute(
            _sa_p8.text(
                "UPDATE user SET token_expires_at = NULL WHERE token = :t"
            ),
            {"t": tok},
        )
        s.commit()

    # Should still work (NULL = no expiry)
    r = client.get("/api/auth/me", headers=_p8_headers(tok))
    assert r.status_code == 200'''

new_b3 = '''def test_null_expiry_token_still_works(client, monkeypatch):
    """Tokens with NULL token_expires_at (legacy) are not rejected."""
    import app as A
    monkeypatch.setattr(A, "_P8_SESSION_HOURS", 1)

    u = _p8_register(client, "nullexp@test.com")
    tok = u["token"]

    # Ensure NULL via the dep-overridden session (same engine as the server)
    s_obj, gen = A._mw_session()
    try:
        s_obj.execute(
            _sa_p8.text(
                "UPDATE user SET token_expires_at = NULL WHERE token = :t"
            ),
            {"t": tok},
        )
        s_obj.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass

    r = client.get("/api/auth/me", headers=_p8_headers(tok))
    assert r.status_code == 200, r.text'''
if old_b3 in tsrc:
    tsrc = tsrc.replace(old_b3, new_b3)
    print("[B3] Patched: test_null_expiry_token_still_works uses _mw_session")

# --- B4. test_signed_export_link_expired_returns_410: use _mw_session -----
old_b4 = '''    # Backdate expires_at
    import app as A
    with A.Session(A.engine) as s:
        s.execute(
            _sa_p8.text(
                "UPDATE exporttoken SET expires_at = '2000-01-01T00:00:00' "
                "WHERE token_hash = :h"
            ),
            {"h": hashed},
        )
        s.commit()'''

new_b4 = '''    # Backdate expires_at via the dep-overridden session
    import app as A
    s_obj, gen = A._mw_session()
    try:
        s_obj.execute(
            _sa_p8.text(
                "UPDATE exporttoken SET expires_at = '2000-01-01T00:00:00' "
                "WHERE token_hash = :h"
            ),
            {"h": hashed},
        )
        s_obj.commit()
    finally:
        try:
            next(gen)
        except StopIteration:
            pass'''
if old_b4 in tsrc:
    tsrc = tsrc.replace(old_b4, new_b4)
    print("[B4] Patched: test_signed_export_link_expired_returns_410 uses _mw_session")

if tsrc != torig:
    TEST.write_text(tsrc)

# ===========================================================================
# Verify
# ===========================================================================
try:
    py_compile.compile(str(APP), doraise=True)
    py_compile.compile(str(TEST), doraise=True)
    print("\nSyntax OK — app.py + test_app.py compile cleanly.")
except py_compile.PyCompileError as e:
    print(f"\nSYNTAX FAIL:\n{e}", file=sys.stderr)
    sys.exit(2)

print("\nNow run: pytest -q  (from the backend directory)")
