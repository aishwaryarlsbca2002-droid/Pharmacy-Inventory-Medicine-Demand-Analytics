"""
services/sale_service.py

Business logic for recording a sale (dispensing medicine at the counter).

The difference from a purchase: a purchase can always be recorded, because
stock arriving is simply a fact. A sale cannot. You cannot sell what you
do not have, so every line is checked against current stock before
anything is saved.

The sale also reports which batch the stock comes out of, using FEFO
(First Expired, First Out), the rule pharmacies dispense by. Our schema
records the sale against the medicine rather than the batch, so this is
shown to the user as guidance rather than stored.
"""

from datetime import date

from models import db, Sale, Medicine


def validate(form):
    """
    Check a submitted sale form.

    Lines arrive as parallel lists:
        medicine_id[] , quantity[] , selling_price[]

    Returns (sale_date, lines, errors, warnings).
    """
    errors, warnings = [], []

    sale_date = parse_date(form.get("sale_date", "").strip())
    if sale_date is None:
        errors.append("Enter a valid sale date.")
    elif sale_date > date.today():
        errors.append("The sale date cannot be in the future.")

    medicine_ids = form.getlist("medicine_id")
    quantities = form.getlist("quantity")
    prices = form.getlist("selling_price")

    lines = []
    # One medicine may appear on two lines of the same receipt, so we track
    # the running total per medicine rather than checking each line alone.
    requested = {}

    for i in range(len(medicine_ids)):
        med_raw = (medicine_ids[i] or "").strip()
        qty_raw = (quantities[i] or "").strip()
        price_raw = (prices[i] or "").strip()

        if not any([med_raw, qty_raw, price_raw]):
            continue                      # an unused row

        label = f"Line {len(lines) + 1}"
        line_errors = []

        medicine = None
        if not med_raw.isdigit():
            line_errors.append(f"{label}: choose a medicine.")
        else:
            medicine = Medicine.query.get(int(med_raw))
            if medicine is None:
                line_errors.append(f"{label}: that medicine no longer exists.")

        quantity = None
        try:
            quantity = int(qty_raw)
            if quantity <= 0:
                line_errors.append(f"{label}: quantity must be more than zero.")
        except ValueError:
            line_errors.append(f"{label}: quantity must be a whole number.")

        price = None
        try:
            price = float(price_raw)
            if price < 0:
                line_errors.append(f"{label}: price cannot be negative.")
        except ValueError:
            line_errors.append(f"{label}: price must be a number.")

        # ---- the stock check, the point of this whole module ----
        if medicine and quantity and quantity > 0:
            available = medicine.current_stock
            already = requested.get(medicine.medicine_id, 0)
            if already + quantity > available:
                remaining = available - already
                line_errors.append(
                    f"{label}: only {remaining} units of {medicine.name} "
                    f"are in stock, and {already + quantity} were requested.")
            else:
                requested[medicine.medicine_id] = already + quantity

                # warn when the sale takes the medicine below its reorder level
                left = available - requested[medicine.medicine_id]
                if left <= medicine.reorder_level:
                    warnings.append(
                        f"{medicine.name} drops to {left} units, at or below "
                        f"its reorder level of {medicine.reorder_level}.")

                # warn when the batch being dispensed is close to expiry
                batch = next_batch(medicine)
                if batch and batch.days_to_expiry <= 30:
                    warnings.append(
                        f"{medicine.name}: dispense batch {batch.batch_no} "
                        f"first, it expires in {batch.days_to_expiry} days.")

        errors.extend(line_errors)

        lines.append({"medicine_id": medicine.medicine_id if medicine else None,
                      "quantity": quantity, "selling_price": price,
                      "medicine": medicine,
                      "medicine_raw": med_raw, "quantity_raw": qty_raw,
                      "price_raw": price_raw})

    if not lines:
        errors.append("Add at least one medicine to the sale.")

    return sale_date, lines, errors, warnings


def next_batch(medicine):
    """The batch that should be dispensed next under FEFO, or None."""
    in_stock = medicine.batches_in_stock()
    return in_stock[0][0] if in_stock else None


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def create(sale_date, lines):
    """
    Save every line of the sale in one transaction.

    As with purchases, either the whole receipt is recorded or none of it,
    so stock can never be left half-decremented.
    """
    saved = []
    for line in lines:
        sale = Sale(medicine_id=line["medicine_id"],
                    sale_date=sale_date,
                    quantity=line["quantity"],
                    selling_price=line["selling_price"])
        db.session.add(sale)
        saved.append(sale)
    db.session.commit()
    return saved


def recent(limit=60, medicine_id=None, on_date=None):
    """Recent sales, newest first."""
    q = Sale.query
    if medicine_id:
        q = q.filter_by(medicine_id=medicine_id)
    if on_date:
        q = q.filter_by(sale_date=on_date)
    return q.order_by(Sale.sale_date.desc(),
                      Sale.sale_id.desc()).limit(limit).all()


def day_summary(on_date):
    """Totals for one day, used on the sales page header."""
    rows = Sale.query.filter_by(sale_date=on_date).all()
    return {
        "transactions": len(rows),
        "units": sum(r.quantity for r in rows),
        "revenue": round(sum(r.revenue for r in rows), 2),
    }


def sellable_medicines():
    """
    Medicines that can actually be sold: active, and with stock on hand.

    Showing an out-of-stock medicine in the dropdown only to reject it on
    submit wastes the user's time, so they are filtered out here.
    """
    rows = (Medicine.query.filter_by(is_active=True)
            .order_by(Medicine.name).all())
    return [m for m in rows if m.current_stock > 0]