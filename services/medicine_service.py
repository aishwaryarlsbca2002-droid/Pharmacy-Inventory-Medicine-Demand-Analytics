"""
services/medicine_service.py

The BUSINESS LOGIC layer for medicines.

Why a separate file: your SOP asks for presentation, business logic and
database access to be separated. Routes in app.py handle the web request
and the response. The rules about what makes a medicine valid, and what
is allowed to be deleted, live here.

A viva question you may get: "where is your business logic?"
Answer: in services/, so the same rules apply no matter which route calls
them, and so they can be tested without starting a web server.
"""

from models import db, Medicine, Sale, PurchaseItem


# Shown in the category dropdown. Kept in one place so the form and the
# filter always agree.
CATEGORIES = [
    "Analgesic",
    "Anti-inflammatory",
    "Antibiotic",
    "Antihistamine",
    "Respiratory",
    "Antidiabetic",
    "Cardiovascular",
    "Gastrointestinal",
    "Supplement",
    "Other",
]


def validate(form, medicine_id=None):
    """
    Check one submitted medicine form.

    Returns (cleaned_values, errors). If errors is empty, the values are
    safe to save. medicine_id is passed when editing, so the medicine
    does not clash with its own code.
    """
    errors = []

    code = (form.get("medicine_code") or "").strip().upper()
    name = (form.get("name") or "").strip()
    category = (form.get("category") or "").strip()
    price_raw = (form.get("unit_price") or "").strip()
    reorder_raw = (form.get("reorder_level") or "").strip()

    if len(code) < 2:
        errors.append("Medicine code must be at least 2 characters.")
    if len(name) < 2:
        errors.append("Medicine name must be at least 2 characters.")
    if category not in CATEGORIES:
        errors.append("Choose a category from the list.")

    # the code must be unique across all medicines
    clash = Medicine.query.filter_by(medicine_code=code).first()
    if clash and clash.medicine_id != medicine_id:
        errors.append(f"The code {code} is already used by {clash.name}.")

    # numbers: reject anything that is not a number, or is negative
    price = None
    try:
        price = float(price_raw)
        if price < 0:
            errors.append("Unit price cannot be negative.")
    except ValueError:
        errors.append("Unit price must be a number.")

    reorder = None
    try:
        reorder = int(reorder_raw)
        if reorder < 0:
            errors.append("Reorder level cannot be negative.")
    except ValueError:
        errors.append("Reorder level must be a whole number.")

    cleaned = {"medicine_code": code, "name": name, "category": category,
               "unit_price": price, "reorder_level": reorder,
               "is_active": form.get("is_active") == "on"}
    return cleaned, errors


def create(values):
    medicine = Medicine(**values)
    db.session.add(medicine)
    db.session.commit()
    return medicine


def update(medicine, values):
    for field, value in values.items():
        setattr(medicine, field, value)
    db.session.commit()
    return medicine


def can_delete(medicine):
    """
    A medicine with sales or purchase history must NOT be deleted, because
    deleting it would break the foreign keys in those tables and destroy
    the history the forecasts are built on.

    Returns (allowed, reason).
    """
    sales = Sale.query.filter_by(medicine_id=medicine.medicine_id).count()
    purchases = PurchaseItem.query.filter_by(
        medicine_id=medicine.medicine_id).count()

    if sales or purchases:
        return False, (f"{medicine.name} has {sales} sales and {purchases} "
                       "purchase lines. Mark it inactive instead of deleting it.")
    return True, ""


def delete(medicine):
    db.session.delete(medicine)
    db.session.commit()


def search(query_text="", category="", stock_filter=""):
    """
    Build the medicine list for the table.

    SQLAlchemy builds parameterised queries, so values typed by the user
    are never pasted into SQL text. That is what prevents SQL injection.
    """
    q = Medicine.query

    if query_text:
        like = f"%{query_text}%"
        q = q.filter(db.or_(Medicine.name.ilike(like),
                            Medicine.medicine_code.ilike(like)))
    if category:
        q = q.filter(Medicine.category == category)

    results = q.order_by(Medicine.name).all()

    # stock level is computed in Python, not stored, so filter it here
    if stock_filter == "low":
        results = [m for m in results if m.is_low_stock]
    elif stock_filter == "out":
        results = [m for m in results if m.current_stock <= 0]

    return results