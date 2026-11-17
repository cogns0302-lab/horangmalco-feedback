"""구글 드라이브의 피드백 문서·책을 받아 학습 자료(data/knowledge)로 정리한다.

  python scripts/sync_knowledge.py                 # 드라이브에서 새로 받아서 정리
  python scripts/sync_knowledge.py --local <피드백폴더> <책폴더>   # 이미 받아 둔 폴더로 정리

드라이브 폴더는 '링크가 있는 모든 사용자 보기' 권한이어야 한다. 새 책(예: 중등특수)을 폴더에 넣고
다시 실행하면 반영된다. 폴더 ID는 .env 의 DRIVE_FEEDBACK_FOLDER / DRIVE_BOOKS_FOLDER 로 바꿀 수 있다.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import config  # noqa: E402
from server.pipeline import hwp_text  # noqa: E402

FEEDBACK_FOLDER = os.environ.get("DRIVE_FEEDBACK_FOLDER", "179KvIWk6C6LFRfZrNfzAi0YKb-BjXSeO")
BOOKS_FOLDER = os.environ.get("DRIVE_BOOKS_FOLDER", "1b0YvfTHtZs2pdSrB-NFrsbOySfrJZQuS")
LEVELS = ["유특", "초특", "중특"]
_ENTRY = re.compile(r'<div class="flip-entry" id="entry-([^"]+)".*?<a href="([^"]+)".*?<div class="flip-entry-title">(.*?)</div>', re.S)


def drive_ls(fid: str) -> list[tuple[str, str, str]]:
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"https://drive.google.com/embeddedfolderview?id={fid}",
                                         headers={"User-Agent": "Mozilla/5.0"})
            page = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
            return [(m.group(1), html.unescape(m.group(2)), html.unescape(m.group(3))) for m in _ENTRY.finditer(page)]
        except Exception:
            time.sleep(2 * (attempt + 1))
    return []


def drive_walk(fid: str, path: str = "") -> list[dict]:
    out = []
    for i, href, title in drive_ls(fid):
        p = f"{path}/{title}"
        if "/drive/folders/" in href:
            out += drive_walk(i, p)
        else:
            out.append({"id": i, "path": p})
    return out


def drive_download(folder: str, dst: Path, want) -> None:
    import gdown
    for f in drive_walk(folder):
        if not want(f["path"]):
            continue
        target = dst / f["path"].lstrip("/")
        if target.exists() and target.stat().st_size > 0:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print("받는 중:", f["path"], flush=True)
        gdown.download(id=f["id"], output=str(target), quiet=True)


def level_of(path: str) -> str | None:
    for lv in LEVELS:
        if f"/{lv}/" in path or lv in Path(path).name:
            return lv
    return None


def build(feedback_dir: Path, books_dir: Path) -> None:
    ex_dir, bk_dir = config.KNOWLEDGE_DIR / "examples", config.KNOWLEDGE_DIR / "books"
    n = 0
    for f in sorted(feedback_dir.rglob("*")):
        if f.suffix.lower() not in (".hwp", ".hwpx"):
            continue
        rel = "/" + str(f.relative_to(feedback_dir))
        if not re.search(r"/2[6-9] 피드백/", rel):            # 현재 틀로 작성된 연도만 예시로 쓴다
            continue
        lv = level_of(rel)
        if not lv:
            continue
        try:
            text = hwp_text.hwpx_text(str(f)) if f.suffix.lower() == ".hwpx" else hwp_text.hwp5_text(str(f))
        except Exception as e:
            print("건너뜀:", f.name, e)
            continue
        text = text.encode("utf-8", "ignore").decode("utf-8")
        if "<2. 수업>" not in text.replace(" ", "").replace("&lt;", "<").replace("<2.수업>", "<2. 수업>") and "수업>" not in text:
            continue
        (ex_dir / lv).mkdir(parents=True, exist_ok=True)
        (ex_dir / lv / (f.stem + ".txt")).write_text(text, encoding="utf-8")
        n += 1
    print(f"피드백 예시 {n}건 정리")

    import pymupdf
    bk_dir.mkdir(parents=True, exist_ok=True)
    m = 0
    for f in sorted(books_dir.rglob("*.pdf")):
        out = bk_dir / (f.stem + ".txt")
        if out.exists() and out.stat().st_mtime >= f.stat().st_mtime:
            m += 1
            continue
        doc = pymupdf.open(f)
        with out.open("w", encoding="utf-8") as o:
            for i, pg in enumerate(doc):
                o.write(f"\n\n===== p.{i + 1} =====\n" + pg.get_text())
        if out.stat().st_size < len(doc) * 100:
            print(f"주의: '{f.name}' 은 글자 인식(OCR)이 안 된 스캔본 같습니다. 검색 가능한 PDF로 저장해 주세요.")
        m += 1
    print(f"책 {m}권 정리 →", config.KNOWLEDGE_DIR)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--local":
        build(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raw = config.DATA_DIR / "drive"
        drive_download(FEEDBACK_FOLDER, raw / "feedback", lambda p: p.lower().endswith((".hwp", ".hwpx")))
        drive_download(BOOKS_FOLDER, raw / "books", lambda p: p.lower().endswith(".pdf"))
        build(raw / "feedback", raw / "books")
