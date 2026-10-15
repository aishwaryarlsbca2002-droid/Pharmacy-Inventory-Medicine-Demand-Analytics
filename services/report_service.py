"""
services/report_service.py

The Reports module: printable, exportable summaries.

The Analytics page is for looking at. Reports are for taking away, which
is a different job. Each report here returns the same shape:

    {"title": ..., "columns": [...], "rows": [[...], ...], "totals": [...]}

That uniform shape is what lets one CSV export route handle every report
rather than needing one export function per report.
"""

from datetime import date, timedelta

from sqlalchemy import func

from models import db, Medicine, Supplier, Sale, Purchase, PurchaseItem


def _range(days):
    """A period ending at the most recent sale, not today."""
    last = db.session.query(func.max(Sale.sale_date)).scalar() or date.today()
    return last - timedelta(days=days - 1), last


# ---------------------------------------------------------- the reports

def stock_report():
    """Current stock position for every medicine, with its value."""
    rows = []
    for m in Medicine.query.order_by(Medicine.name).all():
        stock = m.current_stock
        rows.append([
            m.medicine_code, m.name, m.category,
            round(m.unit_price, 2), stock, m.reorder_level,
            "Below reorder" if m.is_low_stock else "OK",
            round(stock * m.unit_price, 2),
        ])

    return {
        "title": "Stock position",
        "subtitle": "Every medicine, its stock on hand and its retail value",
        "columns": ["Code", "Medicine", "Category", "Unit price",
                    "In stock", "Reorder at", "Status", "Value at retail"],
        "numeric": [3, 4, 5, 7],
        "rows": rows,
        "totals": ["", "", "", "", sum(r[4] for r in rows), "", "",
                   round(sum(r[7] for r in rows), 2)],
    }


def sales_report(days=30):
    """Sales by medicine over a period, with its share of revenue."""
    start, end = _range(days)

    results = (db.session.query(
                    Medicine.medicine_code, Medicine.name, Medicine.category,
                    func.sum(Sale.quantity),
                    func.sum(Sale.quantity * Sale.selling_price),
                    func.count(Sale.sale_id))
               .join(Sale, Sale.medicine_id == Medicine.medicine_id)
               .filter(Sale.sale_date >= start, Sale.sale_date <= end)
               .group_by(Medicine.medicine_id)
               .order_by(func.sum(Sale.quantity * Sale.selling_price).desc())
               .all())

    revenue_total = sum(float(r[4]) for r in results) or 1

    rows = []
    for code, name, category, units, revenue, txns in results:
        rows.append([code, name, category, int(txns), int(units),
                     round(float(revenue), 2),
                     round(100 * float(revenue) / revenue_total, 1)])

    return {
        "title": f"Sales, last {days} days",
        "subtitle": f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}",
        "columns": ["Code", "Medicine", "Category", "Transactions",
                    "Units sold", "Revenue", "Share %"],
        "numeric": [3, 4, 5, 6],
        "rows": rows,
        "totals": ["", "", "", sum(r[3] for r in rows),
                   sum(r[4] for r in rows),
                   round(sum(r[5] for r in rows), 2), 100.0],
    }


def purchase_report(days=90):
    """What was bought, from whom, over a period."""
    start, end = _range(days)

    results = (db.session.query(
                    Supplier.supplier_name,
                    func.count(func.distinct(Purchase.purchase_id)),
                    func.sum(PurchaseItem.quantity),
                    func.sum(PurchaseItem.quantity * PurchaseItem.cost))
               .join(Purchase, Purchase.supplier_id == Supplier.supplier_id)
               .join(PurchaseItem,
                     PurchaseItem.purchase_id == Purchase.purchase_id)
               .filter(Purchase.purchase_date >= start,
                       Purchase.purchase_date <= end)
               .group_by(Supplier.supplier_id)
               .order_by(func.sum(PurchaseItem.quantity *
                                  PurchaseItem.cost).desc())
               .all())

    rows = [[name, int(invoices), int(units), round(float(value), 2),
             round(float(value) / int(invoices), 2)]
            for name, invoices, units, value in results]

    return {
        "title": f"Purchases, last {days} days",
        "subtitle": f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}",
        "columns": ["Supplier", "Invoices", "Units received",
                    "Total cost", "Average invoice"],
        "numeric": [1, 2, 3, 4],
        "rows": rows,
        "totals": ["", sum(r[1] for r in rows), sum(r[2] for r in rows),
                   round(sum(r[3] for r in rows), 2), ""],
    }


