"""
seed_data.py  -  Fills the database with 2 years of realistic data.

Why generated data: no public dataset covers sales, stock, batches,
expiry dates and suppliers together, so we create our own that matches
our table design exactly.

How the sales are built (explain this in your viva):
    demand = base x weekday effect x seasonal effect x trend + noise
  - weekday effect : pharmacies are busier on weekdays than Sundays
  - seasonal effect: cough and cold medicines peak in winter,
                     antihistamines peak in spring
  - trend          : slow growth as the pharmacy gains customers
  - noise          : random day-to-day variation

Stock is kept realistic by a replenishment loop: we walk through time
day by day and place a purchase order whenever stock would run low.
Stock therefore never goes negative.

Run:  python seed_data.py
WARNING: this deletes all existing data and rebuilds it.
"""

import math
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import app
from models import db, User, Medicine, Supplier, Purchase, PurchaseItem, Sale

random.seed(42)                 # same data every run, so results are repeatable

DAYS_OF_HISTORY = 730           # 2 years
TODAY = date.today()
START_DATE = TODAY - timedelta(days=DAYS_OF_HISTORY)

# ---------------------------------------------------------------------
# MASTER DATA
# code, name, category, unit_price, reorder_level, base daily demand, season
# season: "winter" peaks Dec-Feb, "spring" peaks Mar-May, "none" = flat
# ---------------------------------------------------------------------
MEDICINES = [
    ("MED001", "Paracetamol 500mg",    "Analgesic",        2.50, 300, 45, "winter"),
    ("MED002", "Ibuprofen 400mg",      "Analgesic",        4.00, 200, 28, "none"),
    ("MED003", "Aspirin 75mg",         "Analgesic",        1.80, 150, 18, "none"),
    ("MED004", "Diclofenac 50mg",      "Anti-inflammatory",5.50, 120, 15, "none"),
    ("MED005", "Amoxicillin 500mg",    "Antibiotic",      12.00, 180, 22, "winter"),
    ("MED006", "Azithromycin 250mg",   "Antibiotic",      28.00,  80, 10, "winter"),
    ("MED007", "Cetirizine 10mg",      "Antihistamine",    3.20, 200, 26, "spring"),
    ("MED008", "Montelukast 10mg",     "Respiratory",     18.00,  90, 11, "spring"),
    ("MED009", "Salbutamol Inhaler",   "Respiratory",     95.00,  60,  7, "winter"),
    ("MED010", "Metformin 500mg",      "Antidiabetic",     4.50, 250, 32, "none"),
    ("MED011", "Amlodipine 5mg",       "Cardiovascular",   6.00, 220, 29, "none"),
    ("MED012", "Atorvastatin 10mg",    "Cardiovascular",   9.50, 180, 21, "none"),
    ("MED013", "Omeprazole 20mg",      "Gastrointestinal", 7.00, 200, 24, "none"),
    ("MED014", "Ondansetron 4mg",      "Gastrointestinal",11.00,  70,  8, "none"),
    ("MED015", "Vitamin D3 60000IU",   "Supplement",      22.00, 100, 12, "winter"),
]

SUPPLIERS = [
    ("MediSupply Distributors", "9876543210", "orders@medisupply.example",   "Bengaluru"),
    ("HealthPlus Wholesale",    "9123456780", "sales@healthplus.example",    "Mumbai"),
    ("CarePharma Agencies",     "9988776655", "contact@carepharma.example",  "Hyderabad"),
    ("LifeLine Medical Supply", "9001122334", "info@lifeline.example",       "Chennai"),
    ("Apex Drug House",         "9445566778", "apex@apexdrug.example",       "Pune"),
]

# medicines deliberately left low on stock, so the dashboard has something to show
LOW_STOCK_DEMO = {"MED001", "MED007"}
# medicines given an old batch that is expiring soon or already expired
NEAR_EXPIRY_DEMO = {"MED003": 18, "MED010": 25, "MED013": 8}
EXPIRED_DEMO = {"MED004": -12}


