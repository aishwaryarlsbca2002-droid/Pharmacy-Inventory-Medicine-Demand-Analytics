"""
services/expiry_service.py

The Expiry Alerts module.

A pharmacy cannot dispense expired medicine, so every batch sitting on
the shelf has a deadline. This module answers three questions:

    what has already expired and must come off the shelf
    what expires soon and should be dispensed first
    how much money is tied up in each case

The subtle part is that a batch number alone tells you nothing. A batch
that expired last year does not matter if it sold out months ago. So
every figure here is based on the stock still remaining in each batch,
computed by Medicine.batches_in_stock(), not on the batch records alone.
"""

from datetime import date

from models import db, Medicine, PurchaseItem

# The three bands shown on the page, in days remaining.
CRITICAL_DAYS = 30
WARNING_DAYS = 90


def collect(window_days=None, category=None, status=None):
    """
    Every batch that still holds stock, with its expiry position.

    window_days limits the list to batches expiring within that many days
    (expired batches are always included, because they need action now).

    Returns a list of dicts, sorted with the most urgent first.
    """
    rows = []

    query = Medicine.query
    if category:
        query = query.filter(Medicine.category == category)

    for medicine in query.all():
        for batch, remaining in medicine.batches_in_stock():
            days_left = batch.days_to_expiry

            if window_days is not None and days_left > window_days:
                continue

            rows.append({
                "medicine": medicine,
                "batch": batch,
                "remaining": remaining,
                "days_left": days_left,
                "status": batch.expiry_status,
                # value at cost, because a write-off is a loss of what it
                # cost to buy, not of what it might have sold for
                "value_at_cost": round(remaining * batch.cost, 2),
                "value_at_retail": round(remaining * medicine.unit_price, 2),
                "supplier": batch.purchase.supplier,
            })

    if status:
        rows = [r for r in rows if r["status"] == status]

    rows.sort(key=lambda r: r["days_left"])
    return rows


def summary(rows):
    """Headline counts and the money at stake, for the top of the page."""
    expired = [r for r in rows if r["days_left"] < 0]
    critical = [r for r in rows if 0 <= r["days_left"] <= CRITICAL_DAYS]
    warning = [r for r in rows
               if CRITICAL_DAYS < r["days_left"] <= WARNING_DAYS]

    return {
        "expired_batches": len(expired),
        "expired_units": sum(r["remaining"] for r in expired),
        "expired_value": round(sum(r["value_at_cost"] for r in expired), 2),

        "critical_batches": len(critical),
        "critical_units": sum(r["remaining"] for r in critical),
        "critical_value": round(sum(r["value_at_cost"] for r in critical), 2),

        "warning_batches": len(warning),
        "warning_units": sum(r["remaining"] for r in warning),

        "total_at_risk": round(sum(r["value_at_cost"]
                                   for r in expired + critical), 2),
    }


def by_supplier(rows):
    """
    Which suppliers the at-risk stock came from.

    Useful diagnostically: a supplier who repeatedly sends short-dated
    stock is costing the pharmacy money, and that is a conversation to
    have at the next order.
    """
    totals = {}
    for r in rows:
        if r["days_left"] > CRITICAL_DAYS:
            continue
        name = r["supplier"].supplier_name
        entry = totals.setdefault(name, {"name": name, "batches": 0,
                                         "units": 0, "value": 0.0})
        entry["batches"] += 1
        entry["units"] += r["remaining"]
        entry["value"] += r["value_at_cost"]

    result = list(totals.values())
    for entry in result:
        entry["value"] = round(entry["value"], 2)
    result.sort(key=lambda e: e["value"], reverse=True)
    return result


def categories():
    """Category list for the filter dropdown."""
    rows = (db.session.query(Medicine.category)
            .distinct().order_by(Medicine.category).all())
    return [r[0] for r in rows]