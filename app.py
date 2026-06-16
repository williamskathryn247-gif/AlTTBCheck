"""
app.py — Alcohol Label Compliance Checker
Workflow:
  1. Upload application + label together — system assigns REF number
  2. View side-by-side in UI
  3. Run batch compliance check on all ready pairs
  4. Official Submit — store to Azure + download PDF report
"""

import os, uuid, json, logging, threading, random, string, base64, mimetypes
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional

from flask import Flask, request, jsonify, render_template, send_file, Response
try:
    from flask_cors import CORS
except ImportError:
    class CORS:
        def __init__(self, app, **kwargs): pass

from dotenv import load_dotenv
load_dotenv()

from matcher import process_batch, MANDATORY_FIELDS, FIELD_LABELS
from report import generate_excel_report
from pdf_report import generate_pdf_report

# ── Render/Docker: explicitly set Tesseract path ──────────────────────────────
import pytesseract
# Try common Linux paths where Tesseract lives in Docker containers
for _tess_path in ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]:
    if os.path.isfile(_tess_path):
        pytesseract.pytesseract.tesseract_cmd = _tess_path
        break

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
CORS(app)

MAX_BATCH_SIZE     = int(os.getenv("MAX_BATCH_SIZE", 300))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "tiff", "bmp", "gif", "webp", "pdf"}

# ── Azure (optional) ───────────────────────────────────────────────────────────
_AZURE_STORAGE_OK = False
_AZURE_SQL_OK     = False
_blob_service     = None

def _try_init_azure():
    global _AZURE_STORAGE_OK, _AZURE_SQL_OK, _blob_service
    try:
        from azure_storage import AzureBlobService
        conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        if conn and "YOUR" not in conn:
            _blob_service = AzureBlobService()
            _AZURE_STORAGE_OK = True
    except Exception as e:
        logger.info(f"Azure Blob not configured (local mode): {e}")
    try:
        import azure_sql as _db
        if os.getenv("AZURE_SQL_SERVER","") and "your-server" not in os.getenv("AZURE_SQL_SERVER",""):
            _db.init_db()
            _AZURE_SQL_OK = True
    except Exception as e:
        logger.info(f"Azure SQL not configured (local mode): {e}")

_try_init_azure()

# ── In-memory stores ───────────────────────────────────────────────────────────
_applications: Dict[str, Dict] = {}   # ref → record
_lock          = threading.Lock()
_batches:      Dict[str, Dict] = {}
_batch_lock    = threading.Lock()

def allowed_file(fn: str) -> bool:
    return "." in fn and fn.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_ref() -> str:
    year   = datetime.utcnow().year
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"REF-{year}-{suffix}"