def expiry_report():
    """Batches still holding stock, within 180 days of expiry."""
    from services import expiry_service

    rows = []
    for r in expiry_service.collect(window_days=180):
        rows.append([
            r["medicine"].name, r["batch"].batch_no,
            r["supplier"].supplier_name,
            r["batch"].expiry_date.strftime("%Y-%m-%d"),
            r["days_left"], r["remaining"], r["value_at_cost"], r["status"],
        ])

    return {
        "title": "Expiry risk",
        "subtitle": "Batches still holding stock, expiring within 180 days",
        "columns": ["Medicine", "Batch", "Supplier", "Expires",
                    "Days left", "Units left", "Value at cost", "Status"],
        "numeric": [4, 5, 6],
        "rows": rows,
        "totals": ["", "", "", "", "", sum(r[5] for r in rows),
                   round(sum(r[6] for r in rows), 2), ""],
    }


def reorder_report():
    """
    What to order, and roughly how much.

    The suggested quantity covers 30 days at the medicine's recent selling
    rate, plus its reorder level as buffer, minus what is already on the
    shelf. Rounded up to the nearest ten, because stock is ordered in
    packs rather than exact units.
    """
    start, end = _range(90)

    recent = dict(db.session.query(
                      Sale.medicine_id,
                      func.sum(Sale.quantity) / 90.0)
                  .filter(Sale.sale_date >= start, Sale.sale_date <= end)
                  .group_by(Sale.medicine_id).all())

    rows = []
    for m in Medicine.query.order_by(Medicine.name).all():
        stock = m.current_stock
        if not m.is_low_stock and stock > 0:
            continue

        daily = float(recent.get(m.medicine_id, 0) or 0)
        target = daily * 30 + m.reorder_level
        suggested = max(0, target - stock)
        suggested = int(-(-suggested // 10) * 10)      # round up to tens

        days_left = round(stock / daily, 1) if daily > 0 else None

        rows.append([m.medicine_code, m.name, stock, m.reorder_level,
                     round(daily, 1),
                     days_left if days_left is not None else "—",
                     suggested, round(suggested * m.unit_price * 0.7, 2)])

    rows.sort(key=lambda r: (r[5] if isinstance(r[5], float) else 999))

    return {
        "title": "Reorder suggestions",
        "subtitle": ("Medicines at or below reorder level, with a suggested "
                     "quantity covering 30 days"),
        "columns": ["Code", "Medicine", "In stock", "Reorder at",
                    "Sold per day", "Days of cover", "Suggested order",
                    "Approx cost"],
        "numeric": [2, 3, 4, 5, 6, 7],
        "rows": rows,
        "totals": ["", "", "", "", "", "", sum(r[6] for r in rows),
                   round(sum(r[7] for r in rows), 2)],
    }


# ------------------------------------------------------------- registry

REPORTS = {
    "stock": {"label": "Stock position", "build": stock_report,
              "periods": False},
    "sales": {"label": "Sales by medicine", "build": sales_report,
              "periods": True},
    "purchases": {"label": "Purchases by supplier", "build": purchase_report,
                  "periods": True},
    "expiry": {"label": "Expiry risk", "build": expiry_report,
               "periods": False},
    "reorder": {"label": "Reorder suggestions", "build": reorder_report,
                "periods": False},
}


def build(key, days=30):
    """Run one report by name."""
    spec = REPORTS[key]
    return spec["build"](days) if spec["periods"] else spec["build"]()


def to_csv(report):
    """
    Turn any report into CSV text.

    Every report shares one shape, so this single function exports them
    all. Values are written through the csv module rather than joined with
    commas, so a medicine name containing a comma cannot break the file.
    """
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([report["title"]])
    writer.writerow([report["subtitle"]])
    writer.writerow([])
    writer.writerow(report["columns"])
    writer.writerows(report["rows"])
    if report.get("totals"):
        writer.writerow(["TOTAL"] + list(report["totals"])[1:])
    return buffer.getvalue()