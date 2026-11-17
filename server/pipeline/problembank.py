"""문제 은행: 책 본문에서 수업실연 문제를 찾아 강사 · 학교급 · 유형(기출/예상)으로 분류한다.

책은 스캔본을 글자 인식한 텍스트라 오인식이 많다. 그래서 문제 머리말에 반복되는 문구
("2026학년도 평가원 지역 특수학교(초등) 교사 임용후보자 …", "2025 서울", "제1 회 초등특수 수업실연 모의고사")만
느슨하게 찾아 문제의 시작 쪽을 정하고, 다음 문제가 시작되기 전까지를 한 문제로 본다.
문제 쪽 그림(data/knowledge/pages/<책>/<쪽>.jpg)은 scripts/sync_knowledge.py 가 만든다.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from .. import config

INSTRUCTORS = ["박해인", "서진"]
KINDS = ["기출", "예상"]
_PAGE = re.compile(r"\n\n===== p\.(\d+) =====\n")

# 지역 이름의 글자 인식 변형
_REGIONS = {
    "평가원": ["평가원", "펑가원", "폄가뭔", "폄가윈", "폄가뮌", "평가윈"],
    "경기": ["경기", "겸기", "검기", "경가"],
    "서울": ["서울", "서물", "서묠"],
    "대구": ["대구"],
    "인천": ["인천", "인전"],
    "충북": ["충북", "중북"],
    "세종": ["세종"],
    "강원": ["강원"],
    "광주": ["광주"],
}
_REGION_OF = {v: k for k, vs in _REGIONS.items() for v in vs}
_REGION_ALT = "|".join(sorted(_REGION_OF, key=len, reverse=True))


def instructor_of(book: str) -> str | None:
    for name in INSTRUCTORS:
        if name in book:
            return name
    return None


def is_lesson_book(book: str) -> bool:
    return bool(instructor_of(book)) and bool(re.search(r"실연|수업|유초책", book)) and "면접" not in book.replace("유초책", "")


def _pages(path: Path) -> list[tuple[int, str]]:
    parts = _PAGE.split("\n\n" + path.read_text(encoding="utf-8", errors="ignore"))
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" /|·•-—:")


_NOT_TOPIC = r"^(및 )?차시$|^활동 내용$|또는 단원|^학습\s?목표$|주요 학습\s?내용|^수업 진행$|^학습 활동$|^중심학습$|의 개관|^해설|^자율$|^수업 방법$"


def _field(text: str, labels: str) -> str:
    """'교과 \\n 국어' 처럼 표의 이름 칸 다음 줄에 오는 값을 찾는다."""
    m = re.search(rf"(?:^|\n)\s*(?:{labels})\s*[:：]?\s*\n?\s*([^\n]{{2,40}})", text)
    if not m:
        return ""
    v = _clean(m.group(1))
    hangul = len(re.findall(r"[가-힣]", v))
    if hangul < 2 or hangul < len(v) * 0.5 or re.search(r"학년|단원|대상|주제|제재|목표|성취|영역", v[:3]):
        return ""
    if re.match(r"(의|및|또는|이|을|를)\s", v) or re.search(_NOT_TOPIC, v):
        return ""
    return v[:30]


def _topic(text: str) -> str:
    subj = _field(text, "교과|과목")
    topic = _field(text, "제재|주제|주저1|활동명|놀이 주제|놀이주제|프로젝트 주제") or _field(text, "단원")
    return " · ".join(x for x in (subj, topic) if x)


# ── 서진: "2026학년도 평가원 지역 특수학교(초등) 교사 임용후보자 선정 경쟁시험 / 수업실연 기출 문제" ──────────
_SJ_PAST = re.compile(
    rf"(20[12]\d)\s*학\s*[년넌뎐]\s*도\s*({_REGION_ALT})\s*지역\s*특수학교\s*[（(]\s*(초등|유치원)\s*[）)]")
_SJ_MOCK = re.compile(r"2027\s*학\s*[년넌뎐]\s*도\s*특수학교\s*/?\s*[（(]\s*(초등|유치원)\s*[）)]")
_PLAN = re.compile(r"지도\s*[안만편]|과정\s*[안만]")


def _seojin(book: str, pages: list[tuple[int, str]]) -> list[dict]:
    marks = []                                   # (쪽 순번, 종류, 연도, 지역)
    for idx, (_, text) in enumerate(pages):
        for m in _SJ_PAST.finditer(text):
            tail = text[m.end():m.end() + 90]
            kind = "plan" if _PLAN.search(tail.split("1 .")[0].split("1.")[0]) else "기출"
            marks.append((idx, kind, m.group(1), _REGION_OF[m.group(2)]))
            break
        else:
            m = _SJ_MOCK.search(text)
            if m:
                tail = text[m.end():m.end() + 90]
                kind = "plan" if _PLAN.search(tail.split("1 .")[0].split("1.")[0]) else "예상"
                marks.append((idx, kind, "", ""))
            elif len(text.strip()) < 110:        # 장 표지 등 구분 쪽
                marks.append((idx, "break", "", ""))
    level = "유특" if "유특" in book or "유아" in book else "초특"
    out, mock_no = [], 0
    for k, (idx, kind, year, region) in enumerate(marks):
        if kind not in ("기출", "예상"):
            continue
        nxt = marks[k + 1][0] if k + 1 < len(marks) else len(pages)
        span = pages[idx:min(nxt, idx + 4)]
        text = "\n".join(t for _, t in span)
        if kind == "기출":
            label = f"{year}학년도 {region}"
        else:
            mock_no += 1
            label = f"실전 연습문제 {mock_no:02d}"
        out.append({"level": level, "kind": kind, "year": year, "region": region, "label": label,
                    "pages": [p for p, _ in span], "text": text})
    return out


# ── 박해인: "2026 평가원" / "제1 회 초등특수 수업실연 모의고사" ──────────────────────────────────────────
_PH_SECTION = re.compile(r"(조등|초등|유아)\s*특수\s*수업실연\s*기[줄출졸]\s*문제")
_PH_REGION_HEAD = re.compile(rf"^[。oO0ㅇ°]\s*/?\s*({_REGION_ALT})\s*$")
_PH_PAST = re.compile(r"^(.{0,7}?)(20[12]\d)\s*(\S{1,4})\s*$")
_PH_MOCK = re.compile(r"제\s*(\d)\s*회\s*\n?\s*(조등|초등|유아)\s*특수\s*수업실연\s*모의고사")
_PH_BODY = re.compile(r"수업실연|본시|수업\s*조건|단원의 개관|학년군|놀이")
_PH_END = re.compile(r"과정안\(지도안\)|교수\s*[•·]\s*학습\s*과정안\s*\n")


def _parkhaein(book: str, pages: list[tuple[int, str]]) -> list[dict]:
    marks = []                                   # (쪽 순번, 쪽 안 위치, 종류, 학교급, 연도, 지역, 회)
    level, region, started = "", "평가원", False
    for idx, (_, text) in enumerate(pages):
        if "차례" in text[-40:] or "차례" in text[:40]:
            continue
        if started and _PH_END.search(text[:200]) and "모의고사" not in text[:300]:
            break
        pos = 0
        lines = text.split("\n")
        for i, line in enumerate(lines):
            s = line.strip()
            sec = _PH_SECTION.search(s)
            if sec:
                level, region, started = ("유특" if sec.group(1) == "유아" else "초특"), "평가원", True
            elif started:
                rh = _PH_REGION_HEAD.match(s) or (s in ("。", "o", "O", "0") and i + 1 < len(lines)
                                                   and re.match(rf"^({_REGION_ALT})$", lines[i + 1].strip()))
                if rh:
                    region = _REGION_OF[(rh.group(1) if hasattr(rh, "group") else lines[i + 1].strip())]
                else:
                    m = _PH_PAST.match(s)
                    if m and not m.group(3).isdigit() and _PH_BODY.search("\n".join(lines[i + 1:i + 9])):
                        reg = _REGION_OF.get(m.group(3), region)
                        marks.append((idx, pos, "기출", level, m.group(2), reg, 0))
            pos += len(line) + 1
        for m in _PH_MOCK.finditer(text):
            started = True
            marks.append((idx, m.start(), "예상", "유특" if m.group(2) == "유아" else "초특", "", "", int(m.group(1))))
    marks.sort()
    out = []
    for k, (idx, pos, kind, lv, year, reg, no) in enumerate(marks):
        if k + 1 < len(marks):
            n_idx, n_pos = marks[k + 1][0], marks[k + 1][1]
            end = n_idx + 1 if n_pos > 120 and n_idx > idx else max(n_idx, idx + 1)
        else:
            end = idx + 3
        span = pages[idx:min(end, idx + 5)]
        text = pages[idx][1][pos:] if len(span) == 1 else pages[idx][1][pos:] + "\n" + "\n".join(t for _, t in span[1:])
        label = f"{year}학년도 {reg}" if kind == "기출" else f"수업실연 모의고사 {no}회"
        out.append({"level": lv, "kind": kind, "year": year, "region": reg, "label": label,
                    "pages": [p for p, _ in span], "text": text})
    return out


def parse_book(path: Path) -> list[dict]:
    book = path.stem
    inst = instructor_of(book)
    if not inst or not is_lesson_book(book):
        return []
    pages = _pages(path)
    found = _seojin(book, pages) if inst == "서진" else _parkhaein(book, pages)
    by_key: dict[tuple, dict] = {}
    for p in found:
        key = (p["level"], p["kind"], p["label"])
        if key in by_key:
            # 같은 기출이 이론편(답변 예시·스크립트)과 문제편에 두 번 실린 경우: 뒤쪽(문제편)을 문제로 삼고
            # 앞쪽은 예시 답안 참고 쪽으로 붙여 둔다.
            p["ref_pages"] = by_key[key].get("ref_pages", []) + by_key[key]["pages"]
            p["ref_text"] = (by_key[key].get("ref_text", "") + "\n" + by_key[key]["text"]).strip()
        by_key[key] = p
    out = []
    for p in by_key.values():
        topic = _topic(p["text"][:1500])
        p.update(instructor=inst, book=book, title=p["label"] + (f" — {topic}" if topic else ""),
                 id=hashlib.md5(f"{book}|{p['level']}|{p['kind']}|{p['label']}".encode()).hexdigest()[:12])
        p.setdefault("ref_pages", [])
        p.setdefault("ref_text", "")
        out.append(p)
    return out


def _signature() -> tuple:
    return tuple((f.name, f.stat().st_mtime) for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt")))


@lru_cache(maxsize=2)
def _index(sig: tuple) -> dict[str, dict]:
    out = {}
    for f in sorted((config.KNOWLEDGE_DIR / "books").glob("*.txt")):
        for p in parse_book(f):
            out[p["id"]] = p
    return out


def all_problems() -> list[dict]:
    return list(_index(_signature()).values())


def get(problem_id: str) -> dict | None:
    return _index(_signature()).get(problem_id or "")


def search(level: str = "", instructor: str = "", kind: str = "") -> list[dict]:
    rows = [p for p in all_problems()
            if (not level or p["level"] == level) and (not instructor or p["instructor"] == instructor)
            and (not kind or p["kind"] == kind)]
    rows.sort(key=lambda p: (p["kind"], -(int(p["year"]) if p["year"] else 0), p["label"]))
    return rows


def counts() -> dict:
    """{강사: {학교급: {유형: 개수}}}"""
    out: dict = {}
    for p in all_problems():
        out.setdefault(p["instructor"], {}).setdefault(p["level"], {}).setdefault(p["kind"], 0)
        out[p["instructor"]][p["level"]][p["kind"]] += 1
    return out


def siblings(problem: dict) -> list[dict]:
    """같은 기출(연도·지역·학교급)을 실은 다른 책의 문제 — 그 책의 풀이·후기·예시가 참고가 된다."""
    if problem["kind"] != "기출":
        return []
    return [p for p in all_problems() if p["id"] != problem["id"] and p["kind"] == "기출"
            and p["level"] == problem["level"] and p["year"] == problem["year"] and p["region"] == problem["region"]]


def page_dir(book: str) -> Path:
    return config.KNOWLEDGE_DIR / "pages" / re.sub(r'[\\/:*?"<>|]', "_", book)


def page_image(book: str, page: int) -> Path | None:
    p = page_dir(book) / f"{int(page):04d}.jpg"
    return p if p.exists() else None


def page_images(problem: dict) -> list[Path]:
    return [p for p in (page_image(problem["book"], n) for n in problem["pages"]) if p]


def wanted_pages(path: Path) -> list[int]:
    """이 책에서 그림으로 남겨야 할 쪽(문제와 예시 답안이 실린 쪽)."""
    return sorted({n for p in parse_book(path) for n in p["pages"] + p["ref_pages"]})
