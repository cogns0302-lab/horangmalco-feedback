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


def _signature() -> tuple:
    return tuple((f.name, f.stat().st_mtime) for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt")))


@lru_cache(maxsize=2)
def _book_pages(kind: str, sig: tuple) -> list[dict]:
    """용도(lesson/interview)에 맞는 책만 읽어 색인한다. 책 파일이 바뀌면(sig) 다시 만든다."""
    pages = []
    for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt")):
        meta = book_meta(f.stem)
        if kind not in meta["kinds"]:
            continue
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
    pool = [p for p in _book_pages(kind, _signature()) if level in p["levels"]]
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


def _title_keys(title: str) -> set[str]:
    """문제 이름에서 같은 문제를 가리키는 단서(연도 두 자리, 출제 지역, 과목 등)를 뽑는다. '2025학년도 평가원 국어' → {25, 평가원, 국어}"""
    keys = {y[-2:] for y in re.findall(r"(?:20)?([12]\d)\s*(?:학년도|년)", title)}
    keys |= {y[-2:] for y in re.findall(r"\b20([12]\d)\b", title)}
    keys |= {w for w in re.findall(r"[가-힣]{2,}", title) if w not in ("학년도", "실전", "연습문제", "수업실연", "문제", "기출", "예상")}
    return keys


def pick_examples(level: str, query: str, n: int = 2, max_chars: int = 13000, title: str = "",
                  exclude: str = "") -> list[dict]:
    """지난해 실제 피드백 중 이번 문제와 가장 가까운 것을 고른다. 같은 문제(파일 이름의 연도·출제 지역·과목이 일치)를
    가장 먼저, 다음으로 같은 학교급에서 과목·주제가 가까운 문서를 고른다. exclude 는 이번 고객 이름:
    같은 사람의 지난 피드백은 같은 영상을 다룬 것일 수 있어 참고에서 뺀다."""
    root = config.KNOWLEDGE_DIR / "examples"
    qtok = Counter(_tokens(query))
    keys = _title_keys(title)
    scored = []
    for f in sorted(root.glob("*/*.txt")):
        if exclude and len(exclude) >= 2 and exclude in f.stem:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        if len(text) < 3000:
            continue
        same_level = f.parent.name == level
        tf = Counter(_tokens(f.stem + " " + text[:2500]))
        overlap = sum(min(c, tf[t]) for t, c in qtok.items() if t in tf)
        completeness = min(len(text), 12000) / 12000        # 충실히 작성된 문서 선호
        # 파일 이름 예: "황유정 - 경기초특이윤경T - 25년 평가원 국어 피드백"
        name_hits = sum(1 for k in keys if k in f.stem)
        same_problem = same_level and name_hits >= 2 and any(k.isdigit() and re.search(rf"(?<!\d){k}\s*년?", f.stem) for k in keys)
        near = f.parent.name in LEVELS and level in LEVELS and abs(LEVELS.index(f.parent.name) - LEVELS.index(level)) == 1
        score = (overlap + 3 * completeness + (1000 if same_level else 300 if near else 0)
                 + (5000 if same_problem else 0) + 20 * name_hits)
        scored.append((score, f.name, text))
    scored.sort(reverse=True)
    return [{"name": name, "text": text[:max_chars]} for _, name, text in scored[:n]]


def status() -> dict:
    ex = {lv: len(list((config.KNOWLEDGE_DIR / "examples" / lv).glob("*.txt"))) for lv in LEVELS}
    books = [f.stem for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt"))]
    return {"examples": ex, "books": books, "style_guide": config.STYLE_GUIDE.exists()}
