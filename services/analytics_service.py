"""
services/analytics_service.py

The analytics layer: it turns raw sales rows into the numbers the
dashboard charts draw.

Two kinds of analytics live here, in the order your project asks for:

  descriptive  - what happened: revenue, units, best sellers, trends
  diagnostic   - why it happened: which categories drive revenue, which
                 medicines swing hardest between seasons, where units
                 and revenue disagree

Prediction is a separate step and lives in the model, not here.

Every function returns plain Python lists and dicts, so the route can
hand them straight to Chart.js as JSON.
"""

from datetime import date, timedelta

from sqlalchemy import func

from models import db, Sale, Medicine, PurchaseItem


# ---------------------------------------------------------------- range

def resolve_range(days=None, start=None, end=None):
    """
    Work out which dates to report on.

    Defaults to the last 365 days up to the most recent sale, rather than
    today, so the charts are never empty just because nothing sold today.
    """
    last_sale = db.session.query(func.max(Sale.sale_date)).scalar()
    end_date = end or last_sale or date.today()
    start_date = start or (end_date - timedelta(days=(days or 365) - 1))
    return start_date, end_date


def _base_query(start, end):
    return (db.session.query(Sale)
            .filter(Sale.sale_date >= start, Sale.sale_date <= end))


# ----------------------------------------------------------- descriptive

def headline(start, end):
    """The KPI numbers shown above the charts."""
    row = (db.session.query(
                func.coalesce(func.sum(Sale.quantity), 0),
                func.coalesce(func.sum(Sale.quantity * Sale.selling_price), 0),
                func.count(Sale.sale_id),
                func.count(func.distinct(Sale.sale_date)))
           .filter(Sale.sale_date >= start, Sale.sale_date <= end)
           .one())
    units, revenue, transactions, days = row
    days = days or 1

    return {
        "units": int(units),
        "revenue": round(float(revenue), 2),
        "transactions": int(transactions),
        "trading_days": int(days),
        "revenue_per_day": round(float(revenue) / days, 2),
        "units_per_day": round(float(units) / days, 1),
    }


def monthly_trend(start, end):
    """Units and revenue per month. The 'what happened over time' chart."""
    rows = (db.session.query(
                func.strftime("%Y-%m", Sale.sale_date).label("month"),
                func.sum(Sale.quantity),
                func.sum(Sale.quantity * Sale.selling_price))
            .filter(Sale.sale_date >= start, Sale.sale_date <= end)
            .group_by("month").order_by("month").all())

    return {
        "labels": [r[0] for r in rows],
        "units": [int(r[1]) for r in rows],
        "revenue": [round(float(r[2]), 2) for r in rows],
    }


def top_medicines(start, end, limit=8):
    """Best sellers, by units and by revenue side by side."""
    rows = (db.session.query(
                Medicine.name,
                func.sum(Sale.quantity),
                func.sum(Sale.quantity * Sale.selling_price))
            .join(Sale, Sale.medicine_id == Medicine.medicine_id)
            .filter(Sale.sale_date >= start, Sale.sale_date <= end)
            .group_by(Medicine.medicine_id)
            .order_by(func.sum(Sale.quantity).desc())
            .limit(limit).all())

    return {
        "labels": [r[0] for r in rows],
        "units": [int(r[1]) for r in rows],
        "revenue": [round(float(r[2]), 2) for r in rows],
    }


def category_split(start, end):
    """Revenue by category. One of the expected outputs of the project."""
    rows = (db.session.query(
                Medicine.category,
                func.sum(Sale.quantity),
                func.sum(Sale.quantity * Sale.selling_price))
            .join(Sale, Sale.medicine_id == Medicine.medicine_id)
            .filter(Sale.sale_date >= start, Sale.sale_date <= end)
            .group_by(Medicine.category)
            .order_by(func.sum(Sale.quantity * Sale.selling_price).desc())
            .all())

    return {
        "labels": [r[0] for r in rows],
        "units": [int(r[1]) for r in rows],
        "revenue": [round(float(r[2]), 2) for r in rows],
    }


def weekday_pattern(start, end):
    """
    Average units per day of week.

    SQLite's strftime('%w') returns 0 for Sunday, so the result is
    rotated to start the week on Monday, which reads better on a chart.
    """
    rows = (db.session.query(
                func.strftime("%w", Sale.sale_date).label("dow"),
                func.sum(Sale.quantity),
                func.count(func.distinct(Sale.sale_date)))
            .filter(Sale.sale_date >= start, Sale.sale_date <= end)
            .group_by("dow").all())

    totals = {int(r[0]): (int(r[1]), int(r[2])) for r in rows}
    names = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]
    order = [1, 2, 3, 4, 5, 6, 0]          # Monday first, Sunday last

    averages = []
    for dow in order:
        units, days = totals.get(dow, (0, 0))
        averages.append(round(units / days, 1) if days else 0)

    return {"labels": names, "averages": averages}


# ------------------------------------------------------------ diagnostic

def rank_gaps(start, end, limit=5):
    """
    Where units and revenue disagree.

    A medicine ranked high on units but low on revenue is cheap and fast
    moving; the reverse is expensive and slow. Ordering on units alone
    under-stocks the second group, which is the diagnostic insight.
    """
    rows = (db.session.query(
                Medicine.name,
                func.sum(Sale.quantity),
                func.sum(Sale.quantity * Sale.selling_price))
            .join(Sale, Sale.medicine_id == Medicine.medicine_id)
            .filter(Sale.sale_date >= start, Sale.sale_date <= end)
            .group_by(Medicine.medicine_id).all())

    by_units = sorted(rows, key=lambda r: r[1], reverse=True)
    by_revenue = sorted(rows, key=lambda r: r[2], reverse=True)

    unit_rank = {r[0]: i + 1 for i, r in enumerate(by_units)}
    revenue_rank = {r[0]: i + 1 for i, r in enumerate(by_revenue)}

    gaps = []
    for name, units, revenue in rows:
        gaps.append({
            "name": name,
            "units": int(units),
            "revenue": round(float(revenue), 2),
            "unit_rank": unit_rank[name],
            "revenue_rank": revenue_rank[name],
            "gap": unit_rank[name] - revenue_rank[name],
        })

    gaps.sort(key=lambda g: abs(g["gap"]), reverse=True)
    return gaps[:limit]


def stock_health():
    """
    A snapshot of the shelf right now, rather than of past sales.

    Counted here so the analytics page can show the state of inventory
    beside the sales analysis.
    """
    medicines = Medicine.query.all()
    expiring = expired = 0

    for m in medicines:
        for batch, _left in m.batches_in_stock():
            if batch.days_to_expiry < 0:
                expired += 1
            elif batch.days_to_expiry <= 90:
                expiring += 1

    return {
        "medicines": len(medicines),
        "low_stock": sum(1 for m in medicines if m.is_low_stock),
        "out_of_stock": sum(1 for m in medicines if m.current_stock <= 0),
        "expiring_soon": expiring,
        "expired": expired,
        "stock_value": round(sum(m.current_stock * m.unit_price
                                 for m in medicines), 2),
    }


def build_payload(start, end):
    """Everything the analytics page needs, in one dictionary."""
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "headline": headline(start, end),
        "monthly": monthly_trend(start, end),
        "top": top_medicines(start, end),
        "categories": category_split(start, end),
        "weekday": weekday_pattern(start, end),
        "gaps": rank_gaps(start, end),
        "stock": stock_health(),
    }