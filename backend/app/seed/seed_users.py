from app.mock_db import db

DEMO_USERS = [
    {
        "user_id": "user-001",
        "name": "Sarah Mitchell",
        "email": "director@leasearc.com",
        "role": "re_director",
        "org_id": "org-001",
    },
    {
        "user_id": "user-002",
        "name": "James Chen",
        "email": "admin@leasearc.com",
        "role": "lease_admin",
        "org_id": "org-001",
    },
    {
        "user_id": "user-003",
        "name": "Priya Sharma",
        "email": "legal@leasearc.com",
        "role": "legal",
        "org_id": "org-001",
    },
    {
        "user_id": "user-004",
        "name": "Michael Torres",
        "email": "finance@leasearc.com",
        "role": "finance",
        "org_id": "org-001",
    },
]


def seed_users():
    db["organisations"]["org-001"] = {
        "org_id": "org-001",
        "name": "RetailCo Global",
    }
    for u in DEMO_USERS:
        db["users"][u["user_id"]] = u
