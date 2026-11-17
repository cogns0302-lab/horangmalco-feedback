"""피드백 완성본: 크루가 다듬어 고객에게 보낸 최종 파일을 모아 두고, 구글 드라이브의 학교급 폴더에 자동으로 올린다.

드라이브 업로드는 관리자 구글 계정으로 배포한 Apps Script 웹 앱(scripts/drive_upload.gs)을 거친다.
서버에 구글 키를 두지 않아도 되고, 파일은 관리자 드라이브 용량에 관리자 소유로 저장된다.
웹 앱 주소는 관리 화면에서 넣는다(설정 키 drive_webhook).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path

import requests

from . import config, db

DIR = config.DATA_DIR / "finals"
EXT = {".hwp", ".hwpx", ".pdf", ".docx", ".doc"}
MIME = {".hwp": "application/x-hwp", ".hwpx": "application/hwp+zip", ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword"}
_lock = threading.Lock()


def path(row) -> Path:
    return DIR / f"{row['id']}{row['ext']}"


def webhook() -> str:
    return db.setting("drive_webhook") or config.env("DRIVE_WEBHOOK_URL")


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]+', " ", s).strip()[:150] or "피드백"


def upload_to_drive(final_id: int) -> None:
    """한 건을 드라이브 학교급 폴더에 올리고 결과를 기록한다(실패해도 예외를 밖으로 내지 않음)."""
    row = db.one("SELECT * FROM finals WHERE id=?", final_id)
    if not row:
        return
    url = webhook()
    if not url:
        db.run("UPDATE finals SET drive_status='off', drive_error='드라이브 연결 주소가 설정되지 않음' WHERE id=?", final_id)
        return
    try:
        data = base64.standard_b64encode(path(row).read_bytes()).decode()
        r = requests.post(url, data=json.dumps({"level": row["level"], "name": safe_name(row["title"]) + row["ext"],
                                                "mime": MIME.get(row["ext"], "application/octet-stream"), "data": data}),
                          headers={"Content-Type": "text/plain;charset=utf-8"}, timeout=120)
        res = r.json() if r.headers.get("content-type", "").startswith("application/json") or r.text.startswith("{") else {}
        if r.status_code == 200 and res.get("ok"):
            db.run("UPDATE finals SET drive_status='done', drive_url=?, drive_error='' WHERE id=?", res.get("url", ""), final_id)
        else:
            err = res.get("error") or f"HTTP {r.status_code}: {r.text[:200]}"
            db.run("UPDATE finals SET drive_status='error', drive_error=? WHERE id=?", str(err)[:500], final_id)
    except Exception as e:
        db.run("UPDATE finals SET drive_status='error', drive_error=? WHERE id=?", f"{type(e).__name__}: {e}"[:500], final_id)


def upload_later(final_id: int) -> None:
    threading.Thread(target=lambda: _guarded(final_id), daemon=True).start()


def _guarded(final_id: int) -> None:
    with _lock:
        upload_to_drive(final_id)


def retry_pending() -> int:
    """연결 주소를 새로 넣었거나 일시 오류가 났던 건을 다시 올린다."""
    rows = db.q("SELECT id FROM finals WHERE drive_status IN ('pending','error','off') ORDER BY id")
    for r in rows:
        _guarded(r["id"])
    return len(rows)


def add_to_knowledge(row) -> None:
    """한글 완성본은 텍스트로 뽑아 지난 피드백 예시에 더한다 → 다음 AI 피드백의 말투·판정 기준이 된다."""
    if row["ext"] not in (".hwp", ".hwpx"):
        return
    try:
        from .pipeline import hwp_text
        p = str(path(row))
        text = hwp_text.hwpx_text(p) if row["ext"] == ".hwpx" else hwp_text.hwp5_text(p)
        if len(text) < 1500:
            return
        d = config.KNOWLEDGE_DIR / "examples" / row["level"]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"완성본 {row['id']:04d} - {safe_name(row['title'])}.txt").write_text(text, encoding="utf-8")
    except Exception:
        pass


def background_retry() -> None:
    """서버가 켜져 있는 동안 1시간마다 못 올린 건을 다시 시도한다."""
    def loop():
        while True:
            time.sleep(3600)
            try:
                retry_pending()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True, name="drive-retry").start()
