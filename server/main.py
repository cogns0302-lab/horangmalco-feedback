"""수업실연·면접 피드백 웹앱 (비공개: 관리자 승인 회원만 사용)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile      # request.form() 이 돌려주는 형(fastapi.UploadFile 은 그 하위 형이라 isinstance 가 거짓)
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, finals, jobs, roster
from .pipeline import knowledge, problembank, stt
from .pipeline.hwpx import REVIEW_MARK

BASE = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="fb_session",
                   max_age=7 * 24 * 3600, same_site="lax", https_only=config.COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
PROBLEM_EXT = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".hwp", ".hwpx", ".txt"}
LEVELS = roster.LEVELS
EXAMINEE_DIR = config.DATA_DIR / "examinees"
# 담당 크루가 고칠 수 있는 칸(나머지는 관리자만)
CREW_EDITABLE = ["round", "upload_date", "video_url", "note", "done_date", "personal"]


# ── 비밀번호 ────────────────────────────────────────────────────────────────
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + h.hex()


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


@app.on_event("startup")
def startup() -> None:
    db.init()
    if config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
        row = db.one("SELECT id FROM users WHERE email=?", config.ADMIN_EMAIL)
        if not row:                                   # 최초 실행 시 관리자 계정 생성
            db.run("INSERT INTO users(email,name,pw_hash,role,status,created_at) VALUES(?,?,?,?,?,?)",
                   config.ADMIN_EMAIL, "관리자", hash_pw(config.ADMIN_PASSWORD), "admin", "approved", time.time())
        else:                                         # 환경변수 ADMIN_PASSWORD 가 바뀌면 관리자 비밀번호를 맞춘다
            cur = db.one("SELECT pw_hash FROM users WHERE id=?", row["id"])
            if not check_pw(config.ADMIN_PASSWORD, cur["pw_hash"]):
                db.run("UPDATE users SET pw_hash=?, role='admin', status='approved' WHERE id=?",
                       hash_pw(config.ADMIN_PASSWORD), row["id"])
    jobs.start()
    finals.DIR.mkdir(parents=True, exist_ok=True)
    finals.background_retry()
    if config.SYNC_ON_START and knowledge_incomplete():
        start_sync()                                  # 첫 배포 또는 문제 쪽 그림이 아직 없을 때: 드라이브에서 받아 온다
    if config.SYNC_ON_START:
        threading.Thread(target=_sync_later, daemon=True, name="knowledge-sync").start()


def _sync_later() -> None:
    """구글 드라이브가 서버의 내려받기를 한동안 막는 일이 있어, 12시간마다 빠진 자료만 다시 받아 본다.
    (이미 받은 파일은 건너뛴다. 드라이브에 새 책·피드백을 넣으면 이때 함께 들어온다.)"""
    while True:
        time.sleep(12 * 3600)
        try:
            start_sync()
        except Exception:
            pass


def knowledge_incomplete() -> bool:
    st = knowledge.status()
    if not any(st["examples"].values()) and not st["books"]:
        return True
    books = config.KNOWLEDGE_DIR / "books"
    return any(problembank.is_lesson_book(f.stem) and problembank.wanted_pages(f)
               and not problembank.page_dir(f.stem).exists() for f in books.glob("*.txt"))


_sync_proc: subprocess.Popen | None = None
SYNC_LOG = config.DATA_DIR / "sync.log"


def start_sync() -> bool:
    global _sync_proc
    if _sync_proc and _sync_proc.poll() is None:
        return False
    log = SYNC_LOG.open("w", encoding="utf-8")
    _sync_proc = subprocess.Popen([sys.executable, str(config.ROOT / "scripts" / "sync_knowledge.py")],
                                  stdout=log, stderr=subprocess.STDOUT, cwd=config.ROOT)
    return True


def sync_state() -> dict:
    running = bool(_sync_proc and _sync_proc.poll() is None)
    tail = SYNC_LOG.read_text(encoding="utf-8", errors="ignore")[-1500:] if SYNC_LOG.exists() else ""
    return {"running": running, "log": tail}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    if not request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ── 세션 / 권한 ─────────────────────────────────────────────────────────────
def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    return db.one("SELECT * FROM users WHERE id=?", uid)      # 매 요청마다 확인 → 차단 즉시 반영


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


@app.exception_handler(Redirect)
async def _redirect(request: Request, exc: Redirect):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "로그인이 필요합니다."}, status_code=401)
    return RedirectResponse(exc.url, status_code=303)


def member(request: Request):
    u = current_user(request)
    if not u:
        raise Redirect("/login")
    if u["status"] != "approved":
        raise Redirect("/pending")
    return u


def admin(request: Request):
    u = member(request)
    if u["role"] != "admin":
        raise HTTPException(403, "관리자만 접근할 수 있습니다.")
    return u


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


def check_csrf(request: Request, token: str | None) -> None:
    token = token or request.headers.get("x-csrf-token", "")
    if not token or not hmac.compare_digest(token, request.session.get("csrf", "")):
        raise HTTPException(403, "요청이 만료되었습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.")


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.update(user=current_user(request), csrf=csrf_token(request))
    return templates.TemplateResponse(request, name, ctx)


def client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


def stt_names() -> dict:
    return {k: v[0] for k, v in stt.PROVIDERS.items()}


# ── 로그인 / 가입 ───────────────────────────────────────────────────────────
@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/login")
def login_form(request: Request):
    return page(request, "login.html")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), csrf: str = Form("")):
    check_csrf(request, csrf)
    email, ip, now = email.strip().lower(), client_ip(request), time.time()
    db.run("DELETE FROM login_attempts WHERE at<?", now - 900)
    fails = db.one("SELECT COUNT(*) c FROM login_attempts WHERE email=? OR ip=?", email, ip)["c"]
    if fails >= 10:
        return page(request, "login.html", error="로그인 시도가 너무 많습니다. 15분 뒤에 다시 시도해 주세요.")
    u = db.one("SELECT * FROM users WHERE email=?", email)
    if not u or not check_pw(password, u["pw_hash"]):
        db.run("INSERT INTO login_attempts(email,ip,at) VALUES(?,?,?)", email, ip, now)
        return page(request, "login.html", error="아이디 또는 비밀번호가 올바르지 않습니다.")
    request.session.clear()
    request.session["uid"] = u["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/signup")
def signup_form(request: Request):
    return page(request, "signup.html", levels=LEVELS, form={})


@app.post("/signup")
def signup(request: Request, name: str = Form(""), nickname: str = Form(""), level: str = Form(""),
           email: str = Form(""), password: str = Form(""), bank: str = Form(""), account_no: str = Form(""),
           account_holder: str = Form(""), csrf: str = Form("")):
    """크루 가입: 학교급 · 이름 · 닉네임 · 아이디 · 비밀번호 · 정산 계좌. 관리자가 승인해야 로그인된다."""
    check_csrf(request, csrf)
    name, nickname, email = name.strip()[:40], nickname.strip()[:40], email.strip().lower()[:60]
    bank, account_holder = bank.strip()[:30], account_holder.strip()[:40]
    account_no = re.sub(r"[^0-9-]", "", account_no)[:30]
    form = {"name": name, "nickname": nickname, "level": level, "email": email,
            "bank": bank, "account_no": account_no, "account_holder": account_holder}

    def fail(msg: str):
        return page(request, "signup.html", levels=LEVELS, form=form, error=msg)

    if level not in LEVELS:
        return fail("학교급을 선택해 주세요.")
    if not name:
        return fail("이름을 입력해 주세요.")
    if not nickname:
        return fail("닉네임을 입력해 주세요.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._@+-]{2,59}", email):
        return fail("아이디는 영문 소문자·숫자로 3자 이상 입력해 주세요(. _ - @ 사용 가능).")
    if len(password) < 8:
        return fail("비밀번호는 8자 이상이어야 합니다.")
    if not bank:
        return fail("은행을 입력해 주세요.")
    if len(re.sub(r"\D", "", account_no)) < 8:
        return fail("계좌번호를 숫자로 정확히 입력해 주세요.")
    if not account_holder:
        return fail("예금주를 입력해 주세요.")
    if db.one("SELECT 1 FROM users WHERE email=?", email):
        return fail("이미 사용 중인 아이디입니다. 다른 아이디를 입력해 주세요.")
    is_admin = bool(config.ADMIN_EMAIL) and email == config.ADMIN_EMAIL and not config.ADMIN_PASSWORD
    ord_ = db.one("SELECT COALESCE(MAX(ord),0) m FROM users WHERE level=?", level)["m"] + 1
    db.run("INSERT INTO users(email,name,nickname,level,ord,bank,account_no,account_holder,pw_hash,role,status,created_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
           email, name, nickname, level, ord_, bank, account_no, account_holder, hash_pw(password),
           "admin" if is_admin else "member", "approved" if is_admin else "pending", time.time())
    return page(request, "pending.html", just_signed_up=True)


@app.get("/pending")
def pending(request: Request):
    u = current_user(request)
    if u and u["status"] == "approved":
        return RedirectResponse("/", status_code=303)
    return page(request, "pending.html", just_signed_up=False)


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ── 작업(피드백) ────────────────────────────────────────────────────────────
def _job_scope(u) -> tuple[str, tuple]:
    """관리자는 모든 작업, 크루는 자기가 올린 작업과 자기 담당 수험생의 작업만 본다."""
    if u["role"] == "admin":
        return "1=1", ()
    return "(j.user_id=? OR j.examinee_id IN (SELECT id FROM examinees WHERE crew_id=?))", (u["id"], u["id"])


def _jobs(u, limit: int):
    where, args = _job_scope(u)
    return db.q(f"SELECT j.*, u.name uname FROM jobs j JOIN users u ON u.id=j.user_id WHERE {where} "
                f"ORDER BY created_at DESC LIMIT {int(limit)}", *args)


@app.get("/")
def home(request: Request):
    u = member(request)
    if u["role"] == "admin":
        mine = db.one("SELECT COUNT(*) c, SUM(crew_id IS NULL AND crew_text='' AND done_date='') un FROM examinees")
    else:
        mine = db.one("SELECT COUNT(*) c, SUM(done_date='') un FROM examinees WHERE crew_id=?", u["id"])
    return page(request, "home.html", recent=_jobs(u, 5), mine=mine)


@app.get("/lesson/new")
def lesson_new(request: Request):
    u = member(request)
    return page(request, "lesson_new.html", levels=LEVELS, stt=stt.available(), stt_names=stt_names(),
                kb=knowledge.status(), max_mb=config.MAX_UPLOAD_MB, instructors=problembank.INSTRUCTORS,
                kinds=problembank.KINDS, default_level=u["level"] or "초특")


@app.get("/interview/new")
def interview_new(request: Request):
    member(request)
    return page(request, "interview.html")


@app.get("/jobs")
def job_list(request: Request):
    u = member(request)
    return page(request, "jobs.html", jobs=_jobs(u, 200))


def _job_or_404(job_id: str, u) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404)
    j = db.job(job_id)
    if not j:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    if u["role"] != "admin" and j["user_id"] != u["id"]:
        e = db.one("SELECT crew_id FROM examinees WHERE id=?", j.get("examinee_id")) if j.get("examinee_id") else None
        if not e or e["crew_id"] != u["id"]:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return j


def _load(job_id: str, name: str):
    p = config.JOB_DIR / job_id / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@app.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    u = member(request)
    j = _job_or_404(job_id, u)
    return page(request, "job.html", job=j, fb=_load(job_id, "feedback.json"),
                transcript=_load(job_id, "transcript.json"), stats=_load(job_id, "stats.json"),
                stt_names=stt_names(), review_mark=REVIEW_MARK, bank=problembank.get(j["meta"].get("problem_id")),
                has_audio=jobs.has_audio(job_id), max_mb=config.MAX_UPLOAD_MB,
                finals=db.q("SELECT * FROM finals WHERE job_id=? ORDER BY created_at DESC", job_id),
                final_title=_final_default_title(None, j))


@app.get("/api/waiting-count")
def waiting_count():
    """영상 파일을 기다리는 작업 수(숫자만, 로그인 없이). 관리자 PC의 도우미가 할 일이 있는지 먼저 확인하는 데 쓴다."""
    return {"count": db.one("SELECT COUNT(*) c FROM jobs WHERE status='waiting'")["c"]}


@app.get("/api/jobs/waiting")
def waiting_jobs(request: Request):
    """영상 링크를 서버에서 받지 못해 파일을 기다리는 작업 목록(본인이 볼 수 있는 범위)."""
    u = member(request)
    where, args = _job_scope(u)
    rows = db.q(f"SELECT j.id, j.title, j.meta FROM jobs j WHERE j.status='waiting' AND {where} ORDER BY created_at", *args)
    return [{"id": r["id"], "title": r["title"], "video_url": json.loads(r["meta"] or "{}").get("video_url", "")}
            for r in rows]


@app.post("/jobs/{job_id}/attach")
def job_attach(request: Request, job_id: str, upload_id: str = Form(""), csrf: str = Form("")):
    """영상 파일 대기 중인 작업에 영상·음성 파일을 붙여 이어서 처리한다."""
    u = member(request)
    check_csrf(request, csrf)
    j = _job_or_404(job_id, u)
    if j["status"] not in ("waiting", "error") or jobs.has_audio(job_id):
        raise HTTPException(409, "이 작업은 이미 음성이 있거나 처리 중입니다.")
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        raise HTTPException(400, "영상 또는 음성 파일을 올려 주세요.")
    up = config.UPLOAD_DIR / upload_id
    info = json.loads((up / "info.json").read_text()) if (up / "info.json").exists() else None
    if not info or info["received"] != info["size"]:
        raise HTTPException(400, "파일 업로드가 끝나지 않았습니다.")
    jobs.attach_media(job_id, up / ("source" + info["ext"]), info["ext"])
    (up / "info.json").unlink()
    up.rmdir()
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    u = member(request)
    j = _job_or_404(job_id, u)
    return {k: j[k] for k in ("status", "step", "progress", "error")}


def _download_name(j: dict, suffix: str) -> str:
    m = j["meta"]
    name = f"{m.get('region', '')}{m.get('level', '')}{m.get('customer', '')}T - {m.get('problem_title', '')} 피드백(AI 1차){suffix}"
    return re.sub(r'[\\/:*?"<>|\r\n]', " ", name).strip()


@app.get("/jobs/{job_id}/download/{what}")
def download(request: Request, job_id: str, what: str):
    u = member(request)
    j = _job_or_404(job_id, u)
    files = {"hwpx": ("feedback.hwpx", ".hwpx", "application/hwp+zip"),
             "transcript": ("transcript.txt", " 전사문.txt", "text/plain; charset=utf-8"),
             "json": ("feedback.json", ".json", "application/json")}
    if what not in files:
        raise HTTPException(404)
    fname, suffix, mime = files[what]
    p = config.JOB_DIR / job_id / fname
    if not p.exists():
        raise HTTPException(404, "아직 파일이 만들어지지 않았습니다.")
    dn = _download_name(j, suffix)
    return FileResponse(p, media_type=mime,
                        headers={"Content-Disposition": f"attachment; filename=\"feedback{Path(fname).suffix}\"; filename*=UTF-8''{quote(dn)}"})


@app.post("/jobs/{job_id}/regenerate")
def regenerate(request: Request, job_id: str, what: str = Form("feedback"), csrf: str = Form("")):
    u = member(request)
    check_csrf(request, csrf)
    j = _job_or_404(job_id, u)
    if j["status"] in ("queued", "running"):
        raise HTTPException(409, "이미 처리 중입니다.")
    jobs.regenerate(job_id, "all" if what == "all" else "feedback")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/delete")
def job_delete(request: Request, job_id: str, csrf: str = Form("")):
    u = member(request)
    check_csrf(request, csrf)
    j = _job_or_404(job_id, u)
    if u["role"] != "admin" and j["user_id"] != u["id"]:
        raise HTTPException(403, "본인이 올린 작업만 삭제할 수 있습니다.")
    if j["status"] == "running":
        raise HTTPException(409, "처리 중인 작업은 삭제할 수 없습니다.")
    jobs.delete(job_id)
    return RedirectResponse("/jobs", status_code=303)


# ── 업로드(분할) ────────────────────────────────────────────────────────────
# 10~20분 영상은 수백 MB~수 GB라서 5MB 조각으로 나눠 올린다(프록시의 요청 크기 제한 회피, 끊겨도 이어 올리기).
@app.post("/api/uploads")
async def upload_init(request: Request):
    member(request)
    check_csrf(request, None)
    body = await request.json()
    ext = Path(str(body.get("name", ""))).suffix.lower()
    size = int(body.get("size", 0))
    if ext not in MEDIA_EXT:
        raise HTTPException(400, f"지원하지 않는 파일 형식입니다({ext or '확장자 없음'}).")
    if size <= 0 or size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"파일 크기는 {config.MAX_UPLOAD_MB}MB 이하여야 합니다.")
    uid = uuid.uuid4().hex
    d = config.UPLOAD_DIR / uid
    d.mkdir(parents=True)
    (d / "info.json").write_text(json.dumps({"ext": ext, "size": size, "received": 0, "next": 0}))
    return {"upload_id": uid}


@app.put("/api/uploads/{upload_id}/{index}")
async def upload_chunk(request: Request, upload_id: str, index: int):
    member(request)
    check_csrf(request, None)
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        raise HTTPException(404)
    d = config.UPLOAD_DIR / upload_id
    info_p = d / "info.json"
    if not info_p.exists():
        raise HTTPException(404, "업로드를 찾을 수 없습니다.")
    info = json.loads(info_p.read_text())
    if index != info["next"]:
        return {"next": info["next"], "received": info["received"]}       # 순서가 어긋나면 이어 올릴 위치를 알려 준다
    data = await request.body()
    if info["received"] + len(data) > info["size"] or len(data) > 16 * 1024 * 1024:
        raise HTTPException(400, "업로드 크기가 올바르지 않습니다.")
    with (d / ("source" + info["ext"])).open("ab") as f:
        f.write(data)
    info["received"] += len(data)
    info["next"] += 1
    info_p.write_text(json.dumps(info))
    return {"next": info["next"], "received": info["received"], "done": info["received"] == info["size"]}


async def _save_problem_files(files: list, dst: Path, start: int = 0, limit: int = 8) -> int:
    """올린 문제 파일(사진·PDF·한글)을 검사해 dst 에 번호 붙여 저장한다."""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for pf in files[:limit]:
        ext = Path(pf.filename).suffix.lower()
        if ext not in PROBLEM_EXT:
            raise HTTPException(400, f"문제 파일 형식을 지원하지 않습니다({ext}).")
        data = await pf.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "문제 파일은 25MB 이하여야 합니다.")
        (dst / f"{start + n:02d}{ext}").write_bytes(data)
        n += 1
    return n


async def _start_lesson_job(u, form, *, meta: dict, examinee_id: int | None = None,
                            saved_files: list[Path] = ()) -> str:
    """수업실연 피드백 작업을 만든다. 문제는 ① 문제 은행에서 고른 것 ② 올린 파일 ③ 붙여넣은 글 중 하나 이상."""
    def f(k: str, n: int = 200) -> str:
        return str(form.get(k) or "").strip()[:n]

    upload_id, video_url = f("upload_id"), f("video_url", 500) or meta.get("video_url", "")
    problem_text = f("problem_text", 30000)
    bank = problembank.get(f("problem_id")) if f("problem_mode", 10) != "upload" else None
    prob_files = [x for x in form.getlist("problem_files") if isinstance(x, UploadFile) and x.filename]
    saved_files = list(saved_files) if not bank else []
    if not bank and not problem_text and not prob_files and not saved_files:
        raise HTTPException(400, "문제를 고르거나, 문제 파일을 올리거나, 문제를 붙여넣어 주세요.")
    if video_url and not re.match(r"https://", video_url):
        raise HTTPException(400, "영상 링크는 https:// 로 시작해야 합니다.")
    if not upload_id and not video_url:
        raise HTTPException(400, "영상 파일을 올리거나 영상 링크를 입력해 주세요.")

    job_id = uuid.uuid4().hex
    d = jobs.job_dir(job_id)
    try:
        if upload_id:
            if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
                raise HTTPException(400, "업로드 정보가 올바르지 않습니다.")
            up = config.UPLOAD_DIR / upload_id
            info = json.loads((up / "info.json").read_text()) if (up / "info.json").exists() else None
            if not info or info["received"] != info["size"]:
                raise HTTPException(400, "영상 업로드가 끝나지 않았습니다.")
            (up / ("source" + info["ext"])).replace(d / ("source" + info["ext"]))
            (up / "info.json").unlink()
            up.rmdir()
            video_url = ""
        n = 0
        for src in saved_files[:8]:                   # 수험생 기록에 저장해 둔 문제 파일
            (d / "problem").mkdir(exist_ok=True)
            shutil.copyfile(src, d / "problem" / f"{n:02d}{src.suffix.lower()}")
            n += 1
        await _save_problem_files(prob_files, d / "problem", start=n, limit=8 - n)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise

    title_in = f("problem_title", 80)
    meta = dict(meta)
    meta.update(problem_text=problem_text, video_url=video_url,
                problem_id=bank["id"] if bank else "",
                problem_title=title_in or (f"{bank['instructor']} {bank['title']}" if bank else "") or "문제")
    title = f"{meta.get('region', '')}{meta['level']} {meta.get('customer', '')} — {meta['problem_title']}".strip(" —")
    now = time.time()
    db.run("INSERT INTO jobs(id,kind,user_id,title,status,step,meta,examinee_id,created_at,updated_at) "
           "VALUES(?,?,?,?,?,?,?,?,?,?)",
           job_id, "lesson", u["id"], title or "수업실연 피드백", "queued", "대기 중",
           json.dumps(meta, ensure_ascii=False), examinee_id, now, now)
    jobs.enqueue(job_id)
    return job_id


@app.post("/lesson")
async def lesson_create(request: Request):
    u = member(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))

    def f(k: str, n: int = 200) -> str:
        return str(form.get(k) or "").strip()[:n]

    level = f("level")
    if level not in LEVELS:
        raise HTTPException(400, "학교급을 선택해 주세요.")
    meta = {"level": level, "customer": f("customer", 40), "region": f("region", 20), "score": f("score", 60),
            "round": f("round", 20), "request": f("request", 2000)}
    job_id = await _start_lesson_job(u, form, meta=meta)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# ── 문제 은행 ───────────────────────────────────────────────────────────────
def _problem_row(p: dict) -> dict:
    return {"id": p["id"], "title": p["title"], "instructor": p["instructor"], "level": p["level"], "kind": p["kind"],
            "book": p["book"], "pages": p["pages"], "has_image": bool(problembank.page_image(p["book"], p["pages"][0]))}


@app.get("/api/problems")
def api_problems(request: Request, level: str = "", instructor: str = "", kind: str = ""):
    member(request)
    return [_problem_row(p) for p in problembank.search(level, instructor, kind)]


@app.get("/problems")
def problems_page(request: Request, level: str = "", instructor: str = "", kind: str = ""):
    u = member(request)
    level = level if level in LEVELS else (u["level"] or "초특")
    instructor = instructor if instructor in problembank.INSTRUCTORS else problembank.INSTRUCTORS[0]
    kind = kind if kind in problembank.KINDS else problembank.KINDS[0]
    return page(request, "problems.html", levels=LEVELS, instructors=problembank.INSTRUCTORS, kinds=problembank.KINDS,
                level=level, instructor=instructor, kind=kind, counts=problembank.counts(),
                problems=[_problem_row(p) for p in problembank.search(level, instructor, kind)])


def _problem_or_404(pid: str) -> dict:
    p = problembank.get(pid) if re.fullmatch(r"[0-9a-f]{12}", pid) else None
    if not p:
        raise HTTPException(404, "문제를 찾을 수 없습니다.")
    return p


@app.get("/problems/{pid}")
def problem_page(request: Request, pid: str):
    member(request)
    p = _problem_or_404(pid)
    pages = [{"n": n, "img": bool(problembank.page_image(p["book"], n))} for n in p["pages"]]
    refs = [{"n": n, "img": bool(problembank.page_image(p["book"], n))} for n in p["ref_pages"]]
    return page(request, "problem.html", p=p, pages=pages, refs=refs, siblings=problembank.siblings(p))


@app.get("/problems/{pid}/page/{n}")
def problem_page_image(request: Request, pid: str, n: int):
    member(request)
    p = _problem_or_404(pid)
    img = problembank.page_image(p["book"], n) if n in p["pages"] + p["ref_pages"] else None
    if not img:
        raise HTTPException(404)
    return FileResponse(img, media_type="image/jpeg")


# ── 수험생 ──────────────────────────────────────────────────────────────────
def _examinee(request: Request, eid: int):
    """관리자는 모든 수험생, 크루는 자기에게 배정된 수험생만."""
    u = member(request)
    e = db.one("SELECT e.*, u.name crew_name, u.nickname crew_nick FROM examinees e "
               "LEFT JOIN users u ON u.id=e.crew_id WHERE e.id=?", eid)
    if not e or (u["role"] != "admin" and e["crew_id"] != u["id"]):
        raise HTTPException(404, "수험생을 찾을 수 없습니다.")
    return u, e


def _examinee_files(eid: int) -> list[Path]:
    d = EXAMINEE_DIR / str(eid) / "problem"
    return sorted(d.glob("*")) if d.exists() else []


@app.get("/examinees")
def examinee_list(request: Request, level: str = "", view: str = ""):
    u = member(request)
    where, args = ["1=1"], []
    if u["role"] != "admin":
        where.append("e.crew_id=?")
        args.append(u["id"])
    if level in LEVELS:
        where.append("e.level=?")
        args.append(level)
    if view == "todo":
        where.append("e.done_date=''")
    rows = db.q(
        "SELECT e.*, u.name crew_name, u.nickname crew_nick, "
        "(SELECT j.id FROM jobs j WHERE j.examinee_id=e.id ORDER BY j.created_at DESC LIMIT 1) job_id, "
        "(SELECT j.status FROM jobs j WHERE j.examinee_id=e.id ORDER BY j.created_at DESC LIMIT 1) job_status "
        "FROM examinees e LEFT JOIN users u ON u.id=e.crew_id WHERE " + " AND ".join(where) +
        " ORDER BY CASE e.level WHEN '유특' THEN 0 WHEN '초특' THEN 1 ELSE 2 END, e.seq, e.id", *args)
    groups = [(lv, [r for r in rows if r["level"] == lv]) for lv in LEVELS]
    return page(request, "examinees.html", groups=[g for g in groups if g[1] or (u["role"] == "admin" and not level)],
                levels=LEVELS, level=level, view=view, total=len(rows))


@app.post("/examinees")
def examinee_create(request: Request, level: str = Form(""), name: str = Form(""), csrf: str = Form("")):
    admin(request)
    check_csrf(request, csrf)
    if level not in LEVELS or not name.strip():
        raise HTTPException(400, "학교급과 이름을 입력해 주세요.")
    now = time.time()
    seq = db.one("SELECT COALESCE(MAX(seq),0) m FROM examinees WHERE level=?", level)["m"] + 1
    eid = db.run("INSERT INTO examinees(level,seq,name,source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                 level, seq, name.strip()[:40], "manual", now, now)
    roster.assign_unassigned(level)
    return RedirectResponse(f"/examinees/{eid}", status_code=303)


@app.get("/examinees/{eid}")
def examinee_page(request: Request, eid: int):
    u, e = _examinee(request, eid)
    ejobs = db.q("SELECT j.*, u.name uname FROM jobs j JOIN users u ON u.id=j.user_id WHERE j.examinee_id=? "
                 "ORDER BY created_at DESC", eid)
    crew = db.q("SELECT id, name, nickname, level FROM users WHERE status='approved' AND level!='' "
                "ORDER BY level=? DESC, level, ord, id", e["level"]) if u["role"] == "admin" else []
    return page(request, "examinee.html", e=e, jobs=ejobs, crew=crew, levels=LEVELS, fields=roster.FIELDS,
                files=[{"name": p.name, "ext": p.suffix[1:]} for p in _examinee_files(eid)],
                bank=problembank.get(e["problem_id"]), saved=bool(request.query_params.get("saved")),
                skip_fields=("seq", "crew", "request", "note", "video_url"),
                finals=db.q("SELECT * FROM finals WHERE examinee_id=? ORDER BY created_at DESC", eid),
                final_title=_final_default_title(e),
                instructors=problembank.INSTRUCTORS, kinds=problembank.KINDS, stt=stt.available(),
                stt_names=stt_names(), max_mb=config.MAX_UPLOAD_MB, crew_editable=CREW_EDITABLE)


@app.post("/examinees/{eid}")
async def examinee_save(request: Request, eid: int):
    u, e = _examinee(request, eid)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    limits = {"request": 2000, "note": 1000, "video_url": 500}
    cols = roster.TEXT_COLS if u["role"] == "admin" else CREW_EDITABLE
    vals = {k: str(form.get(k) or "").strip()[:limits.get(k, 120)] for k in cols if k in form}
    if vals.get("video_url") and not vals["video_url"].startswith("https://"):
        raise HTTPException(400, "영상 링크는 https:// 로 시작해야 합니다.")
    if u["role"] == "admin":
        if form.get("level") in LEVELS:
            vals["level"] = form.get("level")
        if str(form.get("seq") or "").isdigit():
            vals["seq"] = int(form.get("seq"))
        if "crew_id" in form:
            cid = str(form.get("crew_id") or "")
            vals["crew_id"] = int(cid) if cid.isdigit() and db.one("SELECT 1 FROM users WHERE id=?", int(cid)) else None
            if vals["crew_id"]:
                vals["crew_text"] = ""
    if vals:
        vals["updated_at"] = time.time()
        db.run("UPDATE examinees SET " + ", ".join(f"{k}=?" for k in vals) + " WHERE id=?", *vals.values(), eid)
    return RedirectResponse(f"/examinees/{eid}?saved=1", status_code=303)


@app.post("/examinees/{eid}/delete")
def examinee_delete(request: Request, eid: int, csrf: str = Form("")):
    admin(request)
    check_csrf(request, csrf)
    if db.one("SELECT 1 FROM jobs WHERE examinee_id=?", eid):
        db.run("UPDATE jobs SET examinee_id=NULL WHERE examinee_id=?", eid)    # 피드백 작업은 작업 목록에 남긴다
    db.run("DELETE FROM examinees WHERE id=?", eid)
    shutil.rmtree(EXAMINEE_DIR / str(eid), ignore_errors=True)
    return RedirectResponse("/examinees", status_code=303)


@app.post("/examinees/{eid}/files")
async def examinee_files_add(request: Request, eid: int):
    """수험생이 보낸 문제(사진·PDF 등)를 수험생 기록에 붙여 둔다."""
    _examinee(request, eid)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    files = [x for x in form.getlist("problem_files") if isinstance(x, UploadFile) and x.filename]
    have = _examinee_files(eid)
    start = max([int(p.stem) for p in have if p.stem.isdigit()] + [-1]) + 1
    await _save_problem_files(files, EXAMINEE_DIR / str(eid) / "problem", start=start, limit=max(0, 12 - len(have)))
    return RedirectResponse(f"/examinees/{eid}#problem", status_code=303)


def _examinee_file(eid: int, name: str) -> Path:
    if not re.fullmatch(r"\d{2}\.[a-z0-9]{2,5}", name):
        raise HTTPException(404)
    p = EXAMINEE_DIR / str(eid) / "problem" / name
    if not p.exists():
        raise HTTPException(404)
    return p


@app.get("/examinees/{eid}/files/{name}")
def examinee_file(request: Request, eid: int, name: str):
    _examinee(request, eid)
    p = _examinee_file(eid, name)
    inline = p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".txt")
    return FileResponse(p, headers={"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename=\"{name}\""})


@app.post("/examinees/{eid}/files/{name}/delete")
def examinee_file_delete(request: Request, eid: int, name: str, csrf: str = Form("")):
    _examinee(request, eid)
    check_csrf(request, csrf)
    _examinee_file(eid, name).unlink()
    return RedirectResponse(f"/examinees/{eid}#problem", status_code=303)


@app.post("/examinees/{eid}/feedback")
async def examinee_feedback(request: Request, eid: int):
    """수험생 기록에서 바로 피드백을 시작한다(문제는 크루가 직접 고른다)."""
    u, e = _examinee(request, eid)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    meta = {"level": e["level"], "customer": e["name"], "region": e["region"], "score": e["score"],
            "round": e["round"], "request": e["request"], "video_url": e["video_url"]}
    job_id = await _start_lesson_job(u, form, meta=meta, examinee_id=eid, saved_files=_examinee_files(eid))
    j = db.job(job_id)
    db.run("UPDATE examinees SET problem_id=?, problem_title=?, video_url=?, updated_at=? WHERE id=?",
           j["meta"]["problem_id"], j["meta"]["problem_title"], j["meta"]["video_url"] or e["video_url"],
           time.time(), eid)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# ── 관리자: 명단 가져오기 · 크루 ────────────────────────────────────────────
@app.get("/admin/import")
def import_form(request: Request):
    admin(request)
    return page(request, "import.html", levels=LEVELS, result=None,
                crew={lv: roster.crew(lv) for lv in LEVELS})


@app.post("/admin/import")
async def import_run(request: Request):
    admin(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    up = form.get("file")
    ctx = dict(levels=LEVELS, crew={lv: roster.crew(lv) for lv in LEVELS})
    if not isinstance(up, UploadFile) or not up.filename:
        return page(request, "import.html", result=None, error="파일을 선택해 주세요.", **ctx)
    data = await up.read()
    if len(data) > 20 * 1024 * 1024:
        return page(request, "import.html", result=None, error="파일은 20MB 이하여야 합니다.", **ctx)
    default_level = form.get("level") if form.get("level") in LEVELS else ""
    try:
        rows, notes = roster.parse(roster.read_tables(data, up.filename), default_level)
    except Exception as ex:
        return page(request, "import.html", result=None, error=f"파일을 읽지 못했습니다: {ex}", **ctx)
    result = roster.import_rows(rows)
    result.update(notes=notes, filename=up.filename, rows=len(rows))
    return page(request, "import.html", result=result, **ctx)


@app.post("/admin/assign")
def admin_assign(request: Request, csrf: str = Form("")):
    admin(request)
    check_csrf(request, csrf)
    roster.assign_unassigned()
    return RedirectResponse("/examinees", status_code=303)


@app.get("/admin/users")
def admin_users(request: Request):
    admin(request)
    users = db.q("SELECT u.*, (SELECT COUNT(*) FROM examinees e WHERE e.crew_id=u.id) n_all, "
                 "(SELECT COUNT(*) FROM examinees e WHERE e.crew_id=u.id AND e.done_date='') n_todo "
                 "FROM users u ORDER BY status='pending' DESC, ord, id")
    groups = [(lv, [x for x in users if x["level"] == lv]) for lv in LEVELS]
    groups.append(("학교급 없음", [x for x in users if x["level"] not in LEVELS]))
    return page(request, "admin_users.html", groups=groups, levels=LEVELS,
                next_up={lv: db.setting(f"rr:{lv}") for lv in LEVELS},
                kb=knowledge.status(), sync=sync_state(), stt=stt.available(), stt_names=stt_names(),
                bank=problembank.counts(),
                page_images=sum(1 for _ in (config.KNOWLEDGE_DIR / "pages").glob("*/*.jpg")),
                uploaded=request.query_params.get("uploaded"),
                drive_url=finals.webhook(), drive_check=db.setting("drive_check"),
                drive_counts={r["drive_status"]: r["c"] for r in db.q("SELECT drive_status, COUNT(*) c FROM finals GROUP BY drive_status")},
                ai_key=bool(config.env("ANTHROPIC_API_KEY") or config.env("ANTHROPIC_AUTH_TOKEN")),
                key_check=_key_check, key_hints=key_hints())


_key_check: dict = {}


def key_hints() -> dict:
    """API 키 값은 보여 주지 않고, 형식이 맞는지만 알려 준다(잘못 붙여넣은 경우를 찾기 위함)."""
    out = {}
    for name, prefix in (("ANTHROPIC_API_KEY", "sk-ant-"), ("GROQ_API_KEY", "gsk_")):
        raw = os.environ.get(name, "")
        v = raw.strip().strip('"').strip("'")
        notes = []
        if not raw:
            notes.append("값이 없음")
        else:
            notes.append(f"{len(v)}자")
            notes.append(f"'{prefix}…' 로 시작함" if v.startswith(prefix) else f"'{prefix}…' 로 시작하지 않음 — 다른 값이 들어간 것 같습니다")
            if raw != raw.strip() or re.search(r"\s", v):
                notes.append("공백·줄바꿈이 섞여 있음")
            if raw.strip()[:1] in "\"'":
                notes.append("따옴표가 함께 들어감")
        out[name] = " · ".join(notes)
    return out


@app.post("/admin/check-keys")
def admin_check_keys(request: Request, csrf: str = Form("")):
    """Claude · Groq 키가 실제로 통하는지 비용이 들지 않는 호출(모델 목록)로 확인한다."""
    import requests
    admin(request)
    check_csrf(request, csrf)
    res = {"at": time.time()}
    try:
        import anthropic
        anthropic.Anthropic().models.list(limit=1)
        res["claude"] = (True, "정상")
    except Exception as e:
        res["claude"] = (False, f"{type(e).__name__}: {e}"[:300])
    if config.GROQ_API_KEY:
        try:
            r = requests.get("https://api.groq.com/openai/v1/models",
                             headers={"Authorization": "Bearer " + config.GROQ_API_KEY}, timeout=30)
            res["groq"] = (r.status_code == 200, "정상" if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            res["groq"] = (False, f"{type(e).__name__}: {e}"[:300])
    else:
        res["groq"] = (False, "GROQ_API_KEY 값이 없음")
    _key_check.clear()
    _key_check.update(res)
    return RedirectResponse("/admin/users#keys", status_code=303)


@app.post("/admin/knowledge/sync")
def admin_sync(request: Request, csrf: str = Form("")):
    admin(request)
    check_csrf(request, csrf)
    start_sync()
    return RedirectResponse("/admin/users", status_code=303)


_PACK_NAME = re.compile(r"examples/(유특|초특|중특)/[^/\\]+\.txt|books/[^/\\]+\.txt|pages/[^/\\]+/\d{4}\.jpg")


@app.post("/admin/knowledge/upload")
async def admin_knowledge_upload(request: Request):
    """드라이브 내려받기가 막혔을 때 쓰는 길: 다른 곳에서 만든 학습 자료 묶음(.zip)을 올려 data/knowledge 에 푼다.
    묶음 안의 경로는 examples/<학교급>/*.txt, books/*.txt, pages/<책>/<쪽 4자리>.jpg 만 받는다."""
    import io
    import zipfile
    admin(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    up = form.get("file")
    if not isinstance(up, UploadFile) or not up.filename:
        raise HTTPException(400, "파일을 선택해 주세요.")
    data = await up.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "묶음 파일은 100MB 이하여야 합니다.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "zip 파일이 아닙니다.")
    n = 0
    for info in zf.infolist():
        name = info.filename
        if info.is_dir() or ".." in name or not _PACK_NAME.fullmatch(name) or info.file_size > 30 * 1024 * 1024:
            continue
        target = config.KNOWLEDGE_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(info))
        n += 1
    return RedirectResponse(f"/admin/users?uploaded={n}", status_code=303)


@app.post("/admin/users/{user_id}/update")
def admin_user_update(request: Request, user_id: int, name: str = Form(""), nickname: str = Form(""),
                      level: str = Form(""), ord: str = Form(""), bank: str = Form(""), account_no: str = Form(""),
                      account_holder: str = Form(""), csrf: str = Form("")):
    admin(request)
    check_csrf(request, csrf)
    if not db.one("SELECT 1 FROM users WHERE id=?", user_id):
        raise HTTPException(404)
    db.run("UPDATE users SET name=?, nickname=?, level=?, ord=?, bank=?, account_no=?, account_holder=? WHERE id=?",
           name.strip()[:40] or "이름 없음", nickname.strip()[:40], level if level in LEVELS else "",
           int(ord) if ord.strip().isdigit() else 0, bank.strip()[:30], re.sub(r"[^0-9-]", "", account_no)[:30],
           account_holder.strip()[:40], user_id)
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/{action}")
def admin_user_action(request: Request, user_id: int, action: str, csrf: str = Form("")):
    me = admin(request)
    check_csrf(request, csrf)
    if user_id == me["id"]:
        raise HTTPException(400, "본인 계정은 변경할 수 없습니다.")
    if action == "approve":
        db.run("UPDATE users SET status='approved' WHERE id=?", user_id)
    elif action == "block":
        db.run("UPDATE users SET status='blocked' WHERE id=?", user_id)
    elif action == "delete":
        if db.one("SELECT 1 FROM jobs WHERE user_id=?", user_id) or db.one("SELECT 1 FROM examinees WHERE crew_id=?", user_id):
            db.run("UPDATE users SET status='blocked' WHERE id=?", user_id)   # 작업·담당 이력이 있으면 차단만
        else:
            db.run("DELETE FROM users WHERE id=?", user_id)
    else:
        raise HTTPException(404)
    return RedirectResponse("/admin/users", status_code=303)


# ── 피드백 완성본 ───────────────────────────────────────────────────────────
@app.get("/finals")
def finals_page(request: Request, level: str = "", q: str = ""):
    """크루가 다듬어 보낸 완성본 모음. 모든 크루가 학교급별로 보고 내려받을 수 있다."""
    u = member(request)
    level = level if level in LEVELS else ("" if level == "all" else (u["level"] or ""))
    where, args = ["1=1"], []
    if level:
        where.append("f.level=?")
        args.append(level)
    if q.strip():
        where.append("f.title LIKE ?")
        args.append(f"%{q.strip()[:40]}%")
    rows = db.q("SELECT f.*, u.name uname, u.nickname unick FROM finals f JOIN users u ON u.id=f.user_id WHERE "
                + " AND ".join(where) + " ORDER BY f.created_at DESC LIMIT 500", *args)
    counts = {r["level"]: r["c"] for r in db.q("SELECT level, COUNT(*) c FROM finals GROUP BY level")}
    return page(request, "finals.html", rows=rows, levels=LEVELS, level=level, q=q, counts=counts,
                drive_on=bool(finals.webhook()))


def _final_default_title(e=None, j=None) -> str:
    if e:
        m = j["meta"] if j else {}
        prob = (m.get("problem_title") if m else "") or e["problem_title"]
        return f"{e['region']}{e['level']}{e['name']}T - {prob} 피드백".strip()
    if j:
        m = j["meta"]
        return f"{m.get('region', '')}{m.get('level', '')}{m.get('customer', '')}T - {m.get('problem_title', '')} 피드백"
    return ""


@app.post("/finals")
async def finals_upload(request: Request):
    u = member(request)
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    up = form.get("file")
    if not isinstance(up, UploadFile) or not up.filename:
        raise HTTPException(400, "완성본 파일을 선택해 주세요.")
    ext = Path(up.filename).suffix.lower()
    if ext not in finals.EXT:
        raise HTTPException(400, "한글(hwp/hwpx), PDF, 워드(docx) 파일만 올릴 수 있습니다.")
    data = await up.read()
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(400, "파일은 40MB 이하여야 합니다.")
    eid = str(form.get("examinee_id") or "")
    jid = str(form.get("job_id") or "")
    e = db.one("SELECT * FROM examinees WHERE id=?", int(eid)) if eid.isdigit() else None
    if e and u["role"] != "admin" and e["crew_id"] != u["id"]:
        raise HTTPException(403, "담당 수험생에게만 완성본을 올릴 수 있습니다.")
    j = None
    if re.fullmatch(r"[0-9a-f]{32}", jid):
        j = _job_or_404(jid, u)
        if not e and j.get("examinee_id"):
            e = db.one("SELECT * FROM examinees WHERE id=?", j["examinee_id"])
    level = (e["level"] if e else "") or (j["meta"].get("level") if j else "") or str(form.get("level") or "")
    if level not in LEVELS:
        raise HTTPException(400, "학교급을 선택해 주세요.")
    title = str(form.get("title") or "").strip()[:120] or _final_default_title(e, j) or Path(up.filename).stem
    fid = db.run("INSERT INTO finals(level,title,ext,size,examinee_id,job_id,user_id,drive_status,created_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", level, title, ext, len(data), e["id"] if e else None,
                 j["id"] if j else "", u["id"], "pending" if finals.webhook() else "off", time.time())
    row = db.one("SELECT * FROM finals WHERE id=?", fid)
    finals.path(row).write_bytes(data)
    if e and not e["done_date"]:                       # 완성본을 올리면 피드백 완료일자를 오늘로
        db.run("UPDATE examinees SET done_date=?, updated_at=? WHERE id=?",
               time.strftime("%m.%d", time.localtime()), time.time(), e["id"])
    finals.add_to_knowledge(row)
    if finals.webhook():
        finals.upload_later(fid)
    back = str(form.get("back") or "")
    return RedirectResponse(back if back.startswith("/") and not back.startswith("//") else "/finals", status_code=303)


def _final_or_404(fid: int):
    row = db.one("SELECT * FROM finals WHERE id=?", fid)
    if not row or not finals.path(row).exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다.")
    return row


@app.get("/finals/{fid}/file")
def finals_file(request: Request, fid: int):
    member(request)
    row = _final_or_404(fid)
    name = finals.safe_name(row["title"]) + row["ext"]
    return FileResponse(finals.path(row), media_type=finals.MIME.get(row["ext"], "application/octet-stream"),
                        headers={"Content-Disposition": f"attachment; filename=\"final{row['ext']}\"; filename*=UTF-8''{quote(name)}"})


@app.post("/finals/{fid}/delete")
def finals_delete(request: Request, fid: int, csrf: str = Form("")):
    u = member(request)
    check_csrf(request, csrf)
    row = _final_or_404(fid)
    if u["role"] != "admin" and row["user_id"] != u["id"]:
        raise HTTPException(403, "본인이 올린 완성본만 지울 수 있습니다.")
    finals.path(row).unlink(missing_ok=True)
    for f in (config.KNOWLEDGE_DIR / "examples" / row["level"]).glob(f"완성본 {row['id']:04d} - *.txt"):
        f.unlink(missing_ok=True)
    db.run("DELETE FROM finals WHERE id=?", fid)
    return RedirectResponse("/finals", status_code=303)


@app.post("/finals/{fid}/drive")
def finals_drive_retry(request: Request, fid: int, csrf: str = Form("")):
    member(request)
    check_csrf(request, csrf)
    _final_or_404(fid)
    finals.upload_to_drive(fid)
    return RedirectResponse("/finals", status_code=303)


@app.post("/admin/drive")
def admin_drive(request: Request, url: str = Form(""), csrf: str = Form("")):
    """Apps Script 웹 앱 주소 저장 → 바로 연결 확인 → 못 올린 완성본 다시 올리기."""
    import requests
    admin(request)
    check_csrf(request, csrf)
    url = url.strip()
    if url and not re.fullmatch(r"https://script\.google\.com/macros/s/[A-Za-z0-9_-]+/exec", url):
        raise HTTPException(400, "Apps Script 웹 앱 주소(https://script.google.com/macros/s/…/exec)를 넣어 주세요.")
    db.set_setting("drive_webhook", url)
    msg = "연결 주소를 지웠습니다."
    if url:
        try:
            r = requests.get(url, timeout=30)
            ok = r.status_code == 200 and "horangmalco-drive-upload" in r.text
            msg = "연결 확인 완료" if ok else f"연결 확인 실패: HTTP {r.status_code} (배포의 액세스 권한이 '모든 사용자'인지 확인)"
        except Exception as ex:
            msg = f"연결 확인 실패: {type(ex).__name__}"
        if msg == "연결 확인 완료":
            threading.Thread(target=finals.retry_pending, daemon=True).start()
    db.set_setting("drive_check", msg)
    return RedirectResponse("/admin/users#drive", status_code=303)


# Jinja 필터
def _dt(ts: float) -> str:
    return time.strftime("%m.%d %H:%M", time.localtime(ts))


templates.env.filters["dt"] = _dt


def _comment(text: str):
    """코멘트 미리보기: '!!' 로 시작하는 줄은 강조(한글 파일에서는 빨간색)."""
    from markupsafe import Markup, escape
    out = []
    for line in (text or "").split("\n"):
        if line.lstrip().startswith("!!"):
            out.append(Markup('<b class="strong">') + escape(line.lstrip()[2:].lstrip()) + Markup("</b>"))
        else:
            out.append(escape(line))
    return Markup("\n").join(out)


templates.env.filters["comment"] = _comment
