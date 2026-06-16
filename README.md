# Alcohol Label Compliance Checker

A Flask web application for TTB (Alcohol and Tobacco Tax and Trade Bureau) label compliance checking. Upload application forms and bottle label images in batch — the system uses Tesseract OCR to extract mandatory fields from each, compares them case-sensitively, and generates compliance reports in Excel and PDF format.

---

## What it does

- Accepts batches of up to 300 application form + bottle label image pairs
- Assigns each pair a unique reference number (e.g. `REF-2024-A3F9`)
- Uses Tesseract OCR with OpenCV preprocessing to extract all 7 mandatory TTB fields
- Compares fields case-sensitively — `Blue Ridge Bourbon` ≠ `BLUE RIDGE BOURBON`
- Generates colour-coded Excel reports (3 sheets: Summary, Full Results, Discrepancies)
- Generates PDF compliance reports
- Stores results to Azure Blob Storage and Azure SQL on official submission
- Runs fully locally with no Azure credentials required for testing

---
## Assumptions

- Label generation uses an in-house AI model; no live calls to Anthropic/Claude or other external LLM APIs at runtime.
- No external API requests are required by the app; all processing is performed locally or in our Azure pipeline.
- Document ingestion and processing are performed via Azure services (e.g., Blob Storage + Azure Functions / Azure Cognitive Services) — documents must be uploaded to Azure for processing even if not deployed there yet.
- The app does not send data or telemetry to Anthropic, Claude, or any third-party LLM provider.
- OCR is used to match application data to the image contents; OCR output is compared to application fields to determine matches.
- Generated labels are stored/attached directly to the corresponding application record (metadata or file association).
- Local execution is significantly faster than current deployment; deployed instances (on Render) show slow runtime performance — investigation needed into build/runtime configuration, instance size, and static file handling.
- Example images demonstrating expected behavior are included in the repo for reference and testing.
- Token usage for external LLMs is intentionally avoided to reduce cost and latency.
- Security/privacy: sensitive document contents are processed only within our Azure environment or locally; no third-party LLMs receive un-anonymized data.

---

## Link to view deployed app

https://alttbcheck.onrender.com/

---
## Photos/Images

<img width="1716" height="512" alt="UploadPage" src="https://github.com/user-attachments/assets/25285802-30ae-4976-a4df-b80b0ad071ff" />

<img width="1712" height="412" alt="QueueReview" src="https://github.com/user-attachments/assets/384de65e-2e9f-41c5-b53e-0f542dfae29a" />

<img width="1724" height="933" alt="SideBySideViewLabelApp" src="https://github.com/user-attachments/assets/3337d484-61da-4ce1-92ca-e7cecc496878" />

<img width="1696" height="683" alt="ComplianceReview" src="https://github.com/user-attachments/assets/8504ced5-89ee-4661-9158-f6a99f48bbb6" />

<img width="1719" height="483" alt="Official Submission" src="https://github.com/user-attachments/assets/d0e1de30-8900-4ee3-b996-9e90d9bf00b3" />

<img width="946" height="800" alt="ComplianceReport" src="https://github.com/user-attachments/assets/03478461-72b8-44ee-b42a-0a2563aab5df" />





-See Images folder


## Mandatory TTB fields checked

| Field | Comparison method |
|---|---|
| Brand Name | Case-sensitive exact match |
| Class/Type Designation | Case-sensitive exact match |
| Alcohol Content | Numeric comparison ±0.5% tolerance |
| Net Contents | Volume equivalence (750 mL = 0.75 L) |
| Name & Address of Bottler/Producer | Case-sensitive exact match |
| Country of Origin | Synonym-aware (Mexico = Product of Mexico) |
| Government Health Warning Statement | Keyword presence check |

---

## Project structure

```
alcohol_label_matcher/
├── app.py              — Flask web server, all API routes
├── matcher.py          — Tesseract OCR engine + field extraction + comparison
├── report.py           — Excel report generator (3-sheet workbook)
├── pdf_report.py       — PDF compliance report generator
├── azure_storage.py    — Azure Blob Storage integration (optional)
├── azure_sql.py        — Azure SQL integration (optional)
├── requirements.txt    — Python dependencies
├── .env.example        — Credential template
├── startup.sh          — Azure App Service startup command
├── AZURE_DEPLOY.md     — Full Azure deployment guide
├── templates/
│   └── index.html      — Web UI (4-step workflow)
├── tests/
│   └── test_suite.py   — Full test suite (9 tests, 20 assertions)
└── test_data/          — Synthetic test images for local testing
```

