# -*- coding: utf-8 -*-
"""HWPX 본문(section0.xml) 조립 도우미.

기존 「호랑말코 수업실연 가이드북」의 header.xml 을 그대로 쓰므로, 아래 번호는 모두 그 파일에 들어 있는
글자 모양(charPr) · 문단 모양(paraPr) · 테두리/배경(borderFill) 번호다.
줄 배치 캐시(linesegarray)는 쓰지 않는다. 한글이 파일을 열 때 다시 계산한다(server/pipeline/hwpx.py 와 같은 방식).

글 안의 강조 표시
    **굵게**   __밑줄__   [[굵은 밑줄]]   {{파랑 굵게}}   !!빨강 굵게!!
"""
import calendar as _cal
import itertools
import math
import re
from xml.sax.saxutils import escape

TABLE_W = 41954  # 표 폭: 본문 폭 42520 에서 표 바깥 여백(283*2)을 뺀 값

# ── 글자 모양 ────────────────────────────────────────────────────────────────
CH_COVER = 1      # 80pt 굵게(표지)
CH_BODY = 4       # 12pt
CH_BOLD = 2       # 12pt 굵게 — "(1)대기실" 같은 작은 제목
CH_CHAP = 3       # 15pt 굵게 — 장 제목, 프롤로그·목차
CH_SEC = 6        # 13pt 굵게 — "1. 공고문 확인"
CH_NOTE = 5       # 12pt 파랑 굵게 — "*복장 및 준비물"
CH_T = 13         # 11pt — 표 안 본문
CH_TL = 12        # 11pt 굵게 — 표 안 제목 칸
CH_TNOTE = 15     # 11pt 파랑 굵게 — 글상자 제목
CH_MONTH = 100    # 14pt 굵게 — 달력의 "2026년 11월"
CH_SUN, CH_DAY, CH_SAT, CH_EMPTY, CH_KEY, CH_KEYS = 101, 102, 103, 104, 105, 106

# 바탕 글자 모양 → 강조 변형
VAR = {
    4: dict(b=2, u=7, bu=14, blue=5, red=11),
    2: dict(b=2, u=14, bu=14, blue=5, red=11),
    13: dict(b=12, u=56, bu=80, blue=15, red=17),
    12: dict(b=12, u=80, bu=80, blue=15, red=17),
    8: dict(b=72, u=97, bu=67, blue=99, red=95),
}
SIZE = {0: 8000, 1: 8000, 2: 1200, 3: 1500, 4: 1200, 5: 1200, 6: 1300, 7: 1200, 8: 1000, 10: 1600, 11: 1200,
        12: 1100, 13: 1100, 14: 1200, 15: 1100, 16: 1100, 17: 1100, 56: 1100, 80: 1100, 72: 1000, 97: 1000,
        67: 1000, 99: 1000, 95: 1000, 100: 1400, 101: 1000, 102: 1000, 103: 1000, 104: 1000, 105: 1000, 106: 800}

# ── 문단 모양 ────────────────────────────────────────────────────────────────
PA_J = 5          # 양쪽 맞춤, 줄 간격 160%
PA_C = 1          # 가운데, 160%
PA_L = 28         # 왼쪽, 160%
PA_MONTH, PA_CAL, PA_CAL_L = 42, 43, 44   # 달력(스타일 28)
LINE = {PA_J: 1.6, PA_C: 1.6, PA_L: 1.6, PA_MONTH: 1.14, PA_CAL: 1.0, PA_CAL_L: 1.0}

# ── 테두리/배경 ──────────────────────────────────────────────────────────────
BF = 2            # 실선 테두리
BF_GRAY = 4       # 회색 바탕(제목 칸)
BF_BLUE = 3       # 연파랑 바탕
BF_PINK = 50      # 연분홍 바탕(강조 줄)
BF_CAL_H, BF_CAL, BF_CAL_KEY, BF_CAL_STUDY = 51, 52, 53, 54

