"""학습 자료(피드백 예시 · 책) 검색.

data/knowledge/
  examples/<유특|초특|중특>/*.txt   팀이 작성한 실제 피드백(텍스트)
  books/*.txt                       책 본문("===== p.N =====" 으로 쪽 구분)
scripts/sync_knowledge.py 가 구글 드라이브에서 받아 채운다. 새 책(예: 중등특수)을 드라이브에 넣고
다시 실행하면 자동으로 반영된다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .. import config

LEVELS = ["유특", "초특", "중특"]
_PAGE = re.compile(r"\n\n===== p\.(\d+) =====\n")
_HANGUL = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}|\d{2,}")


def book_meta(name: str) -> dict:
    """파일 이름으로 책의 용도(실연/면접)와 학교급을 추정한다."""
    kinds = set()
    if re.search(r"실연|수업|유초책", name):
        kinds.add("lesson")
    if re.search(r"면접|레시피|원페이지|지니|유초책", name):
        kinds.add("interview")
    levels = set()
    if "유특" in name or "유초" in name or "유아" in name:
        levels.add("유특")
    if "초특" in name or "유초" in name or "초등" in name:
        levels.add("초특")
    if "중특" in name or "중등" in name:
        levels.add("중특")
    return {"kinds": kinds or {"lesson", "interview"}, "levels": levels or set(LEVELS)}


def _tokens(text: str) -> list[str]:
    out = []
    for w in _HANGUL.findall(text):
        out.append(w)
        if len(w) > 2 and "가" <= w[0] <= "힣":
            out += [w[i:i + 2] for i in range(len(w) - 1)]
    return out


@lru_cache(maxsize=1)
def _book_pages() -> list[dict]:
    pages = []
    for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt")):
        meta = book_meta(f.stem)
        parts = _PAGE.split("\n\n" + f.read_text(encoding="utf-8", errors="ignore"))
        for i in range(1, len(parts) - 1, 2):
            body = parts[i + 1].strip()
            if len(body) < 80:
                continue
            pages.append({"book": f.stem, "page": int(parts[i]), "text": body, **meta,
                          "tf": Counter(_tokens(body))})
    return pages


def search_books(query: str, level: str, kind: str = "lesson", max_chars: int = 14000, top: int = 6) -> list[dict]:
    """BM25 로 문제와 관련된 쪽을 찾고, 앞뒤 쪽을 붙여 돌려준다."""
    pool = [p for p in _book_pages() if kind in p["kinds"] and level in p["levels"]]
    if not pool or not query.strip():
        return []
    qtok = Counter(_tokens(query))
    n = len(pool)
    df = Counter()
    for p in pool:
        for t in qtok:
            if t in p["tf"]:
                df[t] += 1
    avg = sum(sum(p["tf"].values()) for p in pool) / n
    scored = []
    for idx, p in enumerate(pool):
        dl = sum(p["tf"].values())
        s = 0.0
        for t in qtok:
            f = p["tf"].get(t, 0)
            if f:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * dl / avg))
        if s > 0:
            scored.append((s, idx))
    scored.sort(reverse=True)
    picked, used, total = [], set(), 0
    for _, idx in scored[:top]:
        for j in (idx, idx + 1, idx - 1):          # 문제 쪽 다음에 모범답안이 이어지는 경우가 많다
            if j < 0 or j >= n or j in used or pool[j]["book"] != pool[idx]["book"]:
                continue
            if total + len(pool[j]["text"]) > max_chars:
                continue
            used.add(j)
            total += len(pool[j]["text"])
            picked.append(pool[j])
    picked.sort(key=lambda p: (p["book"], p["page"]))
    return [{"book": p["book"], "page": p["page"], "text": p["text"]} for p in picked]


def pick_examples(level: str, query: str, n: int = 2, max_chars: int = 13000) -> list[dict]:
    """같은 학교급의 실제 피드백 중 문제(과목·주제)가 가장 가까운 것을 고른다."""
    d = config.KNOWLEDGE_DIR / "examples" / level
    files = sorted(d.glob("*.txt")) if d.exists() else []
    if not files:                                   # 해당 급 예시가 없으면 다른 급에서라도 가져온다
        files = sorted((config.KNOWLEDGE_DIR / "examples").glob("*/*.txt"))
    qtok = Counter(_tokens(query))
    scored = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        if len(text) < 3000:
            continue
        tf = Counter(_tokens(f.stem + " " + text[:2500]))
        overlap = sum(min(c, tf[t]) for t, c in qtok.items() if t in tf)
        completeness = min(len(text), 12000) / 12000        # 충실히 작성된 문서 선호
        scored.append((overlap + 3 * completeness, f.name, text))
    scored.sort(reverse=True)
    return [{"name": name, "text": text[:max_chars]} for _, name, text in scored[:n]]


def status() -> dict:
    ex = {lv: len(list((config.KNOWLEDGE_DIR / "examples" / lv).glob("*.txt"))) for lv in LEVELS}
    books = [f.stem for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt"))]
    return {"examples": ex, "books": books, "style_guide": config.STYLE_GUIDE.exists()}
