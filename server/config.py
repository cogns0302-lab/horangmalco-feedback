"""환경변수 설정. .env 파일이 있으면 읽는다(이미 설정된 환경변수가 우선)."""
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


DATA_DIR = Path(env("DATA_DIR", str(ROOT / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"
JOB_DIR = DATA_DIR / "jobs"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"          # scripts/build_knowledge.py 가 채운다(피드백 예시·책 텍스트)
STYLE_GUIDE = ROOT / "knowledge" / "style_guide.md"
DB_PATH = DATA_DIR / "app.db"
for d in (UPLOAD_DIR, JOB_DIR, KNOWLEDGE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# 세션 서명 키. 운영에서는 반드시 SECRET_KEY 를 고정값으로 지정(없으면 data/secret.key 에 생성·보관).
_key_file = DATA_DIR / "secret.key"
if env("SECRET_KEY"):
    SECRET_KEY = env("SECRET_KEY")
else:
    if not _key_file.exists():
        _key_file.write_text(secrets.token_urlsafe(48))
        _key_file.chmod(0o600)
    SECRET_KEY = _key_file.read_text().strip()

ADMIN_EMAIL = env("ADMIN_EMAIL").lower()
ADMIN_PASSWORD = env("ADMIN_PASSWORD")
COOKIE_SECURE = env("COOKIE_SECURE", "1") == "1"       # 로컬 http 테스트 때만 0

# ── AI ────────────────────────────────────────────────────────────────────
FEEDBACK_MODEL = env("FEEDBACK_MODEL", "claude-opus-5-5")      # 피드백 작성
FEEDBACK_EFFORT = env("FEEDBACK_EFFORT", "high")
MERGE_MODEL = env("MERGE_MODEL", "claude-haiku-5-5")           # STT 결과 교차검증·병합(저비용)
USE_FALLBACKS = env("USE_FALLBACKS", "1") == "1"

# ── STT (키가 있는 것만 자동 사용) ─────────────────────────────────────────
STT_PROVIDERS = [p.strip() for p in env("STT_PROVIDERS", "clova,rtzr,openai,elevenlabs").split(",") if p.strip()]
CLOVA_INVOKE_URL = env("CLOVA_SPEECH_INVOKE_URL")
CLOVA_SECRET = env("CLOVA_SPEECH_SECRET")
RTZR_CLIENT_ID = env("RTZR_CLIENT_ID")
RTZR_CLIENT_SECRET = env("RTZR_CLIENT_SECRET")
OPENAI_API_KEY = env("OPENAI_API_KEY")
OPENAI_STT_MODEL = env("OPENAI_STT_MODEL", "whisper-1")
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_STT_MODEL = env("ELEVENLABS_STT_MODEL", "scribe_v2")

KEEP_VIDEO = env("KEEP_VIDEO", "0") == "1"             # 음성 추출 후 원본 영상 보관 여부
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "4096"))
