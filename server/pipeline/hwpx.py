"""피드백 JSON → 한글(HWPX) 파일.

server/assets/template.hwpx (팀의 '피드백 틀'에서 스타일만 남긴 빈 템플릿)에 본문 XML을 만들어 넣는다.
스타일 ID(charPr/paraPr/borderFill)는 템플릿 header.xml 의 것을 그대로 쓴다.
줄 배치 캐시(linesegarray)는 쓰지 않는다 — 한글이 파일을 열 때 다시 계산한다.
"""
from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "template.hwpx"

# ── 템플릿 header.xml 의 스타일 ID ─────────────────────────────────────────
CH_TITLE = 6      # 15pt 굵게  : <1. 조건>
CH_SUB = 12       # 12pt 굵게  : 2. 수험생 유의 사항
CH_LABEL = 11     # 11pt 굵게  : 표 머리/항목 칸
CH_STAGE = 7      # 12pt 굵게  : 도입/활동1…, 태도·총평 항목 칸
CH_BODY = 5       # 11pt       : 본문
CH_MARK = 66      # 10pt 굵게 빨강 : ○ △ ×
CH_GREEN = 16     # 11pt 초록  : ex. 대안 스크립트
CH_RED = 45       # 11pt 빨강  : 강조 지적
CH_BLUE = 9       # 11pt 파랑  : 2차(영상) 검토 표시
PA_CENTER = 0
PA_JUSTIFY = 1
PA_LEFT = 21
BF_BODY = 3       # 흰 칸
BF_LABEL = 6      # 색 칸(항목)
BF_HEAD = 5       # 색 칸(<2. 수업> 머리)

REVIEW_MARK = "[2차 영상 검토]"

_ids = iter(range(1_900_000_000, 2_100_000_000))


