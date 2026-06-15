"""
azure_sql.py — Azure SQL Database integration
Handles batch records, match results, and audit history.
"""

import os
import uuid
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict

import pyodbc

logger = logging.getLogger(__name__)


def get_connection():
    """Return a pyodbc connection to Azure SQL."""
    conn_str = (
        f"DRIVER={os.getenv('AZURE_SQL_DRIVER', '{ODBC Driver 18 for SQL Server}')};"
        f"SERVER={os.getenv('AZURE_SQL_SERVER')};"
        f"DATABASE={os.getenv('AZURE_SQL_DATABASE')};"
        f"UID={os.getenv('AZURE_SQL_USERNAME')};"
        f"PWD={os.getenv('AZURE_SQL_PASSWORD')};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    return pyodbc.connect(conn_str)


def init_db():
    """Create tables if they don't exist."""
    ddl = """
    IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='batches' AND xtype='U')
    CREATE TABLE batches (
        batch_id        NVARCHAR(64)  PRIMARY KEY,
        created_at      DATETIME2     DEFAULT GETUTCDATE(),
        total_pairs     INT           DEFAULT 0,
        processed       INT           DEFAULT 0,
        matched         INT           DEFAULT 0,
        mismatched      INT           DEFAULT 0,
        errors          INT           DEFAULT 0,
        status          NVARCHAR(32)  DEFAULT 'pending',
        result_blob     NVARCHAR(512) NULL
    );

    IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='match_results' AND xtype='U')
    CREATE TABLE match_results (
        result_id           NVARCHAR(64)  PRIMARY KEY,
        batch_id            NVARCHAR(64)  NOT NULL,
        pair_index          INT           NOT NULL,
        application_blob    NVARCHAR(512) NOT NULL,
        label_blob          NVARCHAR(512) NOT NULL,
        overall_status      NVARCHAR(16)  NOT NULL,
        brand_name          NVARCHAR(8)   NULL,
        class_type          NVARCHAR(8)   NULL,
        alcohol_content     NVARCHAR(8)   NULL,
        net_contents        NVARCHAR(8)   NULL,
        producer_address    NVARCHAR(8)   NULL,
        country_of_origin   NVARCHAR(8)   NULL,
        health_warning      NVARCHAR(8)   NULL,
        discrepancies       NVARCHAR(MAX) NULL,
        confidence_score    FLOAT         NULL,
        processed_at        DATETIME2     DEFAULT GETUTCDATE(),
        FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
    );
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        for stmt in ddl.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cursor.execute(stmt)
        conn.commit()
        conn.close()
        logger.info("Database initialized.")
    except Exception as e:
        logger.error(f"DB init error: {e}")
        raise


def create_batch(batch_id: str, total_pairs: int) -> bool:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO batches (batch_id, total_pairs, status) VALUES (?, ?, 'processing')",
            (batch_id, total_pairs)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"create_batch error: {e}")
        return False


def insert_match_result(batch_id: str, pair_index: int, app_blob: str, label_blob: str, result: Dict) -> bool:
    try:
        result_id = str(uuid.uuid4())
        fields = result.get("fields", {})
        discrepancies = json.dumps(result.get("discrepancies", []))
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO match_results
                (result_id, batch_id, pair_index, application_blob, label_blob,
                 overall_status, brand_name, class_type, alcohol_content,
                 net_contents, producer_address, country_of_origin,
                 health_warning, discrepancies, confidence_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            result_id, batch_id, pair_index, app_blob, label_blob,
            result.get("overall_status", "error"),
            fields.get("brand_name"),
            fields.get("class_type"),
            fields.get("alcohol_content"),
            fields.get("net_contents"),
            fields.get("producer_address"),
            fields.get("country_of_origin"),
            fields.get("health_warning"),
            discrepancies,
            result.get("confidence_score", 0.0)
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"insert_match_result error: {e}")
        return False


def update_batch_progress(batch_id: str, processed: int, matched: int, mismatched: int, errors: int, status: str = "processing", result_blob: str = None):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE batches
            SET processed=?, matched=?, mismatched=?, errors=?, status=?, result_blob=?
            WHERE batch_id=?
        """, (processed, matched, mismatched, errors, status, result_blob, batch_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"update_batch_progress error: {e}")


def get_batch(batch_id: str) -> Optional[Dict]:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        cols = [c[0] for c in cursor.description]
        return dict(zip(cols, row))
    except Exception as e:
        logger.error(f"get_batch error: {e}")
        return None


def get_batch_results(batch_id: str) -> List[Dict]:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM match_results WHERE batch_id=? ORDER BY pair_index",
            (batch_id,)
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error(f"get_batch_results error: {e}")
        return []


def get_all_batches() -> List[Dict]:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM batches ORDER BY created_at DESC")
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error(f"get_all_batches error: {e}")
        return []
