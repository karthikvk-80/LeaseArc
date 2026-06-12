"""Idempotent Postgres seed for the auth demo accounts (Phase 1).

Seeds the "RetailCo Global" organisation and two demo users (admin / user),
each assigned their matching role from schema.sql's seeded `roles` table.
Does not touch mock_db.py / seed_users.py, which still seed the legacy
user-001..004 records the other mock seed modules depend on.
"""

from sqlalchemy import delete, select

from app.db import SessionLocal, engine
from app.db_models import Organisation, Role, User, UserRole
from app.security import hash_password

ORG_NAME = "Xtract.io"
WEWORK_ORG_NAME = "WeWork"

DEMO_USERS = [
    {
        "email": "admin@leasearc.com",
        "password": "Admin@123",
        "first_name": "James",
        "last_name": "Chen",
        "role": "admin",
        "department": "Lease Administration",
    },
    {
        "email": "user1@leasearc.com",
        "password": "User@123",
        "first_name": "Alex",
        "last_name": "Carter",
        "role": "user",
        "department": "Operations",
    },
]

# Real Xtract team accounts. Default password "Xtract@123" — share with each
# person and have them change it once a self-service password-change flow exists.
# Note: only applies to NEW users; existing rows keep their current password_hash
# (this seed never updates an existing user's password).
TEAM_USERS = [
    {"email": "aswatth@xtract.io", "password": "Xtract@123", "first_name": "Aswatth", "last_name": "Krishna", "role": "admin", "department": "Xtract"},
    {"email": "mukundhan.chari@xtract.io", "password": "Xtract@123", "first_name": "Mukundh", "last_name": "Chari", "role": "admin", "department": "Xtract"},
    {"email": "karthikeyan@xtract.io", "password": "Xtract@123", "first_name": "Karthikeyan", "last_name": "", "role": "user", "department": "Xtract"},
    {"email": "kk@xtract.io", "password": "Xtract@123", "first_name": "KK", "last_name": "", "role": "admin", "department": "Xtract"},
    {"email": "prashanth@xtract.io", "password": "Xtract@123", "first_name": "Prashanth", "last_name": "", "role": "user", "department": "Xtract"},
    {"email": "naveena@xtract.io", "password": "Xtract@123", "first_name": "Naveena", "last_name": "", "role": "user", "department": "Xtract"},
    {"email": "kaviya.s@xtract.io", "password": "Xtract@123", "first_name": "Kaviya", "last_name": "S", "role": "user", "department": "Xtract"},
    {"email": "shalini@xtract.io", "password": "Xtract@123", "first_name": "Shalini", "last_name": "", "role": "user", "department": "Xtract"},
]

WEWORK_USERS = [
    {"email": "admin@wework.com", "password": "Wework@123", "first_name": "WeWork", "last_name": "Admin", "role": "admin", "department": "Administration"},
    {"email": "user@wework.com", "password": "Wework@123", "first_name": "WeWork", "last_name": "User", "role": "user", "department": "Operations"},
    {"email": "riya.taneja@wework.co.in", "password": "Wework@123", "first_name": "Riya", "last_name": "Taneja", "role": "user", "department": "WeWork"},
    {"email": "devyani.jadhav@wework.co.in", "password": "Wework@123", "first_name": "Devyani", "last_name": "Jadhav", "role": "user", "department": "WeWork"},
    {"email": "tejaswini.kamjula@wework.co.in", "password": "Wework@123", "first_name": "Tejaswini", "last_name": "Kamjula", "role": "user", "department": "WeWork"},
    {"email": "rupesh.kumar@wework.co.in", "password": "Wework@123", "first_name": "Rupesh", "last_name": "Kumar", "role": "admin", "department": "WeWork"},
    {"email": "samarth.bhandari@wework.co.in", "password": "Wework@123", "first_name": "Samarth", "last_name": "Bhandari", "role": "admin", "department": "WeWork"},
]


def _set_user_role(db, user: User, role: Role, org_id) -> None:
    """Idempotent: ensure the user has exactly this role in org_id, removing any other role rows."""
    # Remove stale role assignments for this (user, org) pair
    db.execute(
        delete(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.org_id == org_id,
            UserRole.role_id != role.id,
        )
    )
    exists = db.scalar(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.role_id == role.id,
            UserRole.org_id == org_id,
        )
    )
    if exists is None:
        db.add(UserRole(user_id=user.id, role_id=role.id, org_id=org_id, assigned_by=user.id))


def _ensure_role(db, name: str) -> Role:
    """Get-or-create a system-wide role (org_id=NULL). schema.sql seeds these, but
    this fallback self-heals environments where schema.sql wasn't applied in full."""
    role = db.scalar(select(Role).where(Role.name == name, Role.org_id.is_(None)))
    if role is None:
        role = Role(name=name, description=f"Auto-created: {name}")
        db.add(role)
        db.flush()
        print(f"[seed_users_pg] created missing role '{name}'")
    return role


def seed_users_pg():
    if engine is None:
        print("DATABASE_URL not set — skipping Postgres user seed")
        return

    db = SessionLocal()
    try:
        org = db.scalar(select(Organisation).where(Organisation.name == ORG_NAME))
        if org is None:
            org = Organisation(name=ORG_NAME)
            db.add(org)
            db.flush()

        wework_org = db.scalar(select(Organisation).where(Organisation.name == WEWORK_ORG_NAME))
        if wework_org is None:
            wework_org = Organisation(name=WEWORK_ORG_NAME)
            db.add(wework_org)
            db.flush()

        for demo in DEMO_USERS + TEAM_USERS:
            user = db.scalar(select(User).where(User.email == demo["email"]))
            if user is None:
                user = User(
                    org_id=org.id,
                    first_name=demo["first_name"],
                    last_name=demo["last_name"],
                    email=demo["email"],
                    password_hash=hash_password(demo["password"]),
                    department=demo["department"],
                    is_verified=True,
                )
                db.add(user)
                db.flush()

            role = _ensure_role(db, demo["role"])
            _set_user_role(db, user, role, org.id)

        for ww in WEWORK_USERS:
            user = db.scalar(select(User).where(User.email == ww["email"]))
            if user is None:
                user = User(
                    org_id=wework_org.id,
                    first_name=ww["first_name"],
                    last_name=ww["last_name"],
                    email=ww["email"],
                    password_hash=hash_password(ww["password"]),
                    department=ww["department"],
                    is_verified=True,
                )
                db.add(user)
                db.flush()

            role = _ensure_role(db, ww["role"])
            _set_user_role(db, user, role, wework_org.id)

        db.commit()
        total = len(DEMO_USERS) + len(TEAM_USERS)
        print(f"Postgres auth seed: org '{ORG_NAME}' + {total} users ready, org '{WEWORK_ORG_NAME}' + {len(WEWORK_USERS)} users ready")
    finally:
        db.close()
