#!/usr/bin/env python3
"""Aptiro Upgrade Phase 7 — Real Notifications patcher.

Run from the project root or point DST at the project directory.

  python3 patch_phase7.py [--dst /path/to/Aptiro]

Idempotent: checks for APTIRO_PHASE7_NOTIFICATIONS_MARKER before
appending. Safe to re-run if a previous run was interrupted.
"""
import argparse
import pathlib
import re
import sys

MARKER = "APTIRO_PHASE7_NOTIFICATIONS_MARKER"

APP_BLOCK = r'''

# ===========================================================================
# Upgrade Phase 7 — Real notification center
# In-app inbox + email via stdlib smtplib (zero new deps) + SMS/Twilio
# behind explicit opt-in. Default: nothing sent until configured.
# APTIRO_PHASE7_NOTIFICATIONS_MARKER — do not remove; idempotency guard.
# ===========================================================================
import smtplib as _smtplib
from email.mime.multipart import MIMEMultipart as _MIMEMultipart
from email.mime.text import MIMEText as _MIMEText

# --- Phase 7 config env vars -------------------------------------------
_SMTP_HOST = os.getenv("APTIRO_SMTP_HOST", "")
try:
    _SMTP_PORT = int(os.getenv("APTIRO_SMTP_PORT", "587") or "587")
except ValueError:
    _SMTP_PORT = 587
_SMTP_USER = os.getenv("APTIRO_SMTP_USER", "")
_SMTP_PASS = os.getenv("APTIRO_SMTP_PASS", "")
_SMTP_FROM = os.getenv("APTIRO_SMTP_FROM", "") or _SMTP_USER
_SMTP_TLS = os.getenv("APTIRO_SMTP_TLS", "starttls").lower()
_TWILIO_SID = os.getenv("APTIRO_TWILIO_SID", "")
_TWILIO_TOKEN = os.getenv("APTIRO_TWILIO_TOKEN", "")
_TWILIO_FROM = os.getenv("APTIRO_TWILIO_FROM", "")


def _smtp_configured() -> bool:
    return bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASS)


def _twilio_configured() -> bool:
    return bool(_TWILIO_SID and _TWILIO_TOKEN and _TWILIO_FROM)


# --- Phase 7 SQLModel tables -------------------------------------------

class UserNotificationPreference(SQLModel, table=True):
    """Per-user notification opt-in settings. Default: nothing is sent
    until the user explicitly enables a channel and supplies an address.
    In-app notifications are always persisted (zero external cost)."""
    __tablename__ = "usernotificationpreference"
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(index=True, unique=True)
    # In-app center (always on)
    in_app_enabled: bool = True
    # Email — off by default; requires address + SMTP server config
    email_enabled: bool = False
    email_address: str = ""
    email_daily_digest: bool = False
    email_weekly_digest: bool = False
    email_match_alerts: bool = False
    email_followup_reminders: bool = False
    # Score threshold for match alerts: 0 = disabled
    match_alert_threshold: int = Field(default=0)
    # SMS — off by default, explicit opt-in only, requires Twilio config
    sms_enabled: bool = False
    sms_phone: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class InAppNotification(SQLModel, table=True):
    """Real in-app notification center items. Persisted per owner with
    read/unread state. Cleared on delete; not in the privacy bundle."""
    __tablename__ = "inappnotification"
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(index=True)
    kind: str = ""
    subject: str = ""
    body: str = ""
    package_id: Optional[str] = None
    is_read: bool = False
    created_at: datetime = Field(default_factory=_now)


# --- Phase 7 helpers ---------------------------------------------------

def _get_or_create_prefs(session, owner_id: str) -> UserNotificationPreference:
    """Fetch the owner's preference row, creating a safe default if absent."""
    prefs = session.exec(
        select(UserNotificationPreference).where(
            UserNotificationPreference.owner_id == owner_id
        )
    ).first()
    if not prefs:
        prefs = UserNotificationPreference(owner_id=owner_id)
        session.add(prefs)
        session.commit()
        session.refresh(prefs)
    return prefs


def _send_email_raw(to_addr: str, subject: str, body: str) -> bool:
    """Send a plain-text email via the configured SMTP server.
    Returns True on success, False on any error. Never raises.
    No-ops silently when SMTP is not configured."""
    if not _smtp_configured() or not to_addr:
        return False
    try:
        msg = _MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = _SMTP_FROM
        msg["To"] = to_addr
        msg.attach(_MIMEText(body, "plain"))
        if _SMTP_TLS == "ssl":
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            with _smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, context=ctx) as srv:
                srv.login(_SMTP_USER, _SMTP_PASS)
                srv.sendmail(_SMTP_FROM, to_addr, msg.as_string())
        else:
            with _smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as srv:
                srv.ehlo()
                if _SMTP_TLS == "starttls":
                    srv.starttls()
                    srv.ehlo()
                if _SMTP_USER:
                    srv.login(_SMTP_USER, _SMTP_PASS)
                srv.sendmail(_SMTP_FROM, to_addr, msg.as_string())
        _logj("email_sent", to=to_addr, subject=subject)
        return True
    except Exception as exc:
        _logj("email_error", to=to_addr, error=str(exc))
        return False


def _send_sms_raw(to_phone: str, body: str) -> bool:
    """Send an SMS via Twilio REST. Uses httpx (already a dep).
    Returns True on success, False on any error. Never raises.
    No-ops when Twilio credentials are absent or httpx is unavailable."""
    if not _twilio_configured() or not to_phone or _httpx is None:
        return False
    try:
        url = ("https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json"
               % _TWILIO_SID)
        resp = _httpx.post(
            url,
            auth=(_TWILIO_SID, _TWILIO_TOKEN),
            data={"From": _TWILIO_FROM, "To": to_phone,
                  "Body": body[:1600]},
            timeout=10,
        )
        ok = resp.status_code in (200, 201)
        _logj("sms_sent" if ok else "sms_error",
              to=to_phone, status=resp.status_code)
        return ok
    except Exception as exc:
        _logj("sms_error", to=to_phone, error=str(exc))
        return False


def _deliver_notification(
    session,
    kind: str,
    subject: str,
    body: str,
    package_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> dict:
    """Deliver a notification through all configured channels for the owner.

    * Always writes an InAppNotification row when in_app_enabled (default).
    * Sends email only when email_enabled + address present + SMTP configured.
    * Sends SMS only when sms_enabled + phone present + Twilio configured.
    * Also writes a legacy NotificationPreview row for backwards compat.

    Returns a summary dict {in_app_id, email_sent, sms_sent}."""
    oid = owner_id or _uid()
    prefs = _get_or_create_prefs(session, oid)

    # 1. In-app
    in_app_id: Optional[str] = None
    if prefs.in_app_enabled:
        n = InAppNotification(
            owner_id=oid,
            kind=kind,
            subject=subject,
            body=body,
            package_id=package_id,
        )
        session.add(n)
        session.commit()
        session.refresh(n)
        in_app_id = n.id

    # 2. Email
    email_sent = False
    if prefs.email_enabled and prefs.email_address and _smtp_configured():
        email_sent = _send_email_raw(prefs.email_address, subject, body)

    # 3. SMS (explicit opt-in only)
    sms_sent = False
    if prefs.sms_enabled and prefs.sms_phone and _twilio_configured():
        sms_sent = _send_sms_raw(prefs.sms_phone, body)

    # 4. Legacy preview row (keeps existing /notifications history working)
    try:
        _persist_previews(
            session,
            NotificationKind.daily_digest,
            (subject, body),
            package_id=package_id,
            only=NotificationChannel.in_app,
        )
    except Exception:
        pass

    return {"in_app_id": in_app_id, "email_sent": email_sent,
            "sms_sent": sms_sent}


# --- Phase 7 Pydantic I/O models ---------------------------------------

class NotifPrefUpdate(BaseModel):
    in_app_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
    email_address: Optional[str] = None
    email_daily_digest: Optional[bool] = None
    email_weekly_digest: Optional[bool] = None
    email_match_alerts: Optional[bool] = None
    email_followup_reminders: Optional[bool] = None
    match_alert_threshold: Optional[int] = None
    sms_enabled: Optional[bool] = None
    sms_phone: Optional[str] = None


class NotifPrefOut(BaseModel):
    id: str
    owner_id: str
    in_app_enabled: bool
    email_enabled: bool
    email_address: str
    email_daily_digest: bool
    email_weekly_digest: bool
    email_match_alerts: bool
    email_followup_reminders: bool
    match_alert_threshold: int
    sms_enabled: bool
    sms_phone: str
    smtp_configured: bool
    twilio_configured: bool
    created_at: datetime
    updated_at: datetime


class InAppNotifOut(BaseModel):
    id: str
    owner_id: str
    kind: str
    subject: str
    body: str
    package_id: Optional[str]
    is_read: bool
    created_at: datetime


class NotifInboxOut(BaseModel):
    items: List[InAppNotifOut]
    unread_count: int


class SendDigestOut(BaseModel):
    subject: str
    in_app_id: Optional[str]
    email_sent: bool
    sms_sent: bool
    top_job_count: int


class SendAlertOut(BaseModel):
    alerts_generated: int
    above_threshold: int
    threshold: int


def _pref_out(p: UserNotificationPreference) -> NotifPrefOut:
    return NotifPrefOut(
        id=p.id, owner_id=p.owner_id,
        in_app_enabled=p.in_app_enabled,
        email_enabled=p.email_enabled, email_address=p.email_address,
        email_daily_digest=p.email_daily_digest,
        email_weekly_digest=p.email_weekly_digest,
        email_match_alerts=p.email_match_alerts,
        email_followup_reminders=p.email_followup_reminders,
        match_alert_threshold=p.match_alert_threshold,
        sms_enabled=p.sms_enabled, sms_phone=p.sms_phone,
        smtp_configured=_smtp_configured(),
        twilio_configured=_twilio_configured(),
        created_at=p.created_at, updated_at=p.updated_at,
    )


def _inapp_out(n: InAppNotification) -> InAppNotifOut:
    return InAppNotifOut(
        id=n.id, owner_id=n.owner_id, kind=n.kind,
        subject=n.subject, body=n.body, package_id=n.package_id,
        is_read=n.is_read, created_at=n.created_at,
    )


# --- Phase 7 routers ---------------------------------------------------

notif_prefs_router = APIRouter(prefix="/api/notifications",
                               tags=["notifications"])
notif_inbox_router = APIRouter(prefix="/api/notifications/inbox",
                               tags=["notifications"])
notif_send_router = APIRouter(prefix="/api/notifications/send",
                              tags=["notifications"])


@notif_prefs_router.get("/preferences", response_model=NotifPrefOut)
def get_notif_prefs(session: Session = Depends(get_session)):
    return _pref_out(_get_or_create_prefs(session, _uid()))


@notif_prefs_router.put("/preferences", response_model=NotifPrefOut)
def update_notif_prefs(body: NotifPrefUpdate,
                       session: Session = Depends(get_session)):
    prefs = _get_or_create_prefs(session, _uid())
    for field, val in body.model_dump(exclude_none=True).items():
        if field == "match_alert_threshold":
            val = max(0, min(100, int(val)))
        setattr(prefs, field, val)
    prefs.updated_at = _now()
    session.add(prefs)
    session.commit()
    session.refresh(prefs)
    return _pref_out(prefs)


@notif_inbox_router.get("", response_model=NotifInboxOut)
def get_inbox(session: Session = Depends(get_session)):
    items = session.exec(
        select(InAppNotification)
        .where(InAppNotification.owner_id == _uid())
        .order_by(InAppNotification.created_at.desc())
    ).all()
    unread = sum(1 for n in items if not n.is_read)
    return NotifInboxOut(items=[_inapp_out(n) for n in items],
                         unread_count=unread)


@notif_inbox_router.post("/read-all", response_model=NotifInboxOut)
def mark_all_read(session: Session = Depends(get_session)):
    items = session.exec(
        select(InAppNotification)
        .where(InAppNotification.owner_id == _uid())
        .where(InAppNotification.is_read == False)  # noqa: E712
    ).all()
    for n in items:
        n.is_read = True
        session.add(n)
    session.commit()
    return get_inbox(session=session)


@notif_inbox_router.post("/{notif_id}/read", response_model=InAppNotifOut)
def mark_read(notif_id: str, session: Session = Depends(get_session)):
    n = session.get(InAppNotification, notif_id)
    if not n or n.owner_id != _uid():
        raise HTTPException(404, "Notification not found")
    n.is_read = True
    session.add(n)
    session.commit()
    session.refresh(n)
    return _inapp_out(n)


@notif_inbox_router.delete("/{notif_id}", status_code=204)
def delete_notif(notif_id: str,
                 session: Session = Depends(get_session)):
    n = session.get(InAppNotification, notif_id)
    if not n or n.owner_id != _uid():
        raise HTTPException(404, "Notification not found")
    session.delete(n)
    session.commit()
    return Response(status_code=204)


@notif_send_router.post("/digest", response_model=SendDigestOut)
def send_digest(session: Session = Depends(get_session)):
    """Render and deliver a daily digest for the current user.
    Writes to in-app inbox always; sends email/SMS when configured."""
    subject, body = _render_digest(session)
    result = _deliver_notification(session, "daily_digest", subject, body)
    jobs = session.exec(select(JobPosting).where(
        JobPosting.is_archived == False)).all()  # noqa: E712
    return SendDigestOut(
        subject=subject,
        in_app_id=result["in_app_id"],
        email_sent=result["email_sent"],
        sms_sent=result["sms_sent"],
        top_job_count=min(3, len(jobs)),
    )


@notif_send_router.post("/match-alert", response_model=SendAlertOut)
def send_match_alert(session: Session = Depends(get_session)):
    """Check active jobs against the user's match-alert threshold and
    deliver an in-app (+ email/SMS) alert for each job above it.
    No-op when threshold is 0 (the default)."""
    prefs = _get_or_create_prefs(session, _uid())
    threshold = prefs.match_alert_threshold
    if threshold == 0:
        return SendAlertOut(alerts_generated=0, above_threshold=0,
                            threshold=0)

    jobs = session.exec(select(JobPosting).where(
        JobPosting.is_archived == False)).all()  # noqa: E712
    strat = _active_strategy(session)
    alerts = above = 0
    for j in jobs:
        sc = score_job(session, j, strat)["score"]
        if sc >= threshold:
            above += 1
            subj = ("High-fit match: %s @ %s (%d/100)"
                    % (j.title, j.company, sc))
            bd = (
                "%s at %s scored %d/100 against your active strategy, "
                "meeting your alert threshold of %d. "
                "Review it in Match Inbox." % (j.title, j.company, sc, threshold)
            )
            _deliver_notification(session, "match_threshold_alert",
                                   subj, bd)
            alerts += 1

    return SendAlertOut(alerts_generated=alerts, above_threshold=above,
                        threshold=threshold)


app.include_router(notif_prefs_router)
app.include_router(notif_inbox_router)
app.include_router(notif_send_router)
'''

