"""Seed a handful of demo leases for the "WeWork" organisation.

Clones n leases from the already-seeded Xtract.io portfolio (including their
extracted attributes, lease clauses, and administration records), re-tags the
copies with WeWork's org_id, and assigns fresh ids so they're fully independent
records. This gives the new WeWork tenant something to see before its own
users/data exist. CAM statements, disputes, rent schedules, payment schedules,
compliance and intelligence data are intentionally NOT cloned — those stay
Xtract-only until WeWork generates its own.
"""

import copy
import uuid

from app.mock_db import db
from app.services.administration_transformer import populate_administration_from_extraction


def seed_wework_demo_leases(n: int = 5) -> None:
    wework_org_id = db.get("org_ids", {}).get("WeWork")
    if not wework_org_id:
        print("WeWork org_id not resolved — skipping WeWork demo lease seed")
        return

    if any(lease.get("org_id") == wework_org_id for lease in db["leases"].values()):
        return  # already seeded

    source_lease_ids = list(db["leases"].keys())[:n]

    for source_lease_id in source_lease_ids:
        source_lease = db["leases"][source_lease_id]
        new_lease_id = str(uuid.uuid4())

        lease_copy = copy.deepcopy(source_lease)
        lease_copy["lease_id"] = new_lease_id
        lease_copy["org_id"] = wework_org_id
        db["leases"][new_lease_id] = lease_copy

        attrs_copy = []
        for attr in db["attributes"].get(source_lease_id, []):
            attr_copy = copy.deepcopy(attr)
            attr_copy["attribute_id"] = str(uuid.uuid4())
            attr_copy["lease_id"] = new_lease_id
            attrs_copy.append(attr_copy)
        db["attributes"][new_lease_id] = attrs_copy

        for clause in list(db["lease_clauses"].values()):
            if clause.get("lease_id") != source_lease_id:
                continue
            clause_copy = copy.deepcopy(clause)
            clause_copy["clause_id"] = str(uuid.uuid4())
            clause_copy["lease_id"] = new_lease_id
            db["lease_clauses"][clause_copy["clause_id"]] = clause_copy

        populate_administration_from_extraction(
            lease_id=new_lease_id,
            attrs=attrs_copy,
            lease_record=lease_copy,
            file_name=f"{lease_copy.get('store_name', 'Lease')}.pdf",
        )

    print(f"Seeded {len(source_lease_ids)} WeWork demo lease(s) (org_id={wework_org_id})")