def image_to_data_url(image_bytes: bytes, filename: str) -> str:
    """Convert image bytes to a base64 data URL for inline display."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "png"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
            "tiff": "image/tiff", "pdf": "application/pdf"}.get(ext, "image/png")
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:{mime};base64,{b64}"

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "mode": "azure" if (_AZURE_STORAGE_OK and _AZURE_SQL_OK) else "local",
        "azure_storage": _AZURE_STORAGE_OK,
        "azure_sql": _AZURE_SQL_OK,
    })

# ── Step 1: Upload application + label together ────────────────────────────────

@app.route("/api/applications/upload", methods=["POST"])
def upload_pair():
    """
    Upload ONE application form and its matching label together.
    Both must be provided in the same request.
    Returns the assigned REF number.
    """
    app_file   = request.files.get("application")
    label_file = request.files.get("label")

    if not app_file:
        return jsonify({"error": "Application file is required (field: 'application')."}), 400
    if not label_file:
        return jsonify({"error": "Label file is required (field: 'label'). Both must be uploaded together."}), 400
    if not allowed_file(app_file.filename):
        return jsonify({"error": f"Unsupported application file type: {app_file.filename}"}), 400
    if not allowed_file(label_file.filename):
        return jsonify({"error": f"Unsupported label file type: {label_file.filename}"}), 400

    ref         = generate_ref()
    app_bytes   = app_file.read()
    label_bytes = label_file.read()

    record = {
        "ref":              ref,
        "app_filename":     app_file.filename,
        "label_filename":   label_file.filename,
        "app_bytes":        app_bytes,
        "label_bytes":      label_bytes,
        "app_data_url":     image_to_data_url(app_bytes,   app_file.filename),
        "label_data_url":   image_to_data_url(label_bytes, label_file.filename),
        "status":           "ready",
        "uploaded_at":      datetime.utcnow().isoformat(),
        "result":           None,
    }

    with _lock:
        _applications[ref] = record

    if _AZURE_STORAGE_OK and _blob_service:
        try:
            _blob_service.upload_application(app_bytes,   app_file.filename,   ref)
            _blob_service.upload_label(label_bytes, label_file.filename, ref)
        except Exception as e:
            logger.warning(f"Blob upload failed for {ref}: {e}")

    logger.info(f"Pair registered: {ref} | app={app_file.filename} | label={label_file.filename}")
    return jsonify({
        "ref":            ref,
        "app_filename":   app_file.filename,
        "label_filename": label_file.filename,
        "status":         "ready",
        "message":        f"Pair registered as {ref} and ready for compliance check.",
    }), 201

# ── Bulk upload (multiple pairs at once) ───────────────────────────────────────

@app.route("/api/applications/upload-batch", methods=["POST"])
def upload_batch_pairs():
    """
    Upload multiple application+label pairs at once.
    Files must be named: applications[] and labels[] — paired by position.
    """
    app_files   = request.files.getlist("applications[]")
    label_files = request.files.getlist("labels[]")

    if not app_files or not label_files:
        return jsonify({"error": "Provide applications[] and labels[] file lists."}), 400
    if len(app_files) != len(label_files):
        return jsonify({"error": f"{len(app_files)} applications vs {len(label_files)} labels — must match."}), 400
    if len(app_files) > MAX_BATCH_SIZE:
        return jsonify({"error": f"Batch size {len(app_files)} exceeds maximum {MAX_BATCH_SIZE}."}), 400

    created = []
    with _lock:
        for af, lf in zip(app_files, label_files):
            if not allowed_file(af.filename) or not allowed_file(lf.filename):
                continue
            ref         = generate_ref()
            app_bytes   = af.read()
            label_bytes = lf.read()
            _applications[ref] = {
                "ref":            ref,
                "app_filename":   af.filename,
                "label_filename": lf.filename,
                "app_bytes":      app_bytes,
                "label_bytes":    label_bytes,
                "app_data_url":   image_to_data_url(app_bytes,   af.filename),
                "label_data_url": image_to_data_url(label_bytes, lf.filename),
                "status":         "ready",
                "uploaded_at":    datetime.utcnow().isoformat(),
                "result":         None,
            }
            created.append({"ref": ref, "app_filename": af.filename, "label_filename": lf.filename})
            if _AZURE_STORAGE_OK and _blob_service:
                try:
                    _blob_service.upload_application(app_bytes,   af.filename, ref)
                    _blob_service.upload_label(label_bytes, lf.filename, ref)
                except Exception: pass

    logger.info(f"Batch registered: {len(created)} pairs")
    return jsonify({"created": created, "count": len(created)}), 201

# ── List / view applications ───────────────────────────────────────────────────

@app.route("/api/applications")
def list_applications():
    with _lock:
        apps = [
            {
                "ref":            r["ref"],
                "app_filename":   r["app_filename"],
                "label_filename": r["label_filename"],
                "status":         r["status"],
                "uploaded_at":    r["uploaded_at"],
                "result":         r.get("result"),
            }
            for r in _applications.values()
        ]
    apps.sort(key=lambda x: x["uploaded_at"], reverse=True)
    summary = {
        "total":      len(apps),
        "ready":      sum(1 for a in apps if a["status"] == "ready"),
        "processing": sum(1 for a in apps if a["status"] == "processing"),
        "processed":  sum(1 for a in apps if a["status"] == "processed"),
    }
    return jsonify({"applications": apps, "summary": summary})

@app.route("/api/applications/<ref>")
def get_application(ref: str):
    with _lock:
        r = _applications.get(ref)
    if not r:
        return jsonify({"error": f"Reference {ref} not found."}), 404
    return jsonify({k: v for k, v in r.items() if k not in ("app_bytes", "label_bytes")})

@app.route("/api/applications/<ref>/images")
def get_images(ref: str):
    """Return base64 data URLs for both images — used by the side-by-side viewer."""
    with _lock:
        r = _applications.get(ref)
    if not r:
        return jsonify({"error": f"Reference {ref} not found."}), 404
    return jsonify({
        "ref":            ref,
        "app_filename":   r["app_filename"],
        "label_filename": r["label_filename"],
        "app_data_url":   r["app_data_url"],
        "label_data_url": r["label_data_url"],
    })

# ── Run batch ──────────────────────────────────────────────────────────────────

@app.route("/api/batch/run", methods=["POST"])
def run_batch():
    body        = request.get_json(silent=True) or {}
    filter_refs = body.get("refs")

    with _lock:
        ready = [
            r for r in _applications.values()
            if r["status"] == "ready"
            and (filter_refs is None or r["ref"] in filter_refs)
        ]

    if not ready:
        return jsonify({"error": "No ready pairs found."}), 400
    if len(ready) > MAX_BATCH_SIZE:
        return jsonify({"error": f"Batch size {len(ready)} exceeds maximum {MAX_BATCH_SIZE}."}), 400

    batch_id = str(uuid.uuid4())
    pairs = [
        {
            "pair_index":  i,
            "ref":         r["ref"],
            "app_bytes":   r["app_bytes"],
            "label_bytes": r["label_bytes"],
        }
        for i, r in enumerate(ready)
    ]

    # Status stays "ready" until the background thread processes each pair
    with _batch_lock:
        _batches[batch_id] = {
            "total": len(pairs), "processed": 0,
            "matched": 0, "mismatched": 0, "errors": 0,
            "status": "processing", "results": [],
            "report_bytes_xlsx": None, "report_bytes_pdf": None,
            "summary": None, "refs": [p["ref"] for p in pairs],
            "started_at": datetime.utcnow().isoformat(),
            "submitted": False,
        }

    threading.Thread(target=_run_batch, args=(batch_id, pairs), daemon=True).start()
    return jsonify({
        "batch_id": batch_id, "total_pairs": len(pairs),
        "refs": [p["ref"] for p in pairs], "status": "processing",
        "message": f"Running compliance check on {len(pairs)} pair(s).",
    }), 202

def _run_batch(batch_id: str, pairs: List[Dict]):
    matched = mismatched = errors = 0
    results = []

    def on_progress(pair_index: int, result: Dict):
        nonlocal matched, mismatched, errors
        overall = result.get("overall_status", "error")
        if overall == "match":      matched   += 1
        elif overall == "mismatch": mismatched += 1
        else:                       errors    += 1
        result["ref"] = pairs[pair_index]["ref"]

        with _lock:
            if pairs[pair_index]["ref"] in _applications:
                _applications[pairs[pair_index]["ref"]]["status"] = "processed"
                _applications[pairs[pair_index]["ref"]]["result"] = {
                    "overall_status":  overall,
                    "confidence_score": result.get("confidence_score"),
                    "discrepancies":   result.get("discrepancies", []),
                }

        with _batch_lock:
            _batches[batch_id]["processed"]  = pair_index + 1
            _batches[batch_id]["matched"]    = matched
            _batches[batch_id]["mismatched"] = mismatched
            _batches[batch_id]["errors"]     = errors
            _batches[batch_id]["results"].append(result)

    try:
        logger.info(f"Batch {batch_id} — starting OCR on {len(pairs)} pair(s)")
        results = process_batch(pairs, progress_callback=on_progress)
        logger.info(f"Batch {batch_id} — OCR complete, generating reports")

        total    = len(pairs)
        summary  = {
            "total_pairs": total, "matched": matched,
            "mismatched": mismatched, "errors": errors,
            "pass_rate": round(matched / total * 100, 1) if total else 0,
            "completed_at": datetime.utcnow().isoformat(),
        }

        # Generate Excel report
        try:
            xlsx_bytes = generate_excel_report(batch_id, results, summary)
            logger.info(f"Batch {batch_id} — Excel report generated ({len(xlsx_bytes)} bytes)")
        except Exception as e:
            logger.error(f"Batch {batch_id} — Excel report failed: {e}")
            import traceback; logger.error(traceback.format_exc())
            xlsx_bytes = None

        # Generate PDF report
        try:
            pdf_bytes = generate_pdf_report(batch_id, results, summary)
            logger.info(f"Batch {batch_id} — PDF report generated ({len(pdf_bytes)} bytes)")
        except Exception as e:
            logger.error(f"Batch {batch_id} — PDF report failed: {e}")
            import traceback; logger.error(traceback.format_exc())
            pdf_bytes = None

        with _batch_lock:
            _batches[batch_id]["status"]            = "complete"
            _batches[batch_id]["report_bytes_xlsx"] = xlsx_bytes
            _batches[batch_id]["report_bytes_pdf"]  = pdf_bytes
            _batches[batch_id]["summary"]           = summary

        logger.info(f"Batch {batch_id} complete — {matched} matched, {mismatched} mismatched, {errors} errors")

    except Exception as e:
        import traceback
        logger.error(f"Batch {batch_id} FAILED: {e}")
        logger.error(traceback.format_exc())
        with _batch_lock:
            _batches[batch_id]["status"] = "error"
            _batches[batch_id]["error_message"] = str(e)

# ── Batch status & results ─────────────────────────────────────────────────────

@app.route("/api/batch/<batch_id>/debug")
def batch_debug(batch_id: str):
    """Full batch state for debugging — shows error messages and result count."""
    with _batch_lock:
        b = _batches.get(batch_id)
    if not b:
        return jsonify({"error": "Batch not found"}), 404
    return jsonify({
        "batch_id":    batch_id,
        "status":      b["status"],
        "total":       b["total"],
        "processed":   b["processed"],
        "matched":     b["matched"],
        "mismatched":  b["mismatched"],
        "errors":      b["errors"],
        "result_count": len(b.get("results", [])),
        "has_xlsx":    b.get("report_bytes_xlsx") is not None,
        "has_pdf":     b.get("report_bytes_pdf") is not None,
        "error_message": b.get("error_message"),
        "started_at":  b.get("started_at"),
    })

@app.route("/api/batch/<batch_id>/progress")
def batch_progress(batch_id: str):
    with _batch_lock:
        b = _batches.get(batch_id)
    if not b:
        return jsonify({"error": "Batch not found"}), 404
    pct = int(b["processed"] / b["total"] * 100) if b["total"] else 0
    return jsonify({
        "batch_id": batch_id, "total": b["total"], "processed": b["processed"],
        "matched": b["matched"], "mismatched": b["mismatched"],
        "errors": b["errors"], "status": b["status"], "percent": pct,
    })

@app.route("/api/batch/<batch_id>/results")
def batch_results(batch_id: str):
    with _batch_lock:
        b = _batches.get(batch_id)
    if not b:
        return jsonify({"error": "Batch not found"}), 404
    if b["status"] == "processing":
        return jsonify({"status": "processing", "results": []}), 202
    return jsonify({"batch_id": batch_id, "count": len(b["results"]),
                    "results": b["results"], "summary": b["summary"]})

@app.route("/api/batch/<batch_id>/report/xlsx")
def download_xlsx(batch_id: str):
    with _batch_lock:
        b = _batches.get(batch_id)
    if not b or not b.get("report_bytes_xlsx"):
        return jsonify({"error": "Report not ready."}), 404
    return send_file(BytesIO(b["report_bytes_xlsx"]),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name=f"compliance_{batch_id[:8]}.xlsx")

@app.route("/api/batch/<batch_id>/report/pdf")
def download_pdf(batch_id: str):
    with _batch_lock:
        b = _batches.get(batch_id)
    if not b or not b.get("report_bytes_pdf"):
        return jsonify({"error": "Report not ready."}), 404
    return send_file(BytesIO(b["report_bytes_pdf"]),
        mimetype="application/pdf",
        as_attachment=True, download_name=f"compliance_{batch_id[:8]}.pdf")

# ── Official Submit ────────────────────────────────────────────────────────────

@app.route("/api/batch/<batch_id>/submit", methods=["POST"])
def submit_batch(batch_id: str):
    """
    Official submission: store results to Azure (if configured) + return PDF for download.
    Marks the batch as submitted.
    """
    with _batch_lock:
        b = _batches.get(batch_id)
    if not b:
        return jsonify({"error": "Batch not found."}), 404
    if b["status"] != "complete":
        return jsonify({"error": "Batch is not complete yet."}), 400
    if b.get("submitted"):
        return jsonify({"error": "Batch has already been submitted.", "submitted": True}), 409

    errors = []

    # Upload to Azure Blob Storage
    if _AZURE_STORAGE_OK and _blob_service:
        try:
            if b.get("report_bytes_xlsx"):
                _blob_service.upload_result(b["report_bytes_xlsx"], f"compliance_{batch_id[:8]}.xlsx", batch_id)
            if b.get("report_bytes_pdf"):
                _blob_service.upload_result(b["report_bytes_pdf"],  f"compliance_{batch_id[:8]}.pdf",  batch_id)
            logger.info(f"Batch {batch_id} reports uploaded to Azure Blob.")
        except Exception as e:
            errors.append(f"Azure Blob upload failed: {e}")
            logger.error(errors[-1])
    else:
        errors.append("Azure Storage not configured — skipping cloud upload.")

    # Save to Azure SQL
    if _AZURE_SQL_OK:
        try:
            import azure_sql as db
            db.create_batch(batch_id, b["total"])
            for result in b["results"]:
                db.insert_match_result(batch_id, result["pair_index"],
                    f"{result['ref']}/application", f"{result['ref']}/label", result)
            db.update_batch_progress(batch_id, b["total"], b["matched"],
                b["mismatched"], b["errors"], "complete")
            logger.info(f"Batch {batch_id} saved to Azure SQL.")
        except Exception as e:
            errors.append(f"Azure SQL save failed: {e}")
            logger.error(errors[-1])
    else:
        errors.append("Azure SQL not configured — skipping database save.")

    with _batch_lock:
        _batches[batch_id]["submitted"]    = True
        _batches[batch_id]["submitted_at"] = datetime.utcnow().isoformat()

    return jsonify({
        "batch_id":     batch_id,
        "submitted":    True,
        "submitted_at": _batches[batch_id]["submitted_at"],
        "azure_stored": _AZURE_STORAGE_OK and not any("Blob" in e for e in errors),
        "db_saved":     _AZURE_SQL_OK and not any("SQL" in e for e in errors),
        "warnings":     errors,
        "message":      "Submission complete. Download your PDF compliance report.",
    })

@app.route("/api/batches")
def list_batches():
    with _batch_lock:
        batches = [
            {k: v for k, v in b.items() if k not in ("report_bytes_xlsx","report_bytes_pdf","results")}
            for b in _batches.values()
        ]
    batches.sort(key=lambda x: x.get("started_at",""), reverse=True)
    return jsonify({"batches": batches})


@app.route("/api/debug/ocr", methods=["POST"])
def debug_ocr():
    """
    Debug endpoint: upload an image and see exactly what Tesseract reads.
    Also shows what fields were extracted. Useful for diagnosing misreads.
    POST with field name 'image'.
    """
    f = request.files.get("image")
    if not f:
        return jsonify({"error": "Provide an image file in field 'image'."}), 400
    from matcher import run_ocr, extract_all_fields
    image_bytes = f.read()
    raw_text    = run_ocr(image_bytes)
    fields      = extract_all_fields(raw_text)
    return jsonify({
        "filename":        f.filename,
        "ocr_raw_text":    raw_text,
        "extracted_fields": fields,
        "char_count":      len(raw_text),
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Starting — mode: {'azure' if _AZURE_STORAGE_OK else 'local'}")
    app.run(host="0.0.0.0", port=port, debug=False)
