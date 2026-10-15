"""
app.py  -  The Flask application.

This version adds authentication:
  - login and logout
  - passwords checked against a hash, never stored as plain text
  - sessions remember who is logged in
  - role-based access: some pages are admin-only

Run:  python app.py
Login with  admin / admin123   or   pharmacist / pharma123
"""

import os
from datetime import date, datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, jsonify, Response)
from werkzeug.security import check_password_hash, generate_password_hash

from models import (db, User, Medicine, Supplier, Sale, Purchase,
                    PurchaseItem, Forecast)
from services import (medicine_service, supplier_service,
                      purchase_service, sale_service, analytics_service,
                      prediction_service, expiry_service, upload_service,
                      report_service)


BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "replace-with-a-long-random-string-in-production"
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + os.path.join(BASE_DIR, "pharmacy.db"))
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.path.join(BASE_DIR, "static", "uploads")
    db.init_app(app)
    return app


app = create_app()


# =====================================================================
# ACCESS CONTROL
# A decorator is a function that wraps another function. Here it runs a
# check before the page function runs, and redirects if the check fails.
# =====================================================================

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("That page is for administrators only.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    """Makes current_user available inside every template automatically."""
    return {"current_user": {"username": session.get("username"),
                             "role": session.get("role")}}


# =====================================================================
# AUTH ROUTES
# =====================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # server-side validation
        if not username or not password:
            flash("Enter both username and password.", "danger")
            return render_template("login.html")

        user = User.query.filter_by(username=username).first()

        # check_password_hash compares the typed password with the stored hash.
        # The real password is never stored anywhere.
        if user is None or not check_password_hash(user.password_hash, password):
            # the same message for both cases, so we don't reveal which
            # usernames exist
            flash("Invalid username or password.", "danger")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user.user_id
        session["username"] = user.username
        session["role"] = user.role
        flash(f"Welcome back, {user.username}.", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = []
        if len(username) < 3:
            errors.append("Username must be at least 3 characters.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("The two passwords do not match.")
        if User.query.filter_by(username=username).first():
            errors.append("That username is already taken.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", username=username)

        # new accounts are always pharmacists; only an admin can create admins
        user = User(username=username,
                    password_hash=generate_password_hash(password),
                    role="pharmacist")
        db.session.add(user)
        db.session.commit()
        flash("Account created. You can log in now.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# =====================================================================
# MAIN PAGES
# =====================================================================

@app.route("/")
@login_required
def dashboard():
    medicines = Medicine.query.all()
    low_stock = [m for m in medicines if m.is_low_stock]

    expiring = []
    for m in medicines:
        for batch, left in m.batches_in_stock():
            if batch.days_to_expiry <= 90:
                expiring.append((m, batch, left))
    expiring.sort(key=lambda row: row[1].expiry_date)

    stats = {
        "medicines": len(medicines),
        "suppliers": Supplier.query.count(),
        "sales": Sale.query.count(),
        "low_stock": len(low_stock),
        "expiring": len(expiring),
        "stock_value": round(sum(m.current_stock * m.unit_price for m in medicines), 2),
    }
    return render_template("dashboard.html", stats=stats,
                           low_stock=low_stock, expiring=expiring[:10])

# =====================================================================
# MASTER DATA: MEDICINE MANAGEMENT  (full CRUD - the viva demo)
# =====================================================================

@app.route("/medicines")
@login_required
def medicines():
    """READ: list medicines, with search and filters."""
    filters = {
        "q": request.args.get("q", "").strip(),
        "category": request.args.get("category", "").strip(),
        "stock": request.args.get("stock", "").strip(),
    }
    rows = medicine_service.search(filters["q"], filters["category"],
                                   filters["stock"])
    return render_template("medicines.html", medicines=rows,
                           total=Medicine.query.count(), filters=filters,
                           categories=medicine_service.CATEGORIES)


@app.route("/medicines/new", methods=["GET", "POST"])
@admin_required
def medicine_new():
    """CREATE: add a medicine."""
    values = {"is_active": True}

    if request.method == "POST":
        values, errors = medicine_service.validate(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            medicine = medicine_service.create(values)
            flash(f"{medicine.name} added.", "success")
            return redirect(url_for("medicines"))

    return render_template("medicine_form.html", values=values,
                           categories=medicine_service.CATEGORIES,
                           heading="Add medicine",
                           subheading="A new item for the catalogue.",
                           submit_label="Add medicine")


@app.route("/medicines/<int:medicine_id>/edit", methods=["GET", "POST"])
@admin_required
def medicine_edit(medicine_id):
    """UPDATE: change an existing medicine."""
    medicine = Medicine.query.get_or_404(medicine_id)

    if request.method == "POST":
        values, errors = medicine_service.validate(request.form, medicine_id)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("medicine_form.html", values=values,
                                   categories=medicine_service.CATEGORIES,
                                   heading="Edit medicine",
                                   subheading=medicine.name,
                                   submit_label="Save changes")
        medicine_service.update(medicine, values)
        flash(f"{medicine.name} updated.", "success")
        return redirect(url_for("medicines"))

    values = {"medicine_code": medicine.medicine_code, "name": medicine.name,
              "category": medicine.category, "unit_price": medicine.unit_price,
              "reorder_level": medicine.reorder_level,
              "is_active": medicine.is_active}
    return render_template("medicine_form.html", values=values,
                           categories=medicine_service.CATEGORIES,
                           heading="Edit medicine", subheading=medicine.name,
                           submit_label="Save changes")


@app.route("/medicines/<int:medicine_id>/delete", methods=["POST"])
@admin_required
def medicine_delete(medicine_id):
    """
    DELETE: remove a medicine, but only if it has no history.

    POST only. A delete must never be reachable by a plain link, because
    anything that visits links (a browser prefetch, a crawler) could then
    destroy data.
    """
    medicine = Medicine.query.get_or_404(medicine_id)
    allowed, reason = medicine_service.can_delete(medicine)

    if not allowed:
        flash(reason, "warning")
    else:
        name = medicine.name
        medicine_service.delete(medicine)
        flash(f"{name} deleted.", "success")

    return redirect(url_for("medicines"))
# =====================================================================
# MASTER DATA: SUPPLIER MANAGEMENT
# =====================================================================

@app.route("/suppliers")
@login_required
def suppliers():
    """READ: list suppliers with their purchase history summary."""
    q = request.args.get("q", "").strip()
    found = supplier_service.search(q)
    rows = [(s, supplier_service.summary(s)) for s in found]
    return render_template("suppliers.html", rows=rows,
                           total=Supplier.query.count(), q=q)


@app.route("/suppliers/new", methods=["GET", "POST"])
@admin_required
def supplier_new():
    """CREATE: add a supplier."""
    values = {}

    if request.method == "POST":
        values, errors = supplier_service.validate(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            supplier = supplier_service.create(values)
            flash(f"{supplier.supplier_name} added.", "success")
            return redirect(url_for("suppliers"))

    return render_template("supplier_form.html", values=values,
                           heading="Add supplier",
                           subheading="A company you buy stock from.",
                           submit_label="Add supplier")


@app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
@admin_required
def supplier_edit(supplier_id):
    """UPDATE: change a supplier's details."""
    supplier = Supplier.query.get_or_404(supplier_id)

    if request.method == "POST":
        values, errors = supplier_service.validate(request.form, supplier_id)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("supplier_form.html", values=values,
                                   heading="Edit supplier",
                                   subheading=supplier.supplier_name,
                                   submit_label="Save changes")
        supplier_service.update(supplier, values)
        flash(f"{supplier.supplier_name} updated.", "success")
        return redirect(url_for("suppliers"))

    values = {"supplier_name": supplier.supplier_name, "phone": supplier.phone,
              "email": supplier.email, "city": supplier.city}
    return render_template("supplier_form.html", values=values,
                           heading="Edit supplier",
                           subheading=supplier.supplier_name,
                           submit_label="Save changes")


@app.route("/suppliers/<int:supplier_id>/delete", methods=["POST"])
@admin_required
def supplier_delete(supplier_id):
    """DELETE: only allowed when the supplier has sent no invoices."""
    supplier = Supplier.query.get_or_404(supplier_id)
    allowed, reason = supplier_service.can_delete(supplier)

    if not allowed:
        flash(reason, "warning")
    else:
        name = supplier.supplier_name
        supplier_service.delete(supplier)
        flash(f"{name} deleted.", "success")

    return redirect(url_for("suppliers"))

# =====================================================================
# DATA ENTRY: PURCHASE ENTRY (goods received)
# =====================================================================

@app.route("/purchases")
@login_required
def purchases():
    """READ: list deliveries. Each row expands to show its batches."""
    supplier_id = request.args.get("supplier_id", type=int)
    batch_query = request.args.get("batch", "").strip()
    rows = purchase_service.recent(supplier_id=supplier_id,
                                   batch_query=batch_query)
    return render_template("purchases.html", rows=rows,
                           suppliers=Supplier.query.order_by(
                               Supplier.supplier_name).all(),
                           selected_supplier=supplier_id,
                           batch_query=batch_query,
                           today_date=date.today())


@app.route("/purchases/new", methods=["GET", "POST"])
@login_required
def purchase_new():
    """
    CREATE: record one delivery.

    Pharmacists do this, not just admins: receiving stock is counter work.
    """
    header = {}
    lines = []

    if request.method == "POST":
        header, lines, errors, warnings = purchase_service.validate(request.form)

        for w in warnings:
            flash(w, "warning")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            purchase = purchase_service.create(header, lines)
            units = sum(l["quantity"] for l in lines)
            flash(f"Invoice {purchase.invoice_no} recorded: "
                  f"{len(lines)} lines, {units} units added to stock.", "success")
            return redirect(url_for("purchases"))

    return render_template(
        "purchase_form.html",
        header=header, lines=lines,
        suppliers=Supplier.query.order_by(Supplier.supplier_name).all(),
        medicines=Medicine.query.filter_by(is_active=True)
                          .order_by(Medicine.name).all(),
        today=date.today().isoformat(),
        default_expiry=purchase_service.suggested_expiry())

@app.route("/purchases/<int:purchase_id>/delete", methods=["POST"])
@admin_required
def purchase_delete(purchase_id):
    """
    DELETE: remove a delivery entered by mistake.

    Allowed only for deliveries entered today whose stock has not been
    sold yet. Anything older is corrected with a stock adjustment, so the
    original invoice stays on record.
    """
    purchase = Purchase.query.get_or_404(purchase_id)
    allowed, reason = purchase_service.can_delete(purchase)

    if not allowed:
        flash(reason, "warning")
    else:
        invoice_no = purchase.invoice_no
        units = sum(i.quantity for i in purchase.items)
        purchase_service.delete(purchase)
        flash(f"Invoice {invoice_no} removed. {units} units taken back "
              "out of stock.", "success")

    return redirect(url_for("purchases"))

# =====================================================================
# DATA ENTRY: SALES ENTRY (dispensing at the counter)
# =====================================================================

@app.route("/sales")
@login_required
def sales():
    """READ: list sales, with today's totals."""
    medicine_id = request.args.get("medicine_id", type=int)
    on_date_raw = request.args.get("on_date", "").strip()
    on_date = sale_service.parse_date(on_date_raw)

    rows = sale_service.recent(medicine_id=medicine_id, on_date=on_date)
    return render_template(
        "sales.html", rows=rows,
        today=sale_service.day_summary(date.today()),
        medicines=Medicine.query.order_by(Medicine.name).all(),
        selected_medicine=medicine_id,
        on_date_raw=on_date_raw,
        today_date=date.today().isoformat())


@app.route("/sales/new", methods=["GET", "POST"])
@login_required
def sale_new():
    """
    CREATE: record one sale.

    Unlike a purchase, a sale is refused when stock is insufficient.
    You cannot dispense what the shelf does not hold.
    """
    sale_date, lines = None, []

    if request.method == "POST":
        sale_date, lines, errors, warnings = sale_service.validate(request.form)

        for w in warnings:
            flash(w, "warning")

        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            saved = sale_service.create(sale_date, lines)
            units = sum(l["quantity"] for l in lines)
            total = sum(l["quantity"] * l["selling_price"] for l in lines)
            flash(f"Sale recorded: {len(saved)} lines, {units} units, "
                  f"{total:,.2f} total.", "success")
            return redirect(url_for("sales"))

    return render_template(
        "sale_form.html",
        sale_date=sale_date.isoformat() if sale_date else None,
        lines=lines,
        medicines=sale_service.sellable_medicines(),
        today=date.today().isoformat())

# =====================================================================
# ANALYTICS
# =====================================================================

@app.route("/analytics")
@login_required
def analytics():
    """The page itself. The numbers arrive separately, from the API below."""
    return render_template("analytics.html")


@app.route("/api/analytics")
@login_required
def api_analytics():
    """
    A REST-style endpoint returning JSON rather than HTML.

    The page calls this with fetch() when you change the period, so only
    the figures travel over the network and the page never reloads.
    """
    days = request.args.get("days", default=365, type=int)
    days = max(1, min(days, 3650))          # never trust a value from the URL

    start, end = analytics_service.resolve_range(days=days)
    return jsonify(analytics_service.build_payload(start, end))

# =====================================================================
# DEMAND PREDICTION
# =====================================================================

def _run_forecast(medicine_id, days):
    """Shared by the predict page and the save action."""
    medicine = Medicine.query.get_or_404(medicine_id)
    days = max(1, min(days, prediction_service.MAX_DAYS))
    rows, error = prediction_service.forecast(medicine, days)
    return medicine, days, rows, error


@app.route("/predict", methods=["GET", "POST"])
@login_required
def predict():
    """
    Load the saved model and predict demand for one medicine.

    The model is trained offline by train_model.py. This route only loads
    it and feeds it features, which is why a prediction is fast enough to
    run inside a web request.
    """
    chosen = rows = summary = chart_data = None
    days = 7

    if request.method == "POST":
        medicine_id = request.form.get("medicine_id", type=int)
        days = request.form.get("days", default=7, type=int)

        if not medicine_id:
            flash("Choose a medicine to predict.", "danger")
        else:
            chosen, days, rows, error = _run_forecast(medicine_id, days)
            if error:
                flash(error, "danger")
                rows = None
            else:
                summary = prediction_service.summarise(rows, chosen)
                actuals = prediction_service.recent_actuals(chosen, 21)
                chart_data = {
                    "labels": ([a["date"].strftime("%d %b") for a in actuals] +
                               [r["date"].strftime("%d %b") for r in rows]),
                    "actual": [a["quantity"] for a in actuals],
                    "forecast": [r["quantity"] for r in rows],
                }

    return render_template("predict.html",
                           info=prediction_service.model_info(),
                           medicines=prediction_service.forecastable_medicines(),
                           chosen=chosen, days=days, rows=rows,
                           summary=summary, chart_data=chart_data)


@app.route("/predict/save", methods=["POST"])
@login_required
def predict_save():
    """
    Store a prediction with its timestamp.

    The project requires prediction output to be kept for history and
    audit, so each run is saved rather than overwriting the last.
    """
    medicine_id = request.form.get("medicine_id", type=int)
    days = request.form.get("days", default=7, type=int)

    medicine, days, rows, error = _run_forecast(medicine_id, days)
    if error:
        flash(error, "danger")
        return redirect(url_for("predict"))

    saved = prediction_service.save_forecast(medicine, rows,
                                             session.get("user_id"))
    flash(f"Forecast saved: {len(saved)} days for {medicine.name}.", "success")
    return redirect(url_for("forecast_history"))


@app.route("/forecasts")
@login_required
def forecast_history():
    """
    READ: saved forecasts, grouped into the runs they were made in,
    each compared against what actually sold.
    """
    medicine_id = request.args.get("medicine_id", type=int)

    q = Forecast.query
    if medicine_id:
        q = q.filter_by(medicine_id=medicine_id)
    all_rows = q.order_by(Forecast.created_at.desc(),
                          Forecast.forecast_date).all()

    # Rows saved together share a created_at and a medicine, so that pair
    # identifies one prediction run.
    grouped = {}
    for row in all_rows:
        key = (row.created_at, row.medicine_id)
        grouped.setdefault(key, []).append(row)

    # what actually sold on each predicted day, for comparison
    actual_sales = {}
    for (created_at, med_id), rows in grouped.items():
        for row in rows:
            k = (med_id, row.forecast_date)
            if k not in actual_sales:
                total = (db.session.query(db.func.sum(Sale.quantity))
                         .filter(Sale.medicine_id == med_id,
                                 Sale.sale_date == row.forecast_date)
                         .scalar())
                actual_sales[k] = total

    runs = []
    for (created_at, med_id), rows in grouped.items():
        errors = []
        for row in rows:
            row.actual = actual_sales.get((med_id, row.forecast_date))
            if row.actual is None:
                row.error = None
            else:
                row.error = row.predicted_quantity - row.actual
                errors.append(abs(row.error))

        runs.append({
            "created_at": created_at,
            "medicine": rows[0].medicine,
            "model_name": rows[0].model_name,
            "rows": rows,
            "first_date": min(r.forecast_date for r in rows),
            "last_date": max(r.forecast_date for r in rows),
            "total": round(sum(r.predicted_quantity for r in rows), 1),
            "scored": len(errors),
            "mae": round(sum(errors) / len(errors), 1) if errors else None,
        })

    runs.sort(key=lambda r: r["created_at"], reverse=True)

    return render_template("forecast_history.html", runs=runs,
                           medicines=Medicine.query.order_by(Medicine.name).all(),
                           selected_medicine=medicine_id)

# =====================================================================
# EXPIRY ALERTS
# =====================================================================

@app.route("/expiry")
@login_required
def expiry():
    """
    Batches approaching or past their expiry date.

    Only batches that still hold stock are listed. A batch that expired
    last year but sold out months ago needs no action, so including it
    would be noise.
    """
    window = request.args.get("window", default=90, type=int)
    category = request.args.get("category", "").strip() or None
    status = request.args.get("status", "").strip() or None

    # the dropdown uses 0 to mean "no limit"
    window_days = None if window == 0 else window

    rows = expiry_service.collect(window_days=window_days,
                                  category=category, status=status)

    # the headline bands always describe the whole picture, not the
    # filtered view, so the counts do not change as you filter
    all_rows = expiry_service.collect(window_days=expiry_service.WARNING_DAYS)

    return render_template("expiry.html",
                           rows=rows,
                           summary=expiry_service.summary(all_rows),
                           suppliers=expiry_service.by_supplier(all_rows),
                           categories=expiry_service.categories(),
                           window=window,
                           selected_category=category or "",
                           selected_status=status or "")

# =====================================================================
# DATA UPLOAD (bulk import from CSV)
# =====================================================================

@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    """
    Import medicines or sales history from a CSV file.

    Admin only: a bad import can corrupt the whole dataset, so this is not
    counter work. The file is validated in three stages (the file itself,
    then its columns, then every row) and only written if all three pass.
    """
    result = None
    kind = request.form.get("kind", "medicines")

    if request.method == "POST":
        if kind not in upload_service.TEMPLATES:
            flash("Choose what you are importing.", "danger")
            return redirect(url_for("upload"))

        storage = request.files.get("file")

        # stage 1: the file
        safe_name, raw, error = upload_service.check_file(storage)
        if error:
            flash(error, "danger")
            return render_template("upload.html", result=None, kind=kind,
                                   **_upload_context())

        # stage 2: the structure
        rows, error = upload_service.read_rows(raw)
        if error is None:
            error = upload_service.check_columns(rows, kind)
        if error:
            flash(error, "danger")
            return render_template("upload.html", result=None, kind=kind,
                                   **_upload_context())

        # keep the original file, so an import can be traced to its source
        upload_service.store(raw, safe_name, app.config["UPLOAD_FOLDER"])

        # stage 3: every row
        if kind == "medicines":
            clean, errors = upload_service.validate_medicines(rows)
        else:
            clean, errors = upload_service.validate_sales(rows)

        label = upload_service.TEMPLATES[kind]["label"]

        if errors:
            # all or nothing: one bad row stops the whole file
            result = {"filename": storage.filename, "safe_name": safe_name,
                      "label": label, "saved": 0, "errors": errors[:20],
                      "error_count": len(errors)}
        else:
            if kind == "medicines":
                saved = upload_service.import_medicines(clean)
            else:
                saved = upload_service.import_sales(clean)
            result = {"filename": storage.filename, "safe_name": safe_name,
                      "label": label, "saved": saved, "errors": [],
                      "error_count": 0}
            flash(f"{saved} rows imported from {storage.filename}.", "success")

    return render_template("upload.html", result=result, kind=kind,
                           **_upload_context())


def _upload_context():
    return {
        "templates": upload_service.TEMPLATES,
        "max_kb": upload_service.MAX_BYTES // 1024,
        "max_rows": upload_service.MAX_ROWS,
    }


@app.route("/upload/template/<kind>")
@admin_required
def upload_template(kind):
    """Download a blank CSV with the right header row."""
    if kind not in upload_service.TEMPLATES:
        flash("Unknown template.", "danger")
        return redirect(url_for("upload"))

    return Response(
        upload_service.template_csv(kind),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename={kind}_template.csv"})

# =====================================================================
# REPORTS
# =====================================================================

@app.route("/reports")
@login_required
def reports():
    """
    Printable summaries. Analytics is for looking at on screen; reports
    are for taking away, so each one can be printed or exported.
    """
    key = request.args.get("key", "stock")
    if key not in report_service.REPORTS:
        key = "stock"
    days = request.args.get("days", default=30, type=int)
    days = max(1, min(days, 3650))

    return render_template("reports.html",
                           reports=report_service.REPORTS,
                           key=key, days=days,
                           report=report_service.build(key, days),
                           generated=datetime.now().strftime("%d %b %Y, %H:%M"))


@app.route("/reports/export")
@login_required
def report_export():
    """
    Download the current report as CSV.

    Every report shares one shape, so one export route serves all of them
    rather than needing a separate download function for each.
    """
    key = request.args.get("key", "stock")
    if key not in report_service.REPORTS:
        flash("Unknown report.", "danger")
        return redirect(url_for("reports"))
    days = request.args.get("days", default=30, type=int)
    days = max(1, min(days, 3650))

    report = report_service.build(key, days)
    stamp = date.today().isoformat()

    return Response(
        report_service.to_csv(report),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename={key}_report_{stamp}.csv"})

@app.route("/admin-only")
@admin_required
def admin_only():
    """Admin-only page. Proves role-based access works."""
    users = User.query.order_by(User.user_id).all()
    return render_template("admin_only.html", users=users)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)