_ids = itertools.count(1001)
_MARK = re.compile(r"(\*\*.+?\*\*|__.+?__|\[\[.+?\]\]|\{\{.+?\}\}|!!.+?!!)")
_KIND = {"**": "b", "__": "u", "[[": "bu", "{{": "blue", "!!": "red"}


class X(str):
    """XML 조각. 표 칸 높이를 어림하려고 예상 높이(h)를 함께 들고 다닌다."""

    def __new__(cls, xml, h=0):
        o = super().__new__(cls, xml)
        o.h = h
        return o


def plain(text):
    return _MARK.sub(lambda m: m.group(0)[2:-2], text or "")


def _em(text):
    """글자 폭 어림(전각 1, 반각 0.55, 빈칸 0.33)."""
    w = 0.0
    for c in plain(text):
        w += 0.33 if c == " " else (0.55 if ord(c) < 0x2000 else 1.0)
    return w


def _lines(text, ch, width):
    return max(1, math.ceil(_em(text) * SIZE.get(ch, 1100) / max(width, 2000)))


def runs(text, ch):
    """강조 표시를 글자 모양이 다른 run 들로 바꾼다."""
    if not text:
        return f'<hp:run charPrIDRef="{ch}"/>'
    var = VAR.get(ch, {})
    out = []
    for part in _MARK.split(text):
        if not part:
            continue
        c = ch
        if _MARK.fullmatch(part):
            c = var.get(_KIND[part[:2]], ch)
            part = part[2:-2]
        out.append(f'<hp:run charPrIDRef="{c}"><hp:t>{escape(part)}</hp:t></hp:run>')
    return "".join(out)


def p(text="", para=PA_J, ch=CH_BODY, pb=False, style=0, pre="", width=42520):
    """문단 하나. pb=True 면 새 쪽에서 시작한다."""
    h = int(_lines(text, ch, width) * SIZE.get(ch, 1200) * LINE.get(para, 1.6))
    return X(f'<hp:p id="0" paraPrIDRef="{para}" styleIDRef="{style}" pageBreak="{1 if pb else 0}" '
             f'columnBreak="0" merged="0">{pre}{runs(text, ch)}</hp:p>', h)


# 자주 쓰는 문단
def t(text): return p(" " + text if text and text[0] not in " *-·" else text)   # 본문(첫 칸 띄움)
def li(text): return p(text)                                                      # 그대로
def e(ch=CH_BODY): return p("", ch=ch)                                           # 빈 줄
def chap(text): return p(text, PA_C, CH_CHAP, pb=True)                           # 장 제목(새 쪽)
def sec(text): return p(text, PA_J, CH_SEC)                                      # 절 제목
def sub(text): return p(text, PA_J, CH_BOLD)                                     # 작은 제목
def note(text): return p(text, PA_J, CH_NOTE)                                    # 파란 * 제목


def col_ctrl(n, ch=CH_BODY):
    """단 설정을 바꾸는 조판 부호(n=1 한 단, n=2 두 단)."""
    gap = 2268 if n > 1 else 0
    return (f'<hp:run charPrIDRef="{ch}"><hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" '
            f'colCount="{n}" sameSz="1" sameGap="{gap}"/></hp:ctrl></hp:run>')


# ── 표 ───────────────────────────────────────────────────────────────────────
def C(text="", cs=1, rs=1, bf=BF, para=PA_J, ch=CH_T, va="CENTER"):
    """본문 칸. text 는 줄바꿈으로 문단을 나누거나, [문자열 | (문자열, 문단, 글자) | X] 목록."""
    return dict(text=text, cs=cs, rs=rs, bf=bf, para=para, ch=ch, va=va)


def L(text="", cs=1, rs=1, bf=BF_GRAY, ch=CH_TL, para=PA_C):
    """제목 칸(회색 바탕, 가운데, 굵게)."""
    return C(text, cs, rs, bf, para, ch)


