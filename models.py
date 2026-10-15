"""
models.py  -  The 7 database tables for the Pharmacy Inventory project.

Each class below becomes one table in the database.
Each attribute becomes one column.

Read this file top to bottom: master tables first (users, medicines,
suppliers), then transaction tables (purchases, purchase_items, sales),
then the analytics table (forecasts).
"""

from datetime import date, datetime
from flask_sqlalchemy import SQLAlchemy

# One shared database object. app.py will connect it to the Flask app.
db = SQLAlchemy()


# =====================================================================
# MASTER TABLES  -  things that exist (people, products, companies)
# =====================================================================

class User(db.Model):
    """Who can log in. Roles: 'admin' or 'pharmacist'."""
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="pharmacist")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


class Medicine(db.Model):
    """One row per medicine the pharmacy sells."""
    __tablename__ = "medicines"

    medicine_id = db.Column(db.Integer, primary_key=True)
    medicine_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(60), nullable=False, index=True)  # EDA groups by this
    unit_price = db.Column(db.Float, nullable=False)                 # selling price
    reorder_level = db.Column(db.Integer, nullable=False, default=50)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # .purchase_items / .sales / .forecasts give easy access to related rows
    purchase_items = db.relationship("PurchaseItem", back_populates="medicine")
    sales = db.relationship("Sale", back_populates="medicine")
    forecasts = db.relationship("Forecast", back_populates="medicine")

    # ---- helper properties used by the dashboard and alerts ----
    @property
    def total_purchased(self):
        return sum(item.quantity for item in self.purchase_items)

    @property
    def total_sold(self):
        return sum(s.quantity for s in self.sales)

    @property
    def current_stock(self):
        """Stock = everything bought minus everything sold."""
        return self.total_purchased - self.total_sold

    @property
    def is_low_stock(self):
        return self.current_stock <= self.reorder_level

    def batches_in_stock(self):
        """
        Work out how much is left in each batch.

        Past sales are deducted from batches in the order the batches
        arrived (oldest purchase first), because stock cannot be sold
        before it is received. Batches fully used up are dropped.

        The batches left over are returned sorted by expiry date, so the
        one needing attention first appears first. That ordering is the
        FEFO rule (First Expired, First Out) that pharmacies dispense by.

        Returns a list of (PurchaseItem, quantity_remaining).
        """
        remaining_to_deduct = self.total_sold
        leftover = []
        for item in sorted(self.purchase_items, key=lambda i: i.purchase_item_id):
            used = min(item.quantity, remaining_to_deduct)
            remaining_to_deduct -= used
            left = item.quantity - used
            if left > 0:
                leftover.append((item, left))
        return sorted(leftover, key=lambda pair: pair[0].expiry_date)

    def __repr__(self):
        return f"<Medicine {self.medicine_code} {self.name}>"


class Supplier(db.Model):
    """Companies the pharmacy buys stock from."""
    __tablename__ = "suppliers"

    supplier_id = db.Column(db.Integer, primary_key=True)
    supplier_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    city = db.Column(db.String(60))

    purchases = db.relationship("Purchase", back_populates="supplier")

    def __repr__(self):
        return f"<Supplier {self.supplier_name}>"


# =====================================================================
# TRANSACTION TABLES  -  things that happen (events with a date)
# =====================================================================

class Purchase(db.Model):
    """One invoice received from one supplier. The 'header' of a purchase."""
    __tablename__ = "purchases"

    purchase_id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.supplier_id"),
                            nullable=False)
    purchase_date = db.Column(db.Date, nullable=False, index=True)
    invoice_no = db.Column(db.String(40), unique=True, nullable=False)

    supplier = db.relationship("Supplier", back_populates="purchases")
    items = db.relationship("PurchaseItem", back_populates="purchase",
                            cascade="all, delete-orphan")

    @property
    def total_cost(self):
        return sum(i.quantity * i.cost for i in self.items)

    def __repr__(self):
        return f"<Purchase {self.invoice_no}>"


class PurchaseItem(db.Model):
    """One medicine line inside an invoice. This is where batches live."""
    __tablename__ = "purchase_items"

    purchase_item_id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.purchase_id"),
                            nullable=False)
    medicine_id = db.Column(db.Integer, db.ForeignKey("medicines.medicine_id"),
                            nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    batch_no = db.Column(db.String(40), nullable=False)
    expiry_date = db.Column(db.Date, nullable=False, index=True)  # expiry alerts use this
    cost = db.Column(db.Float, nullable=False)                    # cost per unit

    purchase = db.relationship("Purchase", back_populates="items")
    medicine = db.relationship("Medicine", back_populates="purchase_items")

    @property
    def days_to_expiry(self):
        return (self.expiry_date - date.today()).days

    @property
    def expiry_status(self):
        d = self.days_to_expiry
        if d < 0:
            return "Expired"
        if d <= 30:
            return "Critical"
        if d <= 90:
            return "Warning"
        return "Safe"

    def __repr__(self):
        return f"<PurchaseItem batch={self.batch_no} qty={self.quantity}>"


class Sale(db.Model):
    """One sale transaction. The main input for demand forecasting."""
    __tablename__ = "sales"

    sale_id = db.Column(db.Integer, primary_key=True)
    medicine_id = db.Column(db.Integer, db.ForeignKey("medicines.medicine_id"),
                            nullable=False, index=True)
    sale_date = db.Column(db.Date, nullable=False, index=True)   # Date, not text
    quantity = db.Column(db.Integer, nullable=False)
    selling_price = db.Column(db.Float, nullable=False)          # price at sale time

    medicine = db.relationship("Medicine", back_populates="sales")

    # the two indexes below make the daily-sales query for EDA/ML fast
    __table_args__ = (
        db.Index("ix_sales_medicine_date", "medicine_id", "sale_date"),
    )

    @property
    def revenue(self):
        return self.quantity * self.selling_price

    def __repr__(self):
        return f"<Sale med={self.medicine_id} {self.sale_date} qty={self.quantity}>"


# =====================================================================
# ANALYTICS TABLE  -  model output, kept for history and audit
# =====================================================================

class Forecast(db.Model):
    """One predicted quantity for one medicine on one future date."""
    __tablename__ = "forecasts"

    forecast_id = db.Column(db.Integer, primary_key=True)
    medicine_id = db.Column(db.Integer, db.ForeignKey("medicines.medicine_id"),
                            nullable=False, index=True)
    forecast_date = db.Column(db.Date, nullable=False, index=True)
    predicted_quantity = db.Column(db.Float, nullable=False)

    # audit trail required by the problem statement
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    model_name = db.Column(db.String(60))      # e.g. "Random Forest"
    created_by = db.Column(db.Integer, db.ForeignKey("users.user_id"))

    medicine = db.relationship("Medicine", back_populates="forecasts")

    def __repr__(self):
        return f"<Forecast med={self.medicine_id} {self.forecast_date}>"