TEST_BLOCK = r'''

# ===========================================================================
# Upgrade Phase 7 — Real Notifications
# ===========================================================================

def test_notification_prefs_default(client):
    """Default prefs: in_app on, email off, no address, threshold 0."""
    p = client.get("/api/notifications/preferences").json()
    assert p["in_app_enabled"] is True
    assert p["email_enabled"] is False
    assert p["email_address"] == ""
    assert p["match_alert_threshold"] == 0
    assert p["sms_enabled"] is False
    assert p["sms_phone"] == ""
    # SMTP / Twilio not configured in test environment
    assert p["smtp_configured"] is False
    assert p["twilio_configured"] is False


def test_notification_prefs_update(client):
    """PUT updates prefs; GET returns updated values."""
    client.put("/api/notifications/preferences", json={
        "email_enabled": True,
        "email_address": "test@example.com",
        "email_daily_digest": True,
        "match_alert_threshold": 75,
    })
    p = client.get("/api/notifications/preferences").json()
    assert p["email_enabled"] is True
    assert p["email_address"] == "test@example.com"
    assert p["email_daily_digest"] is True
    assert p["match_alert_threshold"] == 75


def test_notification_prefs_threshold_clamped(client):
    """Threshold is clamped to 0-100."""
    client.put("/api/notifications/preferences",
               json={"match_alert_threshold": 200})
    p = client.get("/api/notifications/preferences").json()
    assert p["match_alert_threshold"] == 100

    client.put("/api/notifications/preferences",
               json={"match_alert_threshold": -5})
    p = client.get("/api/notifications/preferences").json()
    assert p["match_alert_threshold"] == 0


def test_notification_inbox_starts_empty(client):
    """Fresh user has an empty in-app inbox with unread_count 0."""
    r = client.get("/api/notifications/inbox").json()
    assert r["unread_count"] == 0
    assert r["items"] == []


def test_notification_send_digest_creates_inapp(client):
    """POST /send/digest creates an in-app notification entry."""
    _seed_and_job(client)
    r = client.post("/api/notifications/send/digest")
    assert r.status_code == 200
    d = r.json()
    assert d["in_app_id"] is not None
    # SMTP not configured in test env → email_sent must be False
    assert d["email_sent"] is False
    assert d["sms_sent"] is False
    # In-app inbox now has one unread item
    inbox = client.get("/api/notifications/inbox").json()
    assert inbox["unread_count"] == 1
    assert inbox["items"][0]["kind"] == "daily_digest"


def test_notification_inbox_mark_read(client):
    """Mark a single notification as read; unread_count decreases."""
    _seed_and_job(client)
    client.post("/api/notifications/send/digest")
    inbox = client.get("/api/notifications/inbox").json()
    assert inbox["unread_count"] == 1
    nid = inbox["items"][0]["id"]

    r = client.post(f"/api/notifications/inbox/{nid}/read")
    assert r.status_code == 200
    assert r.json()["is_read"] is True

    inbox2 = client.get("/api/notifications/inbox").json()
    assert inbox2["unread_count"] == 0
    assert inbox2["items"][0]["is_read"] is True


def test_notification_inbox_mark_all_read(client):
    """POST /inbox/read-all clears all unread notifications."""
    _seed_and_job(client)
    client.post("/api/notifications/send/digest")
    client.post("/api/notifications/send/digest")
    inbox = client.get("/api/notifications/inbox").json()
    assert inbox["unread_count"] == 2

    r = client.post("/api/notifications/inbox/read-all")
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0


def test_notification_inbox_delete(client):
    """DELETE removes a notification from the inbox entirely."""
    _seed_and_job(client)
    client.post("/api/notifications/send/digest")
    inbox = client.get("/api/notifications/inbox").json()
    nid = inbox["items"][0]["id"]

    r = client.delete(f"/api/notifications/inbox/{nid}")
    assert r.status_code == 204

    inbox2 = client.get("/api/notifications/inbox").json()
    assert len(inbox2["items"]) == 0


def test_notification_match_alert_no_threshold(client):
    """Match alert is a no-op when threshold is 0 (default)."""
    _seed_and_job(client)
    r = client.post("/api/notifications/send/match-alert").json()
    assert r["alerts_generated"] == 0
    assert r["threshold"] == 0


def test_notification_match_alert_with_low_threshold(client):
    """Match alert with threshold=1 evaluates jobs and may fire alerts."""
    _seed_and_job(client)
    client.put("/api/notifications/preferences",
               json={"match_alert_threshold": 1})
    r = client.post("/api/notifications/send/match-alert").json()
    assert r["threshold"] == 1
    # above_threshold count is non-negative (scoring is deterministic mock)
    assert r["above_threshold"] >= 0


def test_notification_email_not_sent_without_smtp(client):
    """Enabling email prefs does NOT cause sends without SMTP config."""
    _seed_and_job(client)
    client.put("/api/notifications/preferences", json={
        "email_enabled": True,
        "email_address": "user@example.com",
        "email_daily_digest": True,
    })
    r = client.post("/api/notifications/send/digest").json()
    # Test env has no SMTP config → email_sent must remain False
    assert r["email_sent"] is False


def test_notification_sms_not_sent_without_twilio(client):
    """Enabling SMS prefs does NOT cause sends without Twilio config."""
    _seed_and_job(client)
    client.put("/api/notifications/preferences", json={
        "sms_enabled": True,
        "sms_phone": "+15550001234",
    })
    r = client.post("/api/notifications/send/digest").json()
    assert r["sms_sent"] is False


def test_notification_channels_still_no_external_send(client):
    """Existing /channels contract: sends_externally stays False in test env."""
    r = client.get("/api/notifications/channels").json()
    assert r["sends_externally"] is False


def test_notification_prefs_roundtrip(client):
    """Full prefs roundtrip: set all fields, read them back correctly."""
    client.put("/api/notifications/preferences", json={
        "email_enabled": True,
        "email_address": "rt@example.com",
        "email_daily_digest": True,
        "email_weekly_digest": False,
        "email_match_alerts": True,
        "email_followup_reminders": True,
        "match_alert_threshold": 80,
        "sms_enabled": False,
        "sms_phone": "",
    })
    p = client.get("/api/notifications/preferences").json()
    assert p["email_address"] == "rt@example.com"
    assert p["email_daily_digest"] is True
    assert p["email_match_alerts"] is True
    assert p["email_followup_reminders"] is True
    assert p["match_alert_threshold"] == 80
    assert p["sms_enabled"] is False


def test_phase7_migration_chain_extends_to_0009():
    """0009_phase7_notifications is the new head and chains to 0008."""
    import pathlib as _pathlib
    import importlib.util as _ilu2
    vdir = _pathlib.Path(__file__).parent / "alembic" / "versions"
    revs = {}
    for f in sorted(vdir.glob("0*.py")):
        spec = _ilu2.spec_from_file_location(f.stem, f)
        m = _ilu2.module_from_spec(spec)
        spec.loader.exec_module(m)
        revs[getattr(m, "revision", None)] = getattr(m, "down_revision", None)

    assert "0009_phase7_notifications" in revs, (
        "0009_phase7_notifications not found in alembic/versions/. "
        "Did you copy the migration file?"
    )
    assert revs["0009_phase7_notifications"] == "0008_phase6_public_research", (
        "0009 must chain back to 0008_phase6_public_research"
    )


def test_phase7_upgrade_health_field(client):
    """Health endpoint advertises upgrade_phases_shipped includes 7."""
    h = client.get("/api/health").json()
    shipped = h.get("upgrade_phases_shipped", [])
    assert 7 in shipped, (
        f"upgrade_phases_shipped must include 7, got {shipped}. "
        "Did the patcher update the health endpoint?"
    )
'''