def _p(runs: list[tuple[str, int]] | str, para: int = PA_JUSTIFY, char: int = CH_BODY) -> str:
    if isinstance(runs, str):
        runs = [(runs, char)]
    body = ""
    for text, ch in runs:
        if text:
            body += f'<hp:run charPrIDRef="{ch}"><hp:t>{escape(text)}</hp:t></hp:run>'
        else:
            body += f'<hp:run charPrIDRef="{ch}"/>'
    if not body:
        body = f'<hp:run charPrIDRef="{char}"/>'
    return (f'<hp:p id="0" paraPrIDRef="{para}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f"{body}</hp:p>")


def _rich(text: str, para: int = PA_JUSTIFY, char: int = CH_BODY) -> str:
    """여러 줄 텍스트 → 문단들. 'ex.' 로 시작하는 블록은 초록, '!!' 로 시작하는 줄은 빨강, REVIEW_MARK 줄은 파랑."""
    out, green = [], False
    for line in (text or "").replace("\r", "").split("\n"):
        s = line.strip()
        if not s:
            green = False
            out.append(_p("", para, char))
            continue
        low = s.lower()
        if low.startswith(("ex.", "ex)", "ex ", "예)", "예시)")):
            green = True
        if s.startswith(REVIEW_MARK):
            out.append(_p(line, para, CH_BLUE))
        elif s.startswith("!!"):
            out.append(_p(line.replace("!!", "", 1).lstrip(), para, CH_RED))
        else:
            out.append(_p(line, para, CH_GREEN if green else char))
    return "".join(out) or _p("", para, char)


def _est_height(text: str, width: int, paras: int = 1) -> int:
    usable = max(width - 1020, 2000)
    lines = 0
    for line in (text or "").split("\n"):
        lines += max(1, math.ceil(len(line) * 1100 / usable))
    return max(lines, paras) * 1760 + 282


class Table:
    """열 너비를 고정한 격자에 칸을 놓는다. rowspan/colspan 지원."""

    def __init__(self, widths: list[int]):
        self.widths = widths
        self.rows: list[list[dict]] = []

    def row(self, *cells: dict) -> None:
        self.rows.append(list(cells))

    def xml(self) -> str:
        n_rows = len(self.rows)
        # 각 행의 높이 추정(가장 큰 칸 기준)
        heights = []
        for r in self.rows:
            h = 1760 + 282
            for c in r:
                w = sum(self.widths[c["col"]: c["col"] + c.get("cs", 1)])
                if c.get("rs", 1) == 1:
                    h = max(h, _est_height(c.get("plain", ""), w))
            heights.append(h)
        body = ""
        for ri, r in enumerate(self.rows):
            body += "<hp:tr>"
            for c in r:
                cs, rs = c.get("cs", 1), c.get("rs", 1)
                w = sum(self.widths[c["col"]: c["col"] + cs])
                h = sum(heights[ri: ri + rs])
                body += (
                    f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" '
                    f'borderFillIDRef="{c.get("bf", BF_BODY)}">'
                    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
                    'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                    f'{c["xml"]}</hp:subList>'
                    f'<hp:cellAddr colAddr="{c["col"]}" rowAddr="{ri}"/>'
                    f'<hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
                    f'<hp:cellSz width="{w}" height="{h}"/>'
                    '<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>'
                )
            body += "</hp:tr>"
        tid = next(_ids)
        tbl = (
            f'<hp:tbl id="{tid}" zOrder="{tid % 1000}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
            'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
            f'rowCnt="{n_rows}" colCnt="{len(self.widths)}" cellSpacing="0" borderFillIDRef="3" noAdjust="0">'
            f'<hp:sz width="{sum(self.widths)}" widthRelTo="ABSOLUTE" height="{sum(heights)}" '
            'heightRelTo="ABSOLUTE" protect="0"/>'
            '<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
            'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            '<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
            '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
            f"{body}</hp:tbl>"
        )
        return (f'<hp:p id="0" paraPrIDRef="{PA_JUSTIFY}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="{CH_SUB}">{tbl}<hp:t/></hp:run></hp:p>')


def _label(text: str, col: int, *, cs: int = 1, rs: int = 1, bf: int = BF_LABEL, char: int = CH_LABEL) -> dict:
    return {"col": col, "cs": cs, "rs": rs, "bf": bf, "plain": text, "xml": _rich(text, PA_CENTER, char)}


def _body(text: str, col: int, *, cs: int = 1, rs: int = 1, para: int = PA_JUSTIFY, char: int = CH_BODY) -> dict:
    return {"col": col, "cs": cs, "rs": rs, "bf": BF_BODY, "plain": text, "xml": _rich(text, para, char)}


def _mark(text: str, col: int) -> dict:
    return {"col": col, "bf": BF_BODY, "plain": text, "xml": _rich(text, PA_CENTER, CH_MARK)}


def _time(times, col: int, rs: int = 1) -> dict:
    text = "\n".join(times) if isinstance(times, (list, tuple)) else (times or "")
    return {"col": col, "rs": rs, "bf": BF_BODY, "plain": text, "xml": _rich(text, PA_CENTER, CH_BODY)}


def _with_note(text: str, note: str) -> str:
    note = (note or "").strip()
    return f"{text}\n→ {note}" if note else text


def _join_comment(item: dict) -> str:
    c = (item.get("comment") or "").strip()
    ex = (item.get("example") or "").strip()
    if ex and not ex.lower().startswith(("ex.", "ex)")):
        ex = "ex. " + ex
    return f"{c}\n\n{ex}" if ex else c


def _numbered(items) -> str:
    if isinstance(items, str):
        return items
    items = [i.strip() for i in (items or []) if i and i.strip()]
    return "\n\n".join(f"{n}. {t}" for n, t in enumerate(items, 1))


def build_body(fb: dict) -> str:
    x: list[str] = [_p("", PA_CENTER, CH_TITLE)]

    # ── <1. 조건> ──────────────────────────────────────────────────────────
    x.append(_p("본시 학습 지도 내용", 22, CH_STAGE))
    t = Table([5978, 36259])
    for row in fb.get("lesson_info") or [{"label": "", "value": ""}]:
        t.row(_label(row.get("label", ""), 0), _body(row.get("value", ""), 1, para=PA_LEFT))
    x.append(t.xml())
    x.append(_p("", PA_JUSTIFY, 8))

    x.append(_p("2. 수험생 유의 사항", PA_JUSTIFY, CH_SUB))
    t = Table([32580, 4280, 5129])
    t.row(_label("유의 사항", 0), _label("수행", 1), _label("시간", 2))
    for c in fb.get("conditions") or []:
        t.row(_body(_with_note(c.get("text", ""), c.get("note", "")), 0, char=8),
              _mark(c.get("result", ""), 1), _time(c.get("times"), 2))
    x.append(t.xml())

    x.append(_p("3. 학생 실태 및 학습 수행 수준", PA_JUSTIFY, CH_SUB))
    t = Table([4280, 28052, 4846, 4846])
    t.row(_label("이름", 0), _label("특성, 수행 수준", 1), _label("수행", 2), _label("시간", 3))
    for s in fb.get("students") or []:
        traits = s.get("traits") or [{"text": "", "result": "", "times": []}]
        name = s.get("name", "")
        if s.get("group") and s["group"] not in name:
            name = f'{s["group"]}\n{name}' if name else s["group"]
        for i, tr in enumerate(traits):
            cells = []
            if i == 0:
                cells.append(_label(name, 0, rs=len(traits)))
            cells += [_body(_with_note(tr.get("text", ""), tr.get("note", "")), 1, para=PA_LEFT),
                      _mark(tr.get("result", ""), 2), _time(tr.get("times"), 3)]
            t.row(*cells)
    x.append(t.xml())

    x.append(_p("4. 자료", PA_JUSTIFY, CH_SUB))
    x.append(_p("", PA_JUSTIFY, CH_SUB))

    # ── <2. 수업> ──────────────────────────────────────────────────────────
    x.append(_p("<2. 수업>", PA_CENTER, CH_TITLE))
    t = Table([5536, 4121, 3396, 28742])
    t.row(_label("구성", 0, bf=BF_HEAD, char=CH_STAGE), _label("시간", 1, bf=BF_HEAD, char=CH_STAGE),
          _label("내용", 2, cs=2, bf=BF_HEAD, char=CH_STAGE))
    for st in fb.get("lesson") or []:
        items = st.get("items") or [{"time": "", "comment": ""}]
        for i, it in enumerate(items):
            cells = []
            if i == 0:
                cells.append(_label(st.get("stage", ""), 0, rs=len(items), char=CH_STAGE))
            cells += [_time(it.get("time", ""), 1), _body(_join_comment(it), 2, cs=2)]
            t.row(*cells)
    t.row(_label("기타", 0, char=CH_STAGE), _body("목표 재언급 /왜 배우나\n/왜 필요한가", 1, cs=2, para=PA_CENTER),
          _body(fb.get("lesson_etc", ""), 3))
    x.append(t.xml())
    x.append(_p("", PA_JUSTIFY, 0))

    # ── <3. 학생 참여> ─────────────────────────────────────────────────────
    x.append(_p("<3. 학생 참여>", PA_CENTER, CH_TITLE))
    part = fb.get("participation") or {}
    counts = part.get("counts") or []
    pair = 33834 // 6
    t = Table([7835] + [pair] * 5 + [33834 - pair * 5])
    chunks = [counts[i:i + 3] for i in range(0, len(counts), 3)] or [[]]
    span = len(chunks) * 2 + 1
    first = True
    for ch in chunks:
        ch = ch + [{}] * (3 - len(ch))
        head = [_label("학생 질문 및 상호작용\n현황", 0, rs=span)] if first else []
        first = False
        t.row(*head, *[_label(c.get("group", ""), 1 + 2 * i, cs=2) for i, c in enumerate(ch)])
        row = []
        for i, c in enumerate(ch):
            row.append(_body(c.get("name", ""), 1 + 2 * i, para=PA_CENTER, char=CH_LABEL))
            row.append(_body("" if c.get("count") is None else str(c.get("count")), 2 + 2 * i, para=PA_CENTER))
        t.row(*row)
    t.row(_body("종합의견: " + (part.get("summary") or ""), 1, cs=6))
    for label, key in [("학생 수준 구분", "level_distinction"), ("학생 간 상호작용", "peer_interaction"),
                       ("확산적 발문 및 대답유도", "divergent_questions"), ("흥미/\n동기유발", "motivation"),
                       ("또래교수", "peer_tutoring"), ("자기평가\n동료평가", "assessment")]:
        t.row(_label(label, 0), _body(part.get(key, ""), 1, cs=6))
    x.append(t.xml())

    # ── <4. 태도> ──────────────────────────────────────────────────────────
    x.append(_p("<4. 태도>", PA_CENTER, CH_TITLE))
    att = fb.get("attitude") or {}
    t = Table([7552, 10691, 23810])

    def human(key: str, hint: str) -> str:
        v = (att.get(key) or "").strip()
        return f"{REVIEW_MARK} {hint}" + (f"\n{v}" if v else "")

    t.row(_label("자신감 및\n수업 분위기", 0, char=CH_STAGE), _body(att.get("confidence", ""), 1, cs=2))
    t.row(_label("목소리 크기, 발음, 억양, 리듬, 속도 등", 0, char=CH_STAGE), _body(att.get("voice", ""), 1, cs=2))
    t.row(_label("수업속도", 0, char=CH_STAGE), _body(att.get("pace", ""), 1, cs=2))
    t.row(_label("자세", 0, rs=2, char=CH_STAGE), _body("", 1), _body(human("posture", "스크린샷과 함께 자세 코멘트"), 2))
    t.row(_body("", 1), _body("", 2))
    t.row(_label("시선처리", 0, char=CH_STAGE), _body("", 1), _body(human("gaze", "스크린샷과 함께 시선 코멘트"), 2))
    t.row(_label("제스쳐", 0, char=CH_STAGE), _body(human("gesture", "제스쳐 코멘트"), 1, cs=2))
    t.row(_label("공간활용", 0, char=CH_STAGE), _body(human("space", "동선·공간활용 코멘트"), 1, cs=2))
    t.row(_label("간투사\n간투어", 0, char=CH_STAGE), _body(att.get("fillers", ""), 1, cs=2))
    t.row(_label("경어", 0, char=CH_STAGE), _body(att.get("honorifics", ""), 1, cs=2))
    t.row(_label("돌발상황 대처", 0, char=CH_STAGE), _body(att.get("contingency", ""), 1, cs=2))
    t.row(_label("복장 및 단정함", 0, char=CH_STAGE), _body(human("attire", "복장·단정함 코멘트"), 1, cs=2))
    x.append(t.xml())
    x.append(_p("", PA_CENTER, CH_TITLE))

    # ── <5. 총평> ──────────────────────────────────────────────────────────
    x.append(_p("<5. 총평>", PA_CENTER, CH_TITLE))
    ov = fb.get("overall") or {}
    t = Table([6703, 35103])
    for label, text in [("태도", ov.get("attitude", "")), ("수업", ov.get("lesson", "")),
                        ("상호작용", ov.get("interaction", "")), ("개선사항", _numbered(ov.get("improvements"))),
                        ("최종의견", ov.get("final", "")), ("합격생 팁", _numbered(ov.get("tips")))]:
        t.row(_label(label, 0, char=CH_STAGE), _body(text, 1))
    x.append(t.xml())
    x.append(_p("", PA_CENTER, 125))
    x.append(_p(" *수업 실연 주제와 연관 된 지도서를 첨부하였습니다. 내용을 보신 후 이 수업에서 수정 보완하실 점을 찾아보시기를 바랍니다. "
                "자료 및 활동 등에서 유의미한 개선 사항을 찾으실 수 있을 것입니다. 이후 약식으로 간단하게 수업 실연을 해보시면 "
                "이전보다 더 나은 수업이 될 것입니다.", PA_CENTER, CH_BODY))
    x.append(_p("", PA_LEFT, CH_BODY))
    x.append(_p("<6. 참고자료: >", PA_CENTER, CH_TITLE))
    x.append(_p("", PA_CENTER, CH_TITLE))
    return "".join(x)


def build_hwpx(fb: dict) -> bytes:
    """피드백 dict → .hwpx 바이트."""
    zin = zipfile.ZipFile(TEMPLATE)
    section = zin.read("Contents/section0.xml").decode("utf-8")
    section = section.replace("{{TITLE}}", escape("<1. 조건>")).replace("{{BODY}}", build_body(fb))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)
        for name in zin.namelist():
            if name == "mimetype":
                continue
            data = section.encode("utf-8") if name == "Contents/section0.xml" else zin.read(name)
            zout.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
