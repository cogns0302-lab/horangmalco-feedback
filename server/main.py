"""수업실연·면접 피드백 웹앱 (비공개: 관리자 승인 회원만 사용)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import config, db, jobs
from .pipeline import knowledge, stt
from .pipeline.hwpx import REVIEW_MARK

BASE = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="fb_session",
                   max_age=7 * 24 * 3600, same_site="lax", https_only=config.COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}
PROBLEM_EXT = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".hwp", ".hwpx", ".txt"}
LEVELS = ["유특", "초특", "중특"]


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
    jobs.start()


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
        return page(request, "login.html", error="이메일 또는 비밀번호가 올바르지 않습니다.")
    request.session.clear()
    request.session["uid"] = u["id"]
    return RedirectResponse("/", status_code=303)


@app.get("/signup")
def signup_form(request: Request):
    return page(request, "signup.html")


@app.post("/signup")
def signup(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...),
           csrf: str = Form("")):
    check_csrf(request, csrf)
    name, email = name.strip()[:40], email.strip().lower()[:120]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return page(request, "signup.html", error="이메일 형식이 올바르지 않습니다.")
    if len(password) < 8:
        return page(request, "signup.html", error="비밀번호는 8자 이상이어야 합니다.")
    if not name:
        return page(request, "signup.html", error="이름을 입력해 주세요.")
    if not db.one("SELECT 1 FROM users WHERE email=?", email):
        is_admin = bool(config.ADMIN_EMAIL) and email == config.ADMIN_EMAIL and not config.ADMIN_PASSWORD
        db.run("INSERT INTO users(email,name,pw_hash,role,status,created_at) VALUES(?,?,?,?,?,?)",
               email, name, hash_pw(password), "admin" if is_admin else "member",
               "approved" if is_admin else "pending", time.time())
    # 이미 가입된 이메일이어도 같은 화면을 보여 준다(가입 여부 노출 방지)
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


# ── 화면 ────────────────────────────────────────────────────────────────────
@app.get("/")
def home(request: Request):
    member(request)
    recent = db.q("SELECT j.*, u.name uname FROM jobs j JOIN users u ON u.id=j.user_id ORDER BY created_at DESC LIMIT 5")
    return page(request, "home.html", recent=recent)


@app.get("/lesson/new")
def lesson_new(request: Request):
    member(request)
    return page(request, "lesson_new.html", levels=LEVELS, stt=stt.available(),
                stt_names={k: v[0] for k, v in stt.PROVIDERS.items()}, kb=knowledge.status(),
                max_mb=config.MAX_UPLOAD_MB)


@app.get("/interview/new")
def interview_new(request: Request):
    member(request)
    return page(request, "interview.html")


@app.get("/jobs")
def job_list(request: Request):
    member(request)
    rows = db.q("SELECT j.*, u.name uname FROM jobs j JOIN users u ON u.id=j.user_id ORDER BY created_at DESC LIMIT 200")
    return page(request, "jobs.html", jobs=rows)


def _job_or_404(job_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404)
    j = db.job(job_id)
    if not j:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return j


def _load(job_id: str, name: str):
    p = config.JOB_DIR / job_id / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@app.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    member(request)
    j = _job_or_404(job_id)
    return page(request, "job.html", job=j, fb=_load(job_id, "feedback.json"),
                transcript=_load(job_id, "transcript.json"), stats=_load(job_id, "stats.json"),
                stt_names={k: v[0] for k, v in stt.PROVIDERS.items()}, review_mark=REVIEW_MARK)


@app.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    member(request)
    j = _job_or_404(job_id)
    return {k: j[k] for k in ("status", "step", "progress", "error")}


def _download_name(j: dict, suffix: str) -> str:
    m = j["meta"]
    name = f"{m.get('region', '')}{m.get('level', '')}{m.get('customer', '')}T - {m.get('problem_title', '')} 피드백(AI 1차){suffix}"
    return re.sub(r'[\\/:*?"<>|\r\n]', " ", name).strip()


@app.get("/jobs/{job_id}/download/{what}")
def download(request: Request, job_id: str, what: str):
    member(request)
    j = _job_or_404(job_id)
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
    member(request)
    check_csrf(request, csrf)
    j = _job_or_404(job_id)
    if j["status"] in ("queued", "running"):
        raise HTTPException(409, "이미 처리 중입니다.")
    jobs.regenerate(job_id, "all" if what == "all" else "feedback")
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.post("/jobs/{job_id}/delete")
def job_delete(request: Request, job_id: str, csrf: str = Form("")):
    u = member(request)
    check_csrf(request, csrf)
    j = _job_or_404(job_id)
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
    upload_id, video_url = f("upload_id"), f("video_url", 500)
    problem_text = f("problem_text", 30000)
    prob_files = [x for x in form.getlist("problem_files") if isinstance(x, UploadFile) and x.filename]
    if not problem_text and not prob_files:
        raise HTTPException(400, "문제를 붙여넣거나 문제 파일을 올려 주세요.")
    if video_url and not re.match(r"https://", video_url):
        raise HTTPException(400, "영상 링크는 https:// 로 시작해야 합니다.")

    job_id = uuid.uuid4().hex
    d = jobs.job_dir(job_id)
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
    elif not video_url:
        raise HTTPException(400, "영상 파일을 올리거나 영상 링크를 입력해 주세요.")

    (d / "problem").mkdir(exist_ok=True)
    for i, pf in enumerate(prob_files[:8]):
        ext = Path(pf.filename).suffix.lower()
        if ext not in PROBLEM_EXT:
            raise HTTPException(400, f"문제 파일 형식을 지원하지 않습니다({ext}).")
        data = await pf.read()
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "문제 파일은 25MB 이하여야 합니다.")
        (d / "problem" / f"{i:02d}{ext}").write_bytes(data)

    meta = {"level": level, "customer": f("customer", 40), "region": f("region", 20), "score": f("score", 60),
            "round": f("round", 20), "problem_title": f("problem_title", 80), "request": f("request", 2000),
            "problem_text": problem_text, "video_url": video_url}
    title = f"{meta['region']}{level} {meta['customer']} — {meta['problem_title']}".strip(" —")
    now = time.time()
    db.run("INSERT INTO jobs(id,kind,user_id,title,status,step,meta,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
           job_id, "lesson", u["id"], title or "수업실연 피드백", "queued", "대기 중",
           json.dumps(meta, ensure_ascii=False), now, now)
    jobs.enqueue(job_id)
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


# ── 관리자: 회원 승인 ───────────────────────────────────────────────────────
@app.get("/admin/users")
def admin_users(request: Request):
    admin(request)
    return page(request, "admin_users.html", users=db.q("SELECT * FROM users ORDER BY status='pending' DESC, created_at DESC"))


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
        if db.one("SELECT 1 FROM jobs WHERE user_id=?", user_id):
            db.run("UPDATE users SET status='blocked' WHERE id=?", user_id)   # 작업 이력이 있으면 차단만
        else:
            db.run("DELETE FROM users WHERE id=?", user_id)
    else:
        raise HTTPException(404)
    return RedirectResponse("/admin/users", status_code=303)


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
