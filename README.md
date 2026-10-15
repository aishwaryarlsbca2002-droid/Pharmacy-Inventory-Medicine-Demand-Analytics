# Pharmacy Inventory & Medicine Demand Analytics

MCA final-year project — a web application that records pharmacy purchases and
sales, derives stock and batch-level expiry exposure from that history, and
forecasts future demand using machine learning.

**Aishwarya Patil** · USN P15AW25S126023
Department of MCA, K.L.E. Society's Raja Lakhamagouda Science Institute
(Autonomous), Belgaum
Guide: Pranati Ma'am

---

## What it does

- **Medicine and supplier management** with full CRUD and referential protection
- **Purchase entry** capturing batch numbers and expiry dates per delivery
- **Sales entry** with a stock check applied per medicine across the whole receipt
- **Inventory dashboard** leading with low stock and expiring batches
- **Expiry alerts** at batch level, valued at cost
- **CSV bulk import** with full validation and safe filename handling
- **Sales analytics** with interactive charts served from a JSON endpoint
- **Demand prediction** using a trained Random Forest model
- **Forecast history** comparing past predictions against actual sales
- **Reports** — five printable reports, each exportable as CSV

## Technology

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Flask 3.1, Flask-SQLAlchemy |
| Database | SQLite (7 normalised tables) |
| Frontend | HTML5, CSS3, JavaScript, Jinja2, Chart.js |
| Analytics | Pandas, NumPy, Matplotlib |
| Machine learning | scikit-learn, joblib |

## Model results

Three regression models were compared using a time-based split that holds back
the most recent 180 days.

| Model | MAE | RMSE | R² |
|---|---|---|---|
| **Random Forest** | **3.649** | **5.247** | **0.822** |
| Gradient Boosting | 3.685 | 5.278 | 0.820 |
| Linear Regression | 4.042 | 5.659 | 0.793 |

`lag_7` and `lag_14` together account for 68% of the model's decisions, which
confirms the autocorrelation finding from the exploratory analysis.

## Running it

```bash
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS or Linux

pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

| Role | Username | Password |
|---|---|---|
| Administrator | admin | admin123 |
| Pharmacist | pharmacist | pharma123 |

The database and trained model are included, so the application runs
immediately. To regenerate them:

```bash
python seed_data.py      # rebuilds pharmacy.db with 2 years of data
python train_model.py    # retrains and saves the model
```

## Project structure
app.py Flask routes
models.py The 7 tables as SQLAlchemy classes
seed_data.py Generates the demonstration dataset
train_model.py Trains, compares and saves the forecasting model
services/ Business logic, one module per domain area
templates/ Jinja2 pages, all extending base.html
static/ CSS, JavaScript and uploads
models/ Saved model and its metrics
notebooks/ Exploratory data analysis


## Note on the dataset

No public dataset links pharmacy sales to batch numbers, expiry dates and
suppliers together, because transaction-level pharmacy data is commercially
sensitive. The dataset here is therefore generated against the project's own
schema, following realistic patterns: winter and spring seasonal peaks, a
strong weekday cycle and slow business growth.

The limitation this creates is stated openly: the model learns the structure
encoded in the generator, so the reported R² demonstrates that the pipeline
works end to end rather than proving the accuracy the model would achieve on a
real pharmacy's sales.