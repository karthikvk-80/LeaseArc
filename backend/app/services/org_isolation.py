"""Multi-tenancy helpers: resolve real Postgres org UUIDs and enforce org/role
isolation across the in-memory `db` dict that backs most routers.

`load_org_ids()` runs once at startup (after `seed_users_pg()`) and populates
`db["org_ids"]` with the real Postgres UUIDs for ALL organisations in the DB, e.g.
`{"Xtract.io": "<uuid>", "WeWork": "<uuid>", ...}`. Adding a new organisation to
Postgres automatically makes it available here without any code changes.

Seed modules look up their own org by name: `db["org_ids"]["Xtract.io"]`.
Routers use `Depends(current_org_id)` which resolves from the JWT — fully generic,
works for any org present in Postgres.
"""

from typing import Iterable

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, engine, get_db
from app.db_models import Organisation, User, UserSession
from app.mock_db import db
from app.routers.auth import _user_role_name, get_current_user


def load_org_ids() -> None:
    """Load ALL Postgres org UUIDs into db["org_ids"] (run once at startup).

    Any organisation added to Postgres is automatically included — no code change
    required to support new tenants.
    """
    if engine is None:
        print("DATABASE_URL not set — skipping org_ids load")
        db["org_ids"] = {}
        return

    session = SessionLocal()
    try:
        orgs = session.scalars(select(Organisation)).all()
        org_ids = {org.name: str(org.id) for org in orgs}
        db["org_ids"] = org_ids
        print(f"Loaded org_ids for {len(org_ids)} organisation(s): {list(org_ids.keys())}")
    finally:
        session.close()


def current_org_id(current: tuple[User, UserSession] = Depends(get_current_user)) -> str:
    """Org id of the logged-in user. Adding this dependency also enforces login."""
    user, _session = current
    return str(user.org_id)


def require_admin(
    current: tuple[User, UserSession] = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> User:
    """Like current_org_id, but additionally requires the 'admin' role."""
    user, _session = current
    role = _user_role_name(session, user)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def org_id_of_lease(lease_id: str | None) -> str | None:
    if not lease_id:
        return None
    lease = db["leases"].get(lease_id)
    if not lease:
        return None
    return lease.get("org_id")


def _record_org_id(record: dict, lease_field: str) -> str | None:
    """Extract org_id from a record, tolerating both snake_case and camelCase field names."""
    oid = record.get("org_id") or record.get("orgId")
    if oid is None:
        oid = org_id_of_lease(record.get(lease_field) or record.get("leaseId"))
    return oid


def scoped(records: Iterable[dict], org_id: str, lease_field: str = "lease_id") -> list[dict]:
    """Filter records to those belonging to org_id.

    Tolerates both snake_case `org_id` / `lease_id` and camelCase `orgId` / `leaseId`
    field names so all collections can be filtered consistently.
    """
    return [r for r in records if _record_org_id(r, lease_field) == org_id]


def require_same_org(record: dict, org_id: str, lease_field: str = "lease_id") -> None:
    """Raise 404 if record does not belong to org_id (direct org_id or via lease)."""
    if _record_org_id(record, lease_field) != org_id:
        raise HTTPException(status_code=404, detail="Not found")


def current_user_info(
    current: tuple[User, UserSession] = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    """Returns org_id, user_id, and is_admin for the authenticated user.

    Use instead of current_org_id when you need user-level (not just org-level)
    isolation — e.g. regular users should only see their own uploaded leases.
    """
    user, _session = current
    try:
        role = _user_role_name(session, user)
    except Exception as e:
        print(f"[org_isolation] WARNING: _user_role_name failed for user {user.id}: {e}")
        role = None
    is_admin = role == "admin"
    print(f"[org_isolation] user={user.email} id={user.id} org={user.org_id} role={role!r} is_admin={is_admin}")
    return {
        "org_id": str(user.org_id),
        "user_id": str(user.id),
        "is_admin": is_admin,
    }


def user_scoped_leases(leases: Iterable[dict], org_id: str, user_id: str, is_admin: bool) -> list[dict]:
    """Org-scope leases, then further restrict to the calling user's own leases if not admin.

    Leases without a created_by (seed/legacy data) are always included so demo data
    remains visible to all org users regardless of role.
    """
    org_leases = scoped(leases, org_id)
    if is_admin:
        print(f"[org_isolation] user_scoped_leases: admin user {user_id} → {len(org_leases)} org leases")
        return org_leases
    visible = [l for l in org_leases if l.get("created_by") is None or l.get("created_by") == user_id]
    owned = [l for l in org_leases if l.get("created_by") == user_id]
    print(f"[org_isolation] user_scoped_leases: user {user_id} → {len(visible)} visible ({len(owned)} owned, created_by samples: {[l.get('created_by') for l in org_leases[:3]]})")
    return visible


def require_lease_access(lease: dict, org_id: str, user_id: str, is_admin: bool) -> None:
    """Combined org + user check for single-lease endpoints.

    Admins can access any org lease. Regular users can only access leases they created
    (leases without created_by, i.e. seed data, are accessible to all).
    """
    require_same_org(lease, org_id)
    if not is_admin:
        creator = lease.get("created_by")
        if creator is not None and creator != user_id:
            raise HTTPException(status_code=404, detail="Lease not found")
