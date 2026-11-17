"""전사문에서 기계적으로 셀 수 있는 수치(발화 속도, 긴 침묵, 간투사, 반말 후보, 학생 호명 횟수)."""
from __future__ import annotations

import re

FILLERS = ["음", "어", "아", "그", "저", "뭐", "이제", "약간", "자"]
# 문장 끝이 반말로 끝나는 후보(경어 '~요/~다/~까' 로 끝나지 않는 종결)
_BANMAL = re.compile(r"(?:했어|했지|할까|볼까|해봐|해 봐|그래|맞아|뭐야|뭐지|있지|없지|이지|거야|한다|보자|하자|줄래|줄게|할게|왔지|였지|잖아|구나|네)[?.!]?$")


def mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def parse_mmss(s: str) -> float:
    try:
        p = [int(x) for x in s.strip().split(":")]
        return p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]
    except Exception:
        return -1


def transcript_text(segments: list[dict]) -> str:
    return "\n".join(f"[{mmss(s['start'])}] {s['text']}" for s in segments)


def compute(segments: list[dict], duration: float, student_names: list[str]) -> dict:
    text = " ".join(s["text"] for s in segments)
    words = text.split()
    speech_sec = sum(max(0.0, s["end"] - s["start"]) for s in segments) or duration or 1
    total = duration or (segments[-1]["end"] if segments else 0)

    gaps = []
    for a, b in zip(segments, segments[1:]):
        g = b["start"] - a["end"]
        if g >= 4:
            gaps.append({"at": mmss(a["end"]), "seconds": round(g, 1)})

    fillers = {}
    for f in FILLERS:
        n = len(re.findall(rf"(?:(?<=\s)|^){f}[~.…,]*(?=\s|$)", text))
        if n:
            fillers[f] = n

    banmal = []
    for s in segments:
        for sent in re.split(r"(?<=[.?!])\s+", s["text"]):
            st = sent.strip()
            if 2 <= len(st) <= 40 and _BANMAL.search(st) and not st.rstrip("?.!").endswith(("요", "니다", "까요", "세요")):
                banmal.append({"at": mmss(s["start"]), "text": st})

    calls = {}
    for n in student_names:
        n = n.strip()
        if len(n) >= 2:
            calls[n] = len(re.findall(re.escape(n), text))

    # 5분 단위 발화 속도 변화
    pace = []
    step = 300
    t0 = 0
    while t0 < total:
        w = sum(len(s["text"].split()) for s in segments if t0 <= s["start"] < t0 + step)
        span = min(step, total - t0) or 1
        pace.append({"from": mmss(t0), "words_per_min": round(w / span * 60)})
        t0 += step

    return {
        "duration": mmss(total),
        "words": len(words),
        "words_per_min_overall": round(len(words) / (total or 1) * 60),
        "words_per_min_speaking": round(len(words) / speech_sec * 60),
        "pace_by_5min": pace,
        "long_pauses": gaps[:40],
        "fillers": fillers,
        "banmal_candidates": banmal[:40],
        "student_name_mentions": calls,
    }