# ---------------------------------------------------------------------
# DEMAND MODEL
# ---------------------------------------------------------------------
def weekday_factor(day):
    """Mon-Fri busy, Saturday quieter, Sunday quietest."""
    return {0: 1.10, 1: 1.05, 2: 1.00, 3: 1.00, 4: 1.15, 5: 0.80, 6: 0.45}[day.weekday()]


def seasonal_factor(day, season):
    """A smooth yearly wave. Winter drugs peak in January, spring drugs in April."""
    if season == "none":
        return 1.0
    peak_month = 1 if season == "winter" else 4
    # cosine wave: 1.0 at the peak, lower half a year later
    angle = 2 * math.pi * (day.timetuple().tm_yday - peak_month * 30) / 365
    strength = 0.45 if season == "winter" else 0.35
    return 1 + strength * math.cos(angle)


def trend_factor(day):
    """Business grows about 12% over the two years."""
    elapsed = (day - START_DATE).days / DAYS_OF_HISTORY
    return 1 + 0.12 * elapsed


def daily_demand(day, base, season):
    expected = base * weekday_factor(day) * seasonal_factor(day, season) * trend_factor(day)
    noisy = random.gauss(expected, expected * 0.22)
    return max(0, int(round(noisy)))


# ---------------------------------------------------------------------
# BUILD
# ---------------------------------------------------------------------
def seed():
    with app.app_context():
        print("Dropping old tables and creating fresh ones ...")
        db.drop_all()
        db.create_all()

        # ---- users ----
        db.session.add_all([
            User(username="admin",
                 password_hash=generate_password_hash("admin123"), role="admin"),
            User(username="pharmacist",
                 password_hash=generate_password_hash("pharma123"), role="pharmacist"),
        ])

        # ---- suppliers ----
        suppliers = [Supplier(supplier_name=n, phone=p, email=e, city=c)
                     for n, p, e, c in SUPPLIERS]
        db.session.add_all(suppliers)

        # ---- medicines ----
        medicines = []
        for code, name, cat, price, reorder, base, season in MEDICINES:
            medicines.append(Medicine(medicine_code=code, name=name, category=cat,
                                      unit_price=price, reorder_level=reorder))
        db.session.add_all(medicines)
        db.session.commit()
        print(f"Created {len(medicines)} medicines and {len(suppliers)} suppliers")

        meta = {m.medicine_code: m for m in medicines}
        info = {code: (base, season, reorder)
                for code, _, _, _, reorder, base, season in MEDICINES}

        # ---- walk day by day: sell stock, reorder when low ----
        stock = {code: 0 for code in info}
        batch_counter = {code: 0 for code in info}
        invoice_counter = 1000
        purchases_by_date = {}     # date -> Purchase row (group lines into one invoice)
        sales_rows, item_rows = [], []

        day = START_DATE
        while day <= TODAY:
            for code, (base, season, reorder) in info.items():
                med = meta[code]
                want = daily_demand(day, base, season)

                # --- reorder before selling if stock would not cover the day ---
                if stock[code] - want <= reorder:
                    order_qty = int(base * 60)                 # about 60 days of cover
                    batch_counter[code] += 1
                    # pick or create one invoice for this date
                    if day not in purchases_by_date:
                        invoice_counter += 1
                        p = Purchase(supplier_id=random.choice(suppliers).supplier_id,
                                     purchase_date=day,
                                     invoice_no=f"INV-{invoice_counter}")
                        db.session.add(p)
                        db.session.flush()                     # get purchase_id now
                        purchases_by_date[day] = p
                    purchase = purchases_by_date[day]

                    expiry = day + timedelta(days=random.randint(540, 900))
                    item_rows.append(PurchaseItem(
                        purchase_id=purchase.purchase_id, medicine_id=med.medicine_id,
                        quantity=order_qty,
                        batch_no=f"{code}-B{batch_counter[code]:03d}",
                        expiry_date=expiry,
                        cost=round(med.unit_price * random.uniform(0.60, 0.72), 2)))
                    stock[code] += order_qty

                # --- sell: split the day's demand into 1-3 transactions ---
                remaining = min(want, stock[code])
                n_txn = random.randint(1, 3)
                while remaining > 0 and n_txn > 0:
                    qty = remaining if n_txn == 1 else max(1, remaining // n_txn)
                    sales_rows.append(Sale(medicine_id=med.medicine_id, sale_date=day,
                                           quantity=qty, selling_price=med.unit_price))
                    remaining -= qty
                    stock[code] -= qty
                    n_txn -= 1
            day += timedelta(days=1)

        db.session.add_all(item_rows)
        db.session.add_all(sales_rows)
        db.session.commit()
        print(f"Created {len(sales_rows)} sales rows and {len(item_rows)} purchase lines")

        # ---- plant the demo cases the dashboard needs to show ----
        plant_demo_cases(meta)
        db.session.commit()

        report(meta)


def plant_demo_cases(meta):
    """Adjust a few rows so low-stock and expiry alerts have something to display."""
    # Near-expiry and expired batches.
    # We must pick a batch that STILL HOLDS STOCK, otherwise the alert would
    # point at a batch that was sold out long ago.
    for code, days in {**NEAR_EXPIRY_DEMO, **EXPIRED_DEMO}.items():
        med = meta[code]
        in_stock = med.batches_in_stock()
        if in_stock:
            batch = in_stock[0][0]          # the one closest to expiring
            batch.expiry_date = TODAY + timedelta(days=days)

    # Low stock: shrink the most recent delivery, as if the supplier
    # short-shipped. We do NOT add a fake sale - one huge sale would be an
    # outlier that distorts the demand model we train later.
    for code in LOW_STOCK_DEMO:
        med = meta[code]
        excess = med.current_stock - int(med.reorder_level * 0.6)
        newest = max(med.purchase_items, key=lambda i: i.purchase_item_id)
        if 0 < excess < newest.quantity:
            newest.quantity -= int(excess)


def report(meta):
    print("\n--- Database summary ---")
    for label, model in [("users", User), ("medicines", Medicine),
                         ("suppliers", Supplier), ("purchases", Purchase),
                         ("purchase_items", PurchaseItem), ("sales", Sale)]:
        print(f"  {label:16} {model.query.count()}")

    first = Sale.query.order_by(Sale.sale_date).first()
    last = Sale.query.order_by(Sale.sale_date.desc()).first()
    print(f"\nSales span: {first.sale_date}  to  {last.sale_date}")

    print("\nLOW STOCK (at or below reorder level):")
    for m in Medicine.query.all():
        if m.is_low_stock:
            print(f"  {m.name:22} stock={m.current_stock:5}  reorder={m.reorder_level}")

    print("\nEXPIRY ALERTS (batches still holding stock, 90 days or less):")
    found = False
    for m in Medicine.query.all():
        for item, left in m.batches_in_stock():
            if item.days_to_expiry <= 90:
                found = True
                print(f"  {m.name:22} batch {item.batch_no:14} "
                      f"{item.expiry_date}  {left:5} units  {item.expiry_status}")
    if not found:
        print("   none")

    print("\nTOP 5 MEDICINES BY UNITS SOLD:")
    rows = sorted(Medicine.query.all(), key=lambda m: m.total_sold, reverse=True)[:5]
    for m in rows:
        print(f"  {m.name:22} {m.total_sold:7} units")

    print("\nNegative stock check (must be empty):")
    bad = [m.name for m in Medicine.query.all() if m.current_stock < 0]
    print("  ", bad if bad else "none - all stock levels are valid")


if __name__ == "__main__":
    seed()
    print("\nDone. Run 'python app.py' and refresh the browser.")