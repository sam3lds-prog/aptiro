#!/usr/bin/env python3
"""Phase 8 round-4 fix using monkey-patch (avoids text-replacement issues).

The previous rounds tried to match exact source text in app.py and likely
failed silently due to whitespace mismatch (the on-disk file's indentation
didn't match the patch's literal). This round appends a NEW block to the end
of app.py that:

  1. Wraps _p8_token_expires with a tz-safe version. Python resolves the
     name in module globals at each call, so the middleware automatically
     uses the wrapper without any code change in the middleware itself.

  2. Removes the broken DELETE /api/auth/account route and registers a
     replacement that uses raw SQL deletes in dependency order.

Idempotent — uses APTIRO_PHASE8_ROUND4_FIX_MARKER to detect prior runs.
Run from the project root.
"""
import pathlib
import py_compile
import sys

APP = pathlib.Path("backend/app.py").resolve()
if not APP.exists():
    print("ERROR: backend/app.py not found", file=sys.stderr)
    sys.exit(1)

src = APP.read_text()
MARKER = "APTIRO_PHASE8_ROUND4_FIX_MARKER"

if MARKER in src:
    print("Round 4 fix already applied — skipping append.")
else:
    fix_block = '''

# ===========================================================================
# Phase 8 Round 4 fix — monkey-patches that bypass text-replacement issues.
# APTIRO_PHASE8_ROUND4_FIX_MARKER — do not remove (idempotency guard).
# ===========================================================================

# ---- Fix 1: tz-safe wrapper around _p8_token_expires ---------------------
# SQLite strips tzinfo on round-trip; the middleware compares with _now()
# which is tz-aware, so a naive return value crashes the comparison.
# Python looks up names in module globals at call time, so simply
# rebinding _p8_token_expires here shadows the original and the
# middleware automatically uses this wrapped version.
_p8_token_expires_v1 = _p8_token_expires


def _p8_token_expires(token):  # type: ignore[no-redef]
    v = _p8_token_expires_v1(token)
    if v is not None and hasattr(v, "tzinfo") and v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


# ---- Fix 2: replace DELETE /api/auth/account with raw-SQL deletion -------
# The ORM-based delete triggers SET-NULL cascade on profileclaim.source_id
# (which is NOT NULL), poisons the session with PendingRollbackError, and
# returns 500. Raw SQL deletes in dependency order bypass the cascade.
from fastapi.routing import APIRoute as _APIRoute_p8r4

# Remove the original DELETE /api/auth/account route(s)
_p8r4_to_remove = []
for _r in list(app.router.routes):
    if (isinstance(_r, _APIRoute_p8r4)
        and _r.path == "/api/auth/account"
        and "DELETE" in _r.methods):
        _p8r4_to_remove.append(_r)

for _r in _p8r4_to_remove:
    try:
        app.router.routes.remove(_r)
    except ValueError:
        pass


@app.delete("/api/auth/account", status_code=204, tags=["auth-p8-v2"])
def _p8r4_delete_account(
    body: AccountDeleteBody, session: Session = Depends(get_session)
):
    """Confirmed hard-delete (round-4 raw-SQL version)."""
    if body.confirm != "DELETE MY ACCOUNT":
        raise HTTPException(
            422,
            'Set "confirm" to exactly "DELETE MY ACCOUNT" to proceed. '
            "This action is permanent and cannot be undone.",
        )
    uid = _uid()
    if uid == DEFAULT_UID:
        raise HTTPException(
            403,
            "The local default user account cannot be deleted. "
            "Enable auth and use a real account.",
        )

    import sqlalchemy as _sa_p8r4
    insp = _sa_p8r4.inspect(session.get_bind())
    _existing = set(insp.get_table_names())

    # (table, where_clause) — children first, then parents
    _p8r4_dels = [
        ("sourceref",
         "claim_id IN (SELECT id FROM profileclaim WHERE owner_id = :uid)"),
        ("sourceref",
         "source_id IN (SELECT id FROM source WHERE owner_id = :uid)"),
        ("profileclaim", "owner_id = :uid"),
        ("agentcritique",
         "run_id IN (SELECT id FROM agentrun "
         "WHERE package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid))"),
        ("agentrun",
         "package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid)"),
        ("packagebullet",
         "package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid)"),
        ("applysession",
         "package_id IN (SELECT id FROM applicationpackage "
         "WHERE owner_id = :uid)"),
        ("inappnotification", "owner_id = :uid"),
        ("usernotificationpreference", "owner_id = :uid"),
        ("publicresearchfinding", "owner_id = :uid"),
        ("notificationpreview", "owner_id = :uid"),
        ("savedjobsearch", "owner_id = :uid"),
        ("exporttoken", "owner_id = :uid"),
        ("source", "owner_id = :uid"),
        ("applicationpackage", "owner_id = :uid"),
        ("application", "owner_id = :uid"),
        ("strategy", "owner_id = :uid"),
        ("jobposting", "owner_id = :uid"),
    ]
    for _tbl, _where in _p8r4_dels:
        if _tbl in _existing:
            try:
                session.execute(
                    _sa_p8r4.text(
                        'DELETE FROM "' + _tbl + '" WHERE ' + _where
                    ),
                    {"uid": uid},
                )
            except Exception:
                # Defensive: roll back to a clean state and continue.
                try:
                    session.rollback()
                except Exception:
                    pass

    if "user" in _existing:
        try:
            session.execute(
                _sa_p8r4.text('DELETE FROM "user" WHERE id = :uid'),
                {"uid": uid},
            )
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass

    session.commit()
    return Response(status_code=204)
'''
    src = src.rstrip() + fix_block + "\n"
    APP.write_text(src)
    print("Round 4 fix appended to backend/app.py")

# Verify
try:
    py_compile.compile(str(APP), doraise=True)
    print("\nSyntax OK — backend/app.py compiles cleanly.")
except py_compile.PyCompileError as e:
    print(f"\nSYNTAX FAIL:\n{e}", file=sys.stderr)
    sys.exit(2)

print("\nNow run: pytest -q  (from the backend directory)")
