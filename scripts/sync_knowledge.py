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
from server.pipeline import hwp_text, problembank  # noqa: E402

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


def fetch(file_id: str, target: Path) -> bool:
    """드라이브 파일 하나를 받는다. 구글이 한동안 내려받기를 막거나(접속 과다) 공유 권한이 없으면 False.
    한 파일이 실패해도 나머지는 계속 받도록 예외를 삼킨다."""
    import gdown
    for attempt in range(2):
        try:
            if gdown.download(id=file_id, output=str(target), quiet=True) and target.exists() and target.stat().st_size > 0:
                return True
        except Exception as e:
            print("   ", type(e).__name__, str(e).strip().splitlines()[0][:120] if str(e).strip() else "", flush=True)
        time.sleep(3 * (attempt + 1))
    target.unlink(missing_ok=True)
    return False


def drive_download(folder: str, dst: Path, want) -> None:
    for f in drive_walk(folder):
        if not want(f["path"]):
            continue
        target = dst / f["path"].lstrip("/")
        if target.exists() and target.stat().st_size > 0:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print("받는 중:", f["path"], flush=True)
        if not fetch(f["id"], target):
            print("  받기 실패(건너뜀):", f["path"], flush=True)


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
        rel = "/" + f.relative_to(feedback_dir).as_posix()
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
        if out.exists() and out.stat().st_mtime >= f.stat().st_mtime and not missing_pages(out):
            m += 1
            continue
        doc = pymupdf.open(f)
        write_book_text(doc, out)
        if out.stat().st_size < len(doc) * 100:
            print(f"주의: '{f.name}' 은 글자 인식(OCR)이 안 된 스캔본 같습니다. 검색 가능한 PDF로 저장해 주세요.")
        render_pages(doc, out)
        m += 1
    print(f"책 {m}권 정리 →", config.KNOWLEDGE_DIR)


def write_book_text(doc, out: Path) -> None:
    part = out.with_suffix(".part")
    with part.open("w", encoding="utf-8") as o:
        for i, pg in enumerate(doc):
            o.write(f"\n\n===== p.{i + 1} =====\n" + pg.get_text())
    part.replace(out)


def missing_pages(txt: Path) -> list[int]:
    """문제 은행에 쓸 쪽 가운데 아직 그림으로 만들지 않은 쪽."""
    return [n for n in problembank.wanted_pages(txt) if not problembank.page_image(txt.stem, n)]


def render_pages(doc, txt: Path) -> int:
    """문제가 실린 쪽을 그림(JPEG)으로 남긴다. 크루가 화면에서 문제를 확인하고, AI도 깨끗한 원문을 읽는다."""
    import pymupdf
    todo = missing_pages(txt)
    d = problembank.page_dir(txt.stem)
    d.mkdir(parents=True, exist_ok=True)
    for n in todo:
        if 1 <= n <= len(doc):
            pix = doc[n - 1].get_pixmap(dpi=110, colorspace=pymupdf.csRGB)
            tmp = d / f"{n:04d}.tmp.jpg"
            pix.save(str(tmp), jpg_quality=62)
            tmp.replace(d / f"{n:04d}.jpg")
    return len(todo)


def sync_books_from_drive(raw: Path) -> None:
    """책 PDF는 용량이 커서(권당 100~200MB) 한 권씩 받아 텍스트만 남기고 지운다."""
    import gdown
    import pymupdf
    bk_dir = config.KNOWLEDGE_DIR / "books"
    bk_dir.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    for f in drive_walk(BOOKS_FOLDER):
        if not f["path"].lower().endswith(".pdf"):
            continue
        out = bk_dir / (Path(f["path"]).stem + ".txt")
        have_text = out.exists() and out.stat().st_size > 0
        if have_text and not missing_pages(out):
            continue
        print("책 받는 중:", f["path"], "" if not have_text else "(문제 쪽 그림 만들기)", flush=True)
        tmp = raw / "book.pdf"
        tmp.unlink(missing_ok=True)
        if not fetch(f["id"], tmp):
            print("  받기 실패(공유 권한 또는 다운로드 한도 확인):", f["path"], flush=True)
            continue
        doc = pymupdf.open(tmp)
        if not have_text:
            write_book_text(doc, out)
            if out.stat().st_size < len(doc) * 100:
                print(f"  주의: '{f['path']}' 은 글자 인식(OCR)이 안 된 스캔본 같습니다.", flush=True)
        n = render_pages(doc, out)
        if n:
            print(f"  문제 쪽 그림 {n}장", flush=True)
        doc.close()
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--local":
        build(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raw = config.DATA_DIR / "drive"
        drive_download(FEEDBACK_FOLDER, raw / "feedback", lambda p: p.lower().endswith((".hwp", ".hwpx")))
        build(raw / "feedback", raw / "no-local-books")
        sync_books_from_drive(raw)
        print("학습 자료 동기화 완료", flush=True)
