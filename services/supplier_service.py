"""
services/supplier_service.py

Business logic for suppliers. Same shape as medicine_service.py:
validation, the create/update/delete rules, and the list query.
"""

import re

from models import db, Supplier, Purchase

# Simple checks. Deliberately permissive: a real pharmacy has suppliers
# with landlines, mobiles and extensions, so we only reject obvious junk.
PHONE_PATTERN = re.compile(r"^[0-9 +\-()]{7,20}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def validate(form, supplier_id=None):
    """Returns (cleaned_values, errors)."""
    errors = []

    name = (form.get("supplier_name") or "").strip()
    phone = (form.get("phone") or "").strip()
    email = (form.get("email") or "").strip()
    city = (form.get("city") or "").strip()

    if len(name) < 3:
        errors.append("Supplier name must be at least 3 characters.")

    # a supplier name should not appear twice in the list
    clash = Supplier.query.filter(
        db.func.lower(Supplier.supplier_name) == name.lower()).first()
    if clash and clash.supplier_id != supplier_id:
        errors.append(f"{clash.supplier_name} is already in the list.")

    # phone and email are optional, but must look right if filled in
    if phone and not PHONE_PATTERN.match(phone):
        errors.append("Phone number may only contain digits, spaces, + - and ().")
    if email and not EMAIL_PATTERN.match(email):
        errors.append("Enter a valid email address, or leave it blank.")

    # at least one way to reach them
    if not phone and not email:
        errors.append("Give a phone number or an email address.")

    cleaned = {"supplier_name": name, "phone": phone or None,
               "email": email or None, "city": city or None}
    return cleaned, errors


def create(values):
    supplier = Supplier(**values)
    db.session.add(supplier)
    db.session.commit()
    return supplier


def update(supplier, values):
    for field, value in values.items():
        setattr(supplier, field, value)
    db.session.commit()
    return supplier


def can_delete(supplier):
    """
    A supplier with purchase invoices must not be deleted, because every
    purchase row points at them. Deleting would orphan those invoices and
    break the record of where stock came from.

    Returns (allowed, reason).
    """
    count = Purchase.query.filter_by(supplier_id=supplier.supplier_id).count()
    if count:
        return False, (f"{supplier.supplier_name} has {count} purchase "
                       "invoices on record and cannot be deleted.")
    return True, ""


def delete(supplier):
    db.session.delete(supplier)
    db.session.commit()


def search(query_text=""):
    q = Supplier.query
    if query_text:
        like = f"%{query_text}%"
        q = q.filter(db.or_(Supplier.supplier_name.ilike(like),
                            Supplier.city.ilike(like),
                            Supplier.email.ilike(like)))
    return q.order_by(Supplier.supplier_name).all()


def summary(supplier):
    """
    Numbers shown next to each supplier: how many invoices they have sent
    and what those invoices came to in total.
    """
    purchases = Purchase.query.filter_by(supplier_id=supplier.supplier_id).all()
    return {
        "invoices": len(purchases),
        "total_value": round(sum(p.total_cost for p in purchases), 2),
        "last_order": max((p.purchase_date for p in purchases), default=None),
    }