def load(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def save(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def patch_app(app_path: pathlib.Path) -> bool:
    content = load(app_path)
    if MARKER in content:
        print(f"  [skip] {app_path.name} already has Phase 7 marker.")
        return False

    # 1. Append the Phase 7 block
    content += APP_BLOCK

    # 2. Patch upgrade_phases_shipped to include 7.
    #    Handles any form: [1,2,3,4,5,6] or [6] etc.
    def _add_7(m: re.Match) -> str:
        inner = m.group(1).strip().rstrip(",")
        nums = [x.strip() for x in inner.split(",") if x.strip()]
        if "7" not in nums:
            nums.append("7")
        return '"upgrade_phases_shipped": [%s]' % ", ".join(nums)

    patched = re.sub(
        r'"upgrade_phases_shipped":\s*\[([^\]]*)\]',
        _add_7,
        content,
    )
    if patched == content:
        # Fallback: health endpoint may build the list differently.
        # Insert 7 into the first numeric list containing 6.
        patched = re.sub(
            r'(\bupgrade_phases_shipped\b.*?\[)([^\]]*6[^\]]*)\]',
            lambda m2: m2.group(1) + m2.group(2).rstrip(", ") + ", 7]",
            content,
            count=1,
            flags=re.DOTALL,
        )
    save(app_path, patched)
    print(f"  [ok]   {app_path.name} — Phase 7 block appended.")
    return True


def patch_tests(test_path: pathlib.Path) -> bool:
    content = load(test_path)
    marker = "test_phase7_"
    if marker in content:
        print(f"  [skip] {test_path.name} already has Phase 7 tests.")
        return False
    content += TEST_BLOCK
    save(test_path, content)
    print(f"  [ok]   {test_path.name} — Phase 7 tests appended.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Aptiro Phase 7 patcher")
    parser.add_argument("--dst", default=".",
                        help="Project root (default: current directory)")
    args = parser.parse_args()

    root = pathlib.Path(args.dst).resolve()
    backend = root / "backend"

    app_py = backend / "app.py"
    test_py = backend / "test_app.py"

    for p in (app_py, test_py):
        if not p.exists():
            print(f"ERROR: {p} not found. Run from the project root or "
                  f"pass --dst /path/to/Aptiro", file=sys.stderr)
            sys.exit(1)

    print("Patching backend files…")
    patch_app(app_py)
    patch_tests(test_py)
    print("Done. Run: cd backend && pytest -q")


if __name__ == "__main__":
    main()
