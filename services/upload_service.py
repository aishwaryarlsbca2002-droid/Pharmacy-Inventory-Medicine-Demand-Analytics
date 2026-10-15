"""
services/upload_service.py

Bulk import of medicines and sales from a CSV file.

Typing two years of history by hand is impossible, so a pharmacy moving
onto this system needs to import what it already has. That is what this
module is for.

Three things matter when accepting a file from a user, and all three are
handled here:

  the file itself   - only .csv, only up to a size limit, and the name is
                      rewritten with secure_filename() plus a timestamp, so
                      a crafted name like "../../app.py" cannot escape the
                      upload folder or overwrite anything
  the contents      - every row is checked before anything is written
  the outcome       - nothing is saved unless the whole file is valid, so a
                      half-imported file can never leave the data broken
"""

import csv
import io
import os
from datetime import date, datetime

from werkzeug.utils import secure_filename

from models import db, Medicine, Sale

ALLOWED_EXTENSIONS = {".csv"}
MAX_BYTES = 2 * 1024 * 1024          # 2 MB is far more than any class demo
MAX_ROWS = 20000

# What each import expects, shown on the page so the user can match it.
TEMPLATES = {
    "medicines": {
        "label": "Medicines",
        "columns": ["medicine_code", "name", "category", "unit_price",
                    "reorder_level"],
        "example": "MED020,Ranitidine 150mg,Gastrointestinal,5.50,100",
    },
    "sales": {
        "label": "Sales history",
        "columns": ["sale_date", "medicine_code", "quantity", "selling_price"],
        "example": "2026-09-01,MED001,45,2.50",
    },
}


# ------------------------------------------------------------ the file

def check_file(storage):
    """
    Validate the uploaded file before reading a single byte of content.

    Returns (safe_name, raw_bytes, error).
    """
    if storage is None or not storage.filename:
        return None, None, "Choose a CSV file to upload."

    original = storage.filename
    extension = os.path.splitext(original)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        return None, None, "Only .csv files can be uploaded."

    raw = storage.read()
    if len(raw) == 0:
        return None, None, "That file is empty."
    if len(raw) > MAX_BYTES:
        return None, None, (f"That file is {len(raw) // 1024} KB. The limit "
                            f"is {MAX_BYTES // 1024} KB.")

    # secure_filename strips directories and anything unusual, so
    # "../../../app.py" becomes "app.py". The timestamp then makes the
    # name unique, so one upload can never overwrite another.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = f"{stamp}-{secure_filename(original)}"

    return safe_name, raw, None


def store(raw, safe_name, upload_folder):
    """Keep the original file, so an import can be traced back to its source."""
    os.makedirs(upload_folder, exist_ok=True)
    path = os.path.join(upload_folder, safe_name)
    with open(path, "wb") as f:
        f.write(raw)
    return path


def read_rows(raw):
    """Decode the bytes and return (rows, error)."""
    try:
        text = raw.decode("utf-8-sig")       # -sig strips Excel's BOM
    except UnicodeDecodeError:
        return None, ("That file is not plain text. Save it from Excel as "
                      "CSV UTF-8 and try again.")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return None, "That file has no header row."

    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
    rows = list(reader)

    if not rows:
        return None, "That file has a header but no data rows."
    if len(rows) > MAX_ROWS:
        return None, f"That file has {len(rows)} rows. The limit is {MAX_ROWS}."

    return rows, None


def check_columns(rows, kind):
    """Every expected column must be present, in any order."""
    expected = set(TEMPLATES[kind]["columns"])
    found = set(rows[0].keys())
    missing = expected - found
    if missing:
        return (f"These columns are missing: {', '.join(sorted(missing))}. "
                f"Expected: {', '.join(TEMPLATES[kind]['columns'])}.")
    return None


# -------------------------------------------------------- the contents

def validate_medicines(rows):
    """Check every row. Returns (clean_rows, errors)."""
    errors, clean = [], []

    existing = {m.medicine_code for m in Medicine.query.all()}
    seen = set()

    for i, row in enumerate(rows, start=2):        # row 1 is the header
        code = (row.get("medicine_code") or "").strip().upper()
        name = (row.get("name") or "").strip()
        category = (row.get("category") or "").strip()

        if not code or not name:
            errors.append(f"Row {i}: medicine_code and name are required.")
            continue
        if code in existing:
            errors.append(f"Row {i}: {code} is already in the catalogue.")
            continue
        if code in seen:
            errors.append(f"Row {i}: {code} appears twice in this file.")
            continue
        seen.add(code)

        try:
            price = float((row.get("unit_price") or "").strip())
            if price < 0:
                raise ValueError
        except ValueError:
            errors.append(f"Row {i}: unit_price must be a number, "
                          f"got '{row.get('unit_price')}'.")
            continue

        try:
            reorder = int(float((row.get("reorder_level") or "").strip()))
            if reorder < 0:
                raise ValueError
        except ValueError:
            errors.append(f"Row {i}: reorder_level must be a whole number, "
                          f"got '{row.get('reorder_level')}'.")
            continue

        clean.append({"medicine_code": code, "name": name,
                      "category": category or "Other",
                      "unit_price": price, "reorder_level": reorder})

    return clean, errors


def validate_sales(rows):
    """Check every row against the medicine catalogue."""
    errors, clean = [], []
    today = date.today()

    known = {m.medicine_code: m.medicine_id for m in Medicine.query.all()}

    for i, row in enumerate(rows, start=2):
        code = (row.get("medicine_code") or "").strip().upper()
        if code not in known:
            errors.append(f"Row {i}: no medicine with code '{code}'. "
                          "Import the medicines first.")
            continue

        try:
            sale_date = date.fromisoformat((row.get("sale_date") or "").strip())
        except ValueError:
            errors.append(f"Row {i}: sale_date must be yyyy-mm-dd, "
                          f"got '{row.get('sale_date')}'.")
            continue
        if sale_date > today:
            errors.append(f"Row {i}: sale_date {sale_date} is in the future.")
            continue

        try:
            quantity = int(float((row.get("quantity") or "").strip()))
            if quantity <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"Row {i}: quantity must be a positive whole "
                          f"number, got '{row.get('quantity')}'.")
            continue

        try:
            price = float((row.get("selling_price") or "").strip())
            if price < 0:
                raise ValueError
        except ValueError:
            errors.append(f"Row {i}: selling_price must be a number, "
                          f"got '{row.get('selling_price')}'.")
            continue

        clean.append({"medicine_id": known[code], "sale_date": sale_date,
                      "quantity": quantity, "selling_price": price})

    return clean, errors


# ---------------------------------------------------------- the import

def import_medicines(clean):
    """Save all rows in one transaction."""
    for row in clean:
        db.session.add(Medicine(**row))
    db.session.commit()
    return len(clean)


def import_sales(clean):
    """Save all rows in one transaction."""
    for row in clean:
        db.session.add(Sale(**row))
    db.session.commit()
    return len(clean)


def template_csv(kind):
    """The blank template a user can download, fill in and upload back."""
    spec = TEMPLATES[kind]
    return ",".join(spec["columns"]) + "\n" + spec["example"] + "\n"