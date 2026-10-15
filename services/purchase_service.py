"""
services/purchase_service.py

Business logic for recording a goods-received note (a purchase).

A purchase has two parts, which is why it needs two tables:
  - the invoice itself      -> purchases      (supplier, date, invoice no.)
  - the medicines on it     -> purchase_items (one row per batch received)

One delivery can contain several medicines, and the same medicine can
arrive as two batches with different expiry dates. That is why the lines
are a separate table rather than columns on the invoice.
"""

from datetime import date, timedelta

from models import db, Purchase, PurchaseItem, Medicine, Supplier

# A batch arriving with less than this left is almost certainly a typo
# in the expiry date, so we warn about it.
SHORT_DATED_DAYS = 90


def validate(form, purchase_id=None):
    """
    Check a submitted purchase form.

    The line items arrive as parallel lists, because the form posts
    several rows with the same field names:
        medicine_id[] , quantity[] , batch_no[] , expiry_date[] , cost[]

    Returns (header_values, line_values, errors, warnings).
    """
    errors, warnings = [], []

    # ---------- the invoice header ----------
    supplier_id = form.get("supplier_id", "").strip()
    purchase_date_raw = form.get("purchase_date", "").strip()
    invoice_no = form.get("invoice_no", "").strip().upper()

    supplier = None
    if not supplier_id:
        errors.append("Choose a supplier.")
    else:
        supplier = Supplier.query.get(int(supplier_id))
        if supplier is None:
            errors.append("That supplier no longer exists.")

    purchase_date = parse_date(purchase_date_raw)
    if purchase_date is None:
        errors.append("Enter a valid purchase date.")
    elif purchase_date > date.today():
        errors.append("The purchase date cannot be in the future.")

    if len(invoice_no) < 3:
        errors.append("Invoice number must be at least 3 characters.")
    else:
        clash = Purchase.query.filter_by(invoice_no=invoice_no).first()
        if clash and clash.purchase_id != purchase_id:
            errors.append(f"Invoice {invoice_no} has already been recorded.")

    header = {"supplier_id": int(supplier_id) if supplier_id.isdigit() else None,
              "purchase_date": purchase_date, "invoice_no": invoice_no}

    # ---------- the line items ----------
    medicine_ids = form.getlist("medicine_id")
    quantities = form.getlist("quantity")
    batches = form.getlist("batch_no")
    expiries = form.getlist("expiry_date")
    costs = form.getlist("cost")

    lines = []
    seen_batches = set()

    for i in range(len(medicine_ids)):
        med_raw = (medicine_ids[i] or "").strip()
        qty_raw = (quantities[i] or "").strip()
        batch = (batches[i] or "").strip().upper()
        expiry_raw = (expiries[i] or "").strip()
        cost_raw = (costs[i] or "").strip()

        # a completely blank row is just an unused row, so skip it
        if not any([med_raw, qty_raw, batch, expiry_raw, cost_raw]):
            continue

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

        cost = None
        try:
            cost = float(cost_raw)
            if cost < 0:
                line_errors.append(f"{label}: cost cannot be negative.")
        except ValueError:
            line_errors.append(f"{label}: cost must be a number.")

        if len(batch) < 2:
            line_errors.append(f"{label}: enter a batch number.")

        expiry = parse_date(expiry_raw)
        if expiry is None:
            line_errors.append(f"{label}: enter a valid expiry date.")
        elif purchase_date and expiry <= purchase_date:
            # stock that expires before it arrives cannot be sold
            line_errors.append(f"{label}: expiry date must be after the "
                               "purchase date.")

        # the same batch number must not appear twice for one medicine
        if medicine and batch:
            key = (medicine.medicine_id, batch)
            if key in seen_batches:
                line_errors.append(f"{label}: batch {batch} is listed twice "
                                   f"for {medicine.name}.")
            seen_batches.add(key)

            existing = PurchaseItem.query.filter_by(
                medicine_id=medicine.medicine_id, batch_no=batch).first()
            if existing:
                line_errors.append(f"{label}: batch {batch} for {medicine.name} "
                                   "is already on another invoice.")

        errors.extend(line_errors)

        # warn, but still allow: short-dated stock is sometimes bought on purpose
        if not line_errors and expiry:
            days_left = (expiry - date.today()).days
            if days_left < SHORT_DATED_DAYS:
                warnings.append(f"{label}: {medicine.name} batch {batch} "
                                f"expires in {days_left} days.")

        lines.append({"medicine_id": medicine.medicine_id if medicine else None,
                      "quantity": quantity, "batch_no": batch,
                      "expiry_date": expiry, "cost": cost,
                      # kept only to refill the form after an error
                      "medicine_raw": med_raw, "quantity_raw": qty_raw,
                      "expiry_raw": expiry_raw, "cost_raw": cost_raw})

    if not lines:
        errors.append("Add at least one medicine to the invoice.")

    return header, lines, errors, warnings


