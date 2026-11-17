# -*- coding: utf-8 -*-
"""「2027 호랑말코 2차 면접 가이드북」 한글(.hwpx) 파일을 만든다.

    python3 src/make.py

template/ 의 스타일 정의(header.xml 등)는 기존 수업실연 가이드북에서 가져온 것이고,
본문(section0.xml)은 c_*.py 의 원고로 새로 만든다.
"""
import datetime
import io
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hx  # noqa: E402
from c_front import COVER, PROLOGUE, TOC, CH1  # noqa: E402
from c_prep2 import CH2  # noqa: E402
from c_interview import CH3  # noqa: E402
from c_feedback import CH4  # noqa: E402

ROOT = HERE.parent
TPL = ROOT / "template"
OUT = ROOT / "dist" / "2027 호랑말코 2차 면접 가이드북.hwpx"
TITLE = "2027 호랑말코 2차 면접 가이드북"


def build_section() -> str:
    root_open = (TPL / "section_root_open.xml").read_text(encoding="utf-8")
    secpr = (TPL / "section_secpr_runs.xml").read_text(encoding="utf-8")
    cover = [f'<hp:p id="0" paraPrIDRef="{hx.PA_C}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
             f'{secpr if i == 0 else ""}{hx.runs(line, hx.CH_COVER)}</hp:p>' for i, line in enumerate(COVER)]
    body = hx.flat([PROLOGUE, TOC, CH1, CH2, CH3, CH4])
    return root_open + "".join(cover) + "".join(body) + "</hs:sec>"


def plain_text(section: str) -> str:
    """미리보기(PrvText)와 점검에 쓰는 본문 글자."""
    out = []
    for para in re.findall(r"<hp:p .*?</hp:p>", section, flags=re.S):
        text = "".join(re.findall(r"<hp:t>(.*?)</hp:t>", para, flags=re.S))
        out.append(re.sub(r"&(amp|lt|gt);", lambda m: {"amp": "&", "lt": "<", "gt": ">"}[m.group(1)], text))
    return "\n".join(out)


def content_hpf() -> str:
    s = (TPL / "content.hpf").read_text(encoding="utf-8")
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = re.sub(r"<opf:title>.*?</opf:title>", f"<opf:title>{escape(TITLE)}</opf:title>", s)
    s = re.sub(r'(<opf:meta name="lastsaveby" content="text")>.*?</opf:meta>', r"\1/>", s)
    s = re.sub(r'(<opf:meta name="CreatedDate" content="text">).*?(</opf:meta>)', rf"\g<1>{stamp}\g<2>", s)
    s = re.sub(r'(<opf:meta name="ModifiedDate" content="text">).*?(</opf:meta>)', rf"\g<1>{stamp}\g<2>", s)
    s = re.sub(r'(<opf:meta name="date" content="text">).*?(</opf:meta>)',
               rf"\g<1>{now.year}년 {now.month}월 {now.day}일\g<2>", s)
    return s


def preview_png() -> bytes:
    """표지 모양의 미리보기 그림(724x1024)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (724, 1024), "white")
    d = ImageDraw.Draw(img)
    font = None
    for cand in ("/root/.local/share/fonts/guide/Pretendard-Bold.otf", "C:/Windows/Fonts/malgunbd.ttf",
                 "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"):
        if Path(cand).exists():
            font = ImageFont.truetype(cand, 104)
            break
    if font is None:
        return b""
    y = 70
    for line in COVER:
        w = d.textlength(line, font=font)
        d.text(((724 - w) / 2, y), line, fill="black", font=font)
        y += 166
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


SETTINGS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><ha:HWPApplicationSetting '
            'xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
            'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
            '<ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>')


def main():
    section = build_section()
    ET.fromstring(section.encode("utf-8"))            # XML 이 깨지지 않았는지 확인
    text = plain_text(section)
    png = preview_png()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    D, S = zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED
    parts = [                                           # 한글이 저장하는 순서 그대로
        ("mimetype", (TPL / "mimetype").read_bytes(), S),
        ("version.xml", (TPL / "version.xml").read_bytes(), S),
        ("Contents/header.xml", (TPL / "header.xml").read_bytes(), D),
        ("Contents/section0.xml", section.encode("utf-8"), D),
        ("Preview/PrvText.txt", text[:1500].encode("utf-8"), D),
        ("Scripts/headerScripts", (TPL / "Scripts/headerScripts").read_bytes(), D),
        ("Scripts/sourceScripts", (TPL / "Scripts/sourceScripts").read_bytes(), D),
        ("settings.xml", SETTINGS.encode("utf-8"), D),
        ("Preview/PrvImage.png", png, S),
        ("META-INF/container.rdf", (TPL / "META-INF/container.rdf").read_bytes(), D),
        ("Contents/content.hpf", content_hpf().encode("utf-8"), D),
        ("META-INF/container.xml", (TPL / "META-INF/container.xml").read_bytes(), D),
        ("META-INF/manifest.xml", (TPL / "META-INF/manifest.xml").read_bytes(), D),
    ]
    with zipfile.ZipFile(OUT, "w") as z:
        for name, data, method in parts:
            if not data:
                continue
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 17, 0, 0, 0))
            info.compress_type = method
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    n_tbl = section.count("<hp:tbl ")
    n_p = section.count("<hp:p ")
    print(f"{OUT.name}: {OUT.stat().st_size:,} bytes · 문단 {n_p} · 표 {n_tbl} · 글자 {len(text.replace(chr(10), '')):,}")
    (ROOT / "dist" / "원고.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