---

## Quick start (macOS)

### 1. Install dependencies

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Tesseract OCR
brew install tesseract

# Install Python 3.12
brew install python@3.12
```

### 2. Set up the project

```bash
cd ~/Downloads/alcohol_label_matcher

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python packages
pip install flask pillow opencv-python-headless pytesseract pandas openpyxl python-dotenv reportlab
```

### 3. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

```
FLASK_SECRET_KEY=any-random-string-here
```

### 4. Run

```bash
python3 app.py
```

Open **http://localhost:5000** in your browser.

---

## How to use the app

The app has a 4-step workflow:

### Step 1 — Upload pairs

- Drop all application form images into the left zone
- Drop all matching label images into the right zone (same count, same order)
- Files are sorted alphabetically before pairing — name them accordingly
- A pairing preview table shows before you register
- Click **Register All Pairs** — each pair gets a unique REF number

### Step 2 — Review queue

- All registered pairs are listed with their REF numbers and status
- Click 👁 on any row to view the application and label side-by-side
- If a pair has been processed, the result and discrepancies appear in the viewer

### Step 3 — Run compliance check

- Click **Run Compliance Check** to process all ready pairs at once
- A live progress bar shows matched / mismatched / errors in real time
- Results appear in a table with colour-coded field dots
- Download the Excel or PDF report while reviewing

### Step 4 — Official submit

- Shows a final summary of the batch
- Click **Submit & Download PDF Report** to:
  - Upload reports to Azure Blob Storage (if configured)
  - Save results to Azure SQL (if configured)
  - Auto-download the signed PDF compliance report

---

## OCR pipeline

```
Image input
    ↓
OpenCV preprocessing
  · Upscale to 1800px minimum (improves Tesseract accuracy significantly)
  · Denoise (fastNlMeansDenoising)
  · CLAHE contrast enhancement
  · Sharpening kernel
  · Otsu binarisation
    ↓
Tesseract OCR  (--oem 3 --psm 6 --dpi 300)
    ↓
Field extraction (regex)
  · brand_name   — "Brand:" or "Brand Name:" prefix, two-line format supported
  · class_type   — matched against known TTB class/type list
  · alcohol_content — patterns: "45% alc/vol", "45% ABV", "90 Proof"
  · net_contents — patterns: "750 mL", "0.75 L", "75 cL", "25.4 fl oz"
  · producer_address — "Bottled by:", "Name & Address of Bottler/Producer:"
  · country_of_origin — known country terms + "Product of X" phrases
  · health_warning — keyword scoring (requires 2+ of 7 TTB keywords)
    ↓
Validation gate  ← KEY: rejects OCR misreads before comparison
  · Net contents: unit lookup table — ml/m1/ML → mL, mb/mi/mg → None (rejected)
  · Alcohol content: value must be 1–99%, otherwise None
  · Implausible volumes (< 25 mL or > 30,000 mL) → None
  · Partial digit matches blocked by lookbehind
    ↓
Case-sensitive comparison
  · Brand, Class, Producer → exact string match (whitespace normalised only)
  · Alcohol % → numeric ±0.5% tolerance
  · Net contents → mL equivalence (750 mL = 0.75 L = 75 cL)
  · Country → synonym-aware ("Mexico" = "Product of Mexico")
  · Health warning → boolean present/absent
    ↓
Result: match | mismatch | missing_application | missing_label | missing_both
```

If the OCR validation gate rejects a value (returns `None`), the field is flagged as missing rather than passing bad data through to comparison. This prevents misreads like `750 mb` from being treated as a valid volume.

---



## Running the test suite

```bash
cd ~/Downloads/alcohol_label_matcher
source venv/bin/activate
python3 tests/test_suite.py
```

All 9 tests run without Azure credentials or an internet connection. Tests cover:

- Image preprocessing (OpenCV)
- Tesseract OCR extraction
- Individual field extractors (regex)
- Field comparison logic (case sensitivity, tolerances, synonyms)
- Full pair comparison on real test images
- Mismatch detection on intentional mismatch images
- Batch processing (4 pairs)
- Excel report generation (3 sheets verified)
- Flask API endpoints (health, queue, progress)

After running, open `test_output/sample_report.xlsx` to inspect the generated Excel report.

---

## Debug OCR

If a field is being misread, use the debug endpoint to see exactly what Tesseract reads from any image:

```bash
curl -X POST http://localhost:5000/api/debug/ocr \
  -F "image=@/path/to/your/label.png"
