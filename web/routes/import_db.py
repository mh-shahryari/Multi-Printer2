import os
import sqlite3
import json
import logging
from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user
from web.auth import admin_required
from core import store
from core.database import db_connection, init_db

log = logging.getLogger("PrinterMonitor")
bp = Blueprint("import_db", __name__)

TEMP_UPLOAD_PATH = "temp_import.db"

@bp.route('/api/import/analyze', methods=['POST'])
@admin_required
def analyze_db():
    if 'file' not in request.files:
        return jsonify({"error": "فایلی ارسال نشده است"}), 400
    
    file = request.files['file']
    if not file.filename.endswith('.db'):
        return jsonify({"error": "فرمت فایل باید .db باشد"}), 400

    file.save(TEMP_UPLOAD_PATH)
    
    try:
        conn = sqlite3.connect(TEMP_UPLOAD_PATH)
        cursor = conn.cursor()
        
        # بررسی جداول موجود
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        
        summary = {}
        
        if 'logs' in tables:
            cursor.execute("SELECT COUNT(*) FROM logs")
            summary['logs_count'] = cursor.fetchone()[0]
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM logs")
            min_ts, max_ts = cursor.fetchone()
            summary['logs_range'] = {"start": min_ts, "end": max_ts}
            
            # استخراج پرینترهای موجود در لاگ
            cursor.execute("SELECT DISTINCT printer_ip, printer_name FROM logs")
            summary['printers_in_logs'] = [{"ip": r[0], "name": r[1]} for r in cursor.fetchall()]

        if 'printer_counters' in tables:
            cursor.execute("SELECT COUNT(*) FROM printer_counters")
            summary['counters_count'] = cursor.fetchone()[0]

        conn.close()
        return jsonify({"status": "ok", "summary": summary})
        
    except Exception as e:
        if os.path.exists(TEMP_UPLOAD_PATH):
            os.remove(TEMP_UPLOAD_PATH)
        return jsonify({"error": f"خطا در تحلیل دیتابیس: {str(e)}"}), 500

@bp.route('/api/import/confirm', methods=['POST'])
@admin_required
def confirm_import():
    if not os.path.exists(TEMP_UPLOAD_PATH):
        return jsonify({"error": "فایل موقت یافت نشد. مجدداً آپلود کنید."}), 400
    
    data = request.get_json() or {}
    filters = data.get("filters", {}) # e.g. { "start_date": "...", "ips": [...] }
    
    try:
        source_conn = sqlite3.connect(TEMP_UPLOAD_PATH)
        source_cursor = source_conn.cursor()
        
        # دریافت ستون‌های جدول مقصد برای مچ کردن
        with db_connection() as target_conn:
            # کپی کردن لاگ‌ها با فیلتر
            query = "SELECT printer_ip, printer_name, timestamp, type, message, pages, color, code, severity, paper_size, username, details FROM logs WHERE 1=1"
            params = []
            
            if filters.get("start_date"):
                query += " AND timestamp >= ?"
                params.append(filters["start_date"])
            if filters.get("end_date"):
                query += " AND timestamp <= ?"
                params.append(filters["end_date"])
            if filters.get("ips"):
                placeholders = ",".join(["?"] * len(filters["ips"]))
                query += f" AND printer_ip IN ({placeholders})"
                params.extend(filters["ips"])
            
            source_cursor.execute(query, params)
            rows = source_cursor.fetchall()
            
            target_cursor = target_conn.cursor()
            imported_count = 0
            for row in rows:
                target_cursor.execute('''
                    INSERT INTO logs (printer_ip, printer_name, timestamp, type, message, 
                                      pages, color, code, severity, paper_size, username, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', row)
                imported_count += 1
            
            target_conn.commit()
            
        source_conn.close()
        os.remove(TEMP_UPLOAD_PATH)
        
        return jsonify({"status": "success", "imported": imported_count})
        
    except Exception as e:
        log.exception("Import failed")
        return jsonify({"error": str(e)}), 500