def Cc(text="", cs=1, rs=1, bf=BF, ch=CH_T):
    """가운데 맞춘 본문 칸."""
    return C(text, cs, rs, bf, PA_C, ch)


def _split(widths, total):
    s = float(sum(widths))
    ws = [int(total * w / s) for w in widths]
    ws[-1] += total - sum(ws)
    return ws


def _cell_body(c, width):
    """칸 안 문단 XML 과 예상 높이."""
    items = c["text"]
    if isinstance(items, str):
        items = items.split("\n")
    xml, h = [], 0
    for it in items:
        if isinstance(it, X):
            xml.append(it)
            h += it.h
            continue
        if isinstance(it, tuple):
            txt, para, ch = it
        else:
            txt, para, ch = it, c["para"], c["ch"]
        x = p(txt, para, ch, width=width - 1020)
        xml.append(x)
        h += x.h
    return "".join(xml), h + 282


def table(widths, rows, total=TABLE_W, bf=BF, cal=False):
    """widths: 열 너비 비율. rows: 칸(dict) 목록의 목록. 병합(cs, rs)은 자리를 알아서 채운다."""
    ws = _split(widths, total)
    occ, placed = set(), []
    for ri, row in enumerate(rows):
        ci = 0
        for c in row:
            while (ri, ci) in occ:
                ci += 1
            c = dict(c, row=ri, col=ci)
            for dr in range(c["rs"]):
                for dc in range(c["cs"]):
                    occ.add((ri + dr, ci + dc))
            c["w"] = sum(ws[ci: ci + c["cs"]])
            c["xml"], c["need"] = _cell_body(c, c["w"])
            placed.append(c)
            ci += c["cs"]
    n_rows = len(rows)
    heights = [0] * n_rows
    for c in placed:
        if c["rs"] == 1:
            heights[c["row"]] = max(heights[c["row"]], c["need"])
    for c in placed:  # 여러 줄에 걸친 칸이 더 크면 마지막 줄에 보탠다
        if c["rs"] > 1:
            have = sum(heights[c["row"]: c["row"] + c["rs"]])
            if c["need"] > have:
                heights[c["row"] + c["rs"] - 1] += c["need"] - have
    heights = [h or 2042 for h in heights]
    body = []
    for ri in range(n_rows):
        body.append("<hp:tr>")
        for c in (x for x in placed if x["row"] == ri):
            h = sum(heights[ri: ri + c["rs"]])
            body.append(
                f'<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" '
                f'borderFillIDRef="{c["bf"]}">'
                f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="{c["va"]}" '
                'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                f'{c["xml"]}</hp:subList>'
                f'<hp:cellAddr colAddr="{c["col"]}" rowAddr="{ri}"/>'
                f'<hp:cellSpan colSpan="{c["cs"]}" rowSpan="{c["rs"]}"/>'
                f'<hp:cellSz width="{c["w"]}" height="{h}"/>'
                '<hp:cellMargin left="510" right="510" top="141" bottom="141"/></hp:tc>')
        body.append("</hp:tr>")
    tid = next(_ids)
    tbl = (
        f'<hp:tbl id="{tid}" zOrder="{tid % 1000}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
        'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
        f'rowCnt="{n_rows}" colCnt="{len(ws)}" cellSpacing="0" borderFillIDRef="{bf}" noAdjust="0">'
        f'<hp:sz width="{total}" widthRelTo="ABSOLUTE" height="{sum(heights)}" heightRelTo="ABSOLUTE" protect="0"/>'
        '<hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
        'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
        '<hp:outMargin left="283" right="283" top="283" bottom="283"/>'
        '<hp:inMargin left="510" right="510" top="141" bottom="141"/>'
        f'{"".join(body)}</hp:tbl>')
    ch, para = (8, PA_J) if cal else (CH_BODY, PA_J)
    return X(f'<hp:p id="0" paraPrIDRef="{para}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
             f'<hp:run charPrIDRef="{ch}">{tbl}<hp:t/></hp:run></hp:p>', sum(heights) + 566)