```

Returns the raw OCR text and all extracted field values so you can diagnose extraction issues.

---

## Azure setup (optional — for production)

Azure Blob Storage and Azure SQL are optional. The app runs fully locally without them — results are stored in memory for the session.

When Azure is configured:
- Application forms and labels are uploaded to Blob Storage on registration
- Compliance results are saved to Azure SQL on official submission
- Excel and PDF reports are uploaded to Blob Storage
- Batch history persists across server restarts

See **AZURE_DEPLOY.md** for full step-by-step Azure CLI commands to provision all resources.

Add these to your `.env` file:

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_SQL_SERVER=your-server.database.windows.net
AZURE_SQL_DATABASE=AlcoholLabelDB
AZURE_SQL_USERNAME=sqladmin
AZURE_SQL_PASSWORD=YourPassword123!
```

---

## Hosting

### Render.com (recommended — free)

1. Push code to GitHub (see below)
2. Go to **render.com** → New → Web Service
3. Connect your GitHub repo
4. Set build command: `pip install -r requirements.txt`
5. Set start command: `gunicorn app:app`
6. Add environment variables from your `.env` file
7. Deploy

Free tier: 750 hours/month (resets monthly — enough to run one app 24/7 indefinitely). App sleeps after 15 minutes of inactivity; first request after sleep takes ~30 seconds.


---

## Sample test images

The `test_data/` folder contains 4 synthetic test images. For more realistic testing, use the `sample_labels_v3.zip` file which contains 12 high-resolution images (300 DPI):

| Pair | Product | Expected result |
|---|---|---|
| 01 | Blue Ridge Straight Bourbon Whisky | ✓ Match |
| 02 | Sonoma Valley Reserve Chardonnay | ✓ Match |
| 03 | Cancun Silver Tequila (import) | ✓ Match |
| 04 | Harbor Light Vodka | ✗ Mismatch — ABV wrong (40%→35%), size wrong (750→1000 mL), health warning missing |
| 05 | Emerald Isle Irish Whiskey | ✗ Mismatch — brand name wrong, class type wrong (Irish→Scotch) |
| 06 | Pacific Coast IPA | ✓ Match |

Upload all 6 application files to the left zone and all 6 label files to the right zone in the same order. The compliance check should return 4 matches and 2 mismatches.

---

## Dependencies

| Package | Purpose |
|---|---|
| flask | Web framework |
| pillow | Image handling |
| opencv-python-headless | Image preprocessing (upscale, denoise, binarise) |
| pytesseract | Tesseract OCR wrapper |
| pandas | Data processing |
| openpyxl | Excel report generation |
| reportlab | PDF report generation |
| python-dotenv | Environment variable loading |
| azure-storage-blob | Azure Blob Storage (optional) |
| azure-identity | Azure authentication (optional) |
| pyodbc | Azure SQL connection (optional) |
| gunicorn | Production WSGI server |

Tesseract OCR must be installed separately:
- macOS: `brew install tesseract`
- Ubuntu: `sudo apt-get install tesseract-ocr`
- Windows: download installer from https://github.com/UB-Mannheim/tesseract/wiki

---

## Known limitations

- OCR accuracy depends on image quality. Images should be at least 300 DPI and have clear, unobscured text. The preprocessing pipeline upscales small images but cannot recover text that is genuinely illegible.
- If a field cannot be extracted with confidence, it returns `None` and is flagged as missing rather than passing a potentially wrong value through to comparison.
- In-memory storage only — uploaded files and results are lost when the server restarts unless Azure Blob and SQL are configured.
- The app currently does not support PDF input files on the label side (image formats only: PNG, JPG, TIFF, BMP, GIF, WebP).