def parse_date(text):
    """Read a yyyy-mm-dd date from the form. Returns None if unreadable."""
    try:
        return date.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def create(header, lines):
    """
    Save the invoice and its lines together.

    Everything is added to one session and committed once, so either the
    whole delivery is recorded or none of it is. A half-saved invoice
    would leave stock counts wrong.
    """
    purchase = Purchase(**header)
    db.session.add(purchase)
    db.session.flush()            # assigns purchase_id without committing yet

    for line in lines:
        db.session.add(PurchaseItem(
            purchase_id=purchase.purchase_id,
            medicine_id=line["medicine_id"],
            quantity=line["quantity"],
            batch_no=line["batch_no"],
            expiry_date=line["expiry_date"],
            cost=line["cost"]))

    db.session.commit()
    return purchase


def recent(limit=50, supplier_id=None, batch_query=""):
    """
    Recent deliveries, newest first.

    batch_query searches the batch numbers inside each invoice. That is
    the question asked during a product recall: a manufacturer withdraws
    batch X, and the pharmacy needs to know which delivery brought it in
    and from which supplier.
    """
    q = Purchase.query
    if supplier_id:
        q = q.filter_by(supplier_id=supplier_id)
    if batch_query:
        q = q.join(PurchaseItem).filter(
            PurchaseItem.batch_no.ilike(f"%{batch_query}%")).distinct()
    return q.order_by(Purchase.purchase_date.desc(),
                      Purchase.purchase_id.desc()).limit(limit).all()


def suggested_expiry():
    """A sensible default for the date picker: two years out."""
    return (date.today() + timedelta(days=730)).isoformat()



def can_delete(purchase):
    """
    Deleting a delivery is allowed only in the narrow case where it is
    obviously a data-entry mistake and nothing depends on it yet:

      1. it was entered today, and
      2. removing it would not push any medicine's stock below zero,
         which would mean stock from this delivery has already been sold.

    An older delivery is corrected with a stock adjustment instead, so
    the original invoice stays on record. That is how real pharmacy and
    accounting systems handle a correction: you add a document, you do
    not quietly rewrite one.

    Returns (allowed, reason).
    """
    if purchase.purchase_date != date.today():
        return False, (f"Invoice {purchase.invoice_no} was recorded on "
                       f"{purchase.purchase_date.strftime('%d %b %Y')}. "
                       "Only deliveries entered today can be removed; correct "
                       "an older one with a stock adjustment.")

    for item in purchase.items:
        remaining = item.medicine.current_stock - item.quantity
        if remaining < 0:
            return False, (f"{item.medicine.name} would drop to {remaining} "
                           "units, so stock from this delivery has already "
                           "been sold. It cannot be removed.")

    return True, ""


def delete(purchase):
    """Remove the invoice and its lines together, in one transaction."""
    db.session.delete(purchase)       # cascade removes the purchase_items
    db.session.commit()