def kv(rows, w=(7, 35), label_bf=BF_GRAY):
    """[제목, 내용] 두 칸짜리 표."""
    return table(w, [[L(a, bf=label_bf), C(b)] for a, b in rows])


def box(title, lines, total=TABLE_W):
    """한 칸짜리 글상자. title 은 파란 굵은 글씨."""
    items = ([(title, PA_J, CH_TNOTE)] if title else []) + list(lines)
    return table([1], [[C(items)]], total=total)


def qa(q, answers):
    """Q/A 글상자."""
    return table([1], [[C([("Q. " + q, PA_J, CH_TL)] + ["A. " + answers[0]] + list(answers[1:]))]])


def ox(bad, good, total=TABLE_W):
    """X / O 예시 표."""
    return table((3, 39), [[L("X"), C(bad)], [L("O"), C(good)]], total=total)


def head(*names, bf=BF_GRAY):
    """표의 제목 줄."""
    return [L(n, bf=bf) for n in names]


# ── 달력 ─────────────────────────────────────────────────────────────────────
def calendar(year, month, study=None, key=None, spans=None):
    """월 달력(일요일 시작).
    study: {날짜: "1회차"}  파란 칸
    key:   {날짜: "1차 시험"}  분홍 칸
    spans: [(시작일, 끝일, "스터디 구성")]  같은 주 안에서 합친 분홍 칸
    """
    study, key, spans = study or {}, key or {}, spans or []

    def cp(text, ch=CH_DAY, para=PA_CAL):
        return X(f'<hp:p id="0" paraPrIDRef="{para}" styleIDRef="28" pageBreak="0" columnBreak="0" merged="0">'
                 f'{runs(text, ch)}</hp:p>', int(SIZE[ch] * 1.0))

    def day_ch(i):
        return CH_SUN if i == 0 else CH_DAY

    rows = [[C([cp(n, [CH_SUN, CH_DAY, CH_DAY, CH_DAY, CH_DAY, CH_DAY, CH_SAT][i])], bf=BF_CAL_H)
             for i, n in enumerate("일월화수목금토")]]
    for week in _cal.Calendar(firstweekday=6).monthdayscalendar(year, month):
        row, i = [], 0
        while i < 7:
            d = week[i]
            sp = next((s for s in spans if d and s[0] <= d <= s[1]), None)
            if sp:
                n = 1
                while i + n < 7 and week[i + n] and week[i + n] <= sp[1]:
                    n += 1
                last = week[i + n - 1]
                label = f"{d}~{last} {sp[2]}" if last != d else f"{d} {sp[2]}"
                row.append(C([cp(label, CH_KEY)], cs=n, bf=BF_CAL_KEY))
                i += n
                continue
            if not d:
                row.append(C([cp("", CH_EMPTY, PA_CAL_L), cp("", CH_EMPTY, PA_CAL_L)], bf=BF_CAL))
            elif d in key:
                row.append(C([cp(str(d), CH_KEY), cp(key[d], CH_KEYS)], bf=BF_CAL_KEY))
            elif d in study:
                row.append(C([cp(str(d), day_ch(i)), cp(f"({study[d]})", CH_DAY)], bf=BF_CAL_STUDY))
            else:
                row.append(C([cp(str(d), day_ch(i)), cp("", CH_EMPTY)], bf=BF_CAL))
            i += 1
        rows.append(row)
    title = X(f'<hp:p id="0" paraPrIDRef="{PA_MONTH}" styleIDRef="28" pageBreak="0" columnBreak="0" merged="0">'
              f'{runs(f"{year}년 {month}월", CH_MONTH)}</hp:p>', 3600)
    return [title, table([1] * 7, rows, total=41951, cal=True)]


def flat(blocks):
    """중첩된 목록을 편다."""
    out = []
    for b in blocks:
        if isinstance(b, (list, tuple)):
            out.extend(flat(b))
        else:
            out.append(b)
    return out
