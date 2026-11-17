"""수험생 명단: 스프레드시트(구글 시트 · 네이버폼 결과) 가져오기와 담당 크루 순번 배정."""
from __future__ import annotations

import csv
import io
import re
import time

from . import db

LEVELS = ["유특", "초특", "중특"]

# 화면·시트에 쓰는 열 이름 ↔ DB 열
FIELDS = [("seq", "연번"), ("round", "회수"), ("name", "이름"), ("phone", "연락처"), ("region", "지역"),
          ("score", "1차 컷플"), ("upload_date", "영상 업로드 날짜"), ("email", "이메일"), ("request", "요구사항"),
          ("video_url", "영상링크"), ("note", "비고"), ("crew", "담당자"), ("done_date", "피드백 완료일자"),
          ("personal", "개인일정"), ("settle", "정산")]
_HEAD = {label.replace(" ", ""): key for key, label in FIELDS}
TEXT_COLS = ["round", "name", "phone", "region", "score", "upload_date", "email", "request", "video_url", "note",
             "done_date", "personal", "settle"]
_REGION_SHORT = {"경기도": "경기", "강원도": "강원", "충청북도": "충북", "충청남도": "충남", "전라북도": "전북",
                 "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주도": "제주",
                 "서울특별시": "서울", "서울시": "서울"}


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


# ── 파일 읽기 ───────────────────────────────────────────────────────────────
def read_tables(data: bytes, filename: str) -> list[tuple[str, list[list[str]]]]:
    """[(시트 이름, 행들)] — .xlsx 는 모든 시트, .csv 는 한 장."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        return [(ws.title, [[_s(c) for c in row] for row in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    if name.endswith((".csv", ".tsv", ".txt")):
        for enc in ("utf-8-sig", "cp949"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("파일 글자 인코딩을 알 수 없습니다. 엑셀(.xlsx)로 저장해 올려 주세요.")
        delim = "\t" if name.endswith(".tsv") or text.count("\t") > text.count(",") else ","
        return [("", [[_s(c) for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)])]
    raise ValueError("엑셀(.xlsx) 또는 CSV 파일만 올릴 수 있습니다.")


def _split2(v: str) -> tuple[str, str]:
    parts = re.split(r"\s*[/,]\s*", v.strip(), maxsplit=1)
    return (parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")


def _level_of(text: str) -> str:
    t = text.replace(" ", "")
    for lv in LEVELS:
        if lv in t:
            return lv
    if "유아" in t or "유치원" in t:
        return "유특"
    if "초등" in t:
        return "초특"
    if "중등" in t or "중학" in t or "고등" in t:
        return "중특"
    return ""


def parse(tables: list[tuple[str, list[list[str]]]], default_level: str = "") -> tuple[list[dict], list[str]]:
    """표 → 수험생 행 목록. 네이버폼 결과와 '피드백 현황' 시트 형식을 머리글로 구분한다."""
    rows, notes = [], []
    for sheet, table in tables:
        head_i = next((i for i, r in enumerate(table[:10])
                       if sum(1 for c in r if c) >= 3 and any(k in "".join(r).replace(" ", "")
                                                              for k in ("성함", "이름"))), None)
        if head_i is None:
            if any(any(c for c in r) for r in table):
                notes.append(f"'{sheet or '시트'}': 머리글(이름/성함)을 찾지 못해 건너뜀")
            continue
        head = [c.replace(" ", "").replace("\n", "") for c in table[head_i]]
        body = [r for r in table[head_i + 1:] if any(c for c in r)]

        def col(*keys):
            return next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)

        if col("성함") is not None and col("학교급") is not None:          # 네이버폼 결과
            ix = {"who": col("성함"), "area": col("응시지역", "지역"), "level": col("학교급"), "email": col("이메일"),
                  "request": col("중점", "피드백받고", "요구"), "upload": col("영상을보내", "날짜"),
                  "ts": col("응답일시"), "no": col("참여자")}
            n = 0
            for r in body:
                g = lambda k: r[ix[k]].strip() if ix[k] is not None and ix[k] < len(r) else ""  # noqa: E731
                level = _level_of(g("level")) or default_level
                name, phone = _split2(g("who"))
                if not name or not level:
                    continue
                region, score = _split2(g("area"))
                region = _REGION_SHORT.get(region.replace(" ", ""), region)
                rows.append({"level": level, "name": name, "phone": phone, "region": region, "score": score,
                             "email": g("email"), "request": g("request"), "upload_date": g("upload"),
                             "source": "form", "src_key": f"form:{g('ts')}|{g('email').lower() or name}"})
                n += 1
            notes.append(f"'{sheet or '시트'}': 네이버폼 응답 {n}건")
        else:                                                               # 피드백 현황 시트
            ix = {key: next((i for i, h in enumerate(head) if h == label), None) for label, key in _HEAD.items()}
            if ix.get("name") is None:
                notes.append(f"'{sheet or '시트'}': '이름' 열이 없어 건너뜀")
                continue
            lv_col = col("학교급")
            sheet_level = _level_of(sheet)
            n = 0
            for r in body:
                g = lambda k: r[ix[k]].strip() if ix.get(k) is not None and ix[k] < len(r) else ""  # noqa: E731
                level = sheet_level or (_level_of(r[lv_col]) if lv_col is not None and lv_col < len(r) else "") or default_level
                if not g("name") or not level:
                    continue
                row = {k: g(k) for k in TEXT_COLS}
                row.update(level=level, seq=int(g("seq")) if g("seq").isdigit() else 0, crew_text=g("crew"),
                           source="sheet",
                           src_key="sheet:" + "|".join([level, g("name"), re.sub(r"\D", "", g("phone")),
                                                        g("round"), g("video_url")]))
                rows.append(row)
                n += 1
            if n or sheet_level:
                notes.append(f"'{sheet or '시트'}': {sheet_level or '학교급 열 기준'} {n}명")
    return rows, notes


# ── 크루 · 순번 배정 ────────────────────────────────────────────────────────
def crew(level: str) -> list:
    return db.q("SELECT * FROM users WHERE status='approved' AND level=? ORDER BY ord, id", level)


def next_crew(level: str):
    """학교급별로 크루를 순번대로 한 명씩 돌아가며 고른다(마지막으로 배정한 사람 다음 차례)."""
    members = crew(level)
    if not members:
        return None
    last = db.setting(f"rr:{level}")
    ids = [str(m["id"]) for m in members]
    pick = members[(ids.index(last) + 1) % len(members)] if last in ids else members[0]
    db.set_setting(f"rr:{level}", str(pick["id"]))
    return pick


def assign_unassigned(level: str = "") -> int:
    """담당자가 없는 수험생(완료된 건 제외)을 연번 순서대로 배정한다."""
    sql = "SELECT id, level FROM examinees WHERE crew_id IS NULL AND crew_text='' AND done_date=''"
    rows = db.q(sql + (" AND level=?" if level else "") + " ORDER BY level, seq, id", *([level] if level else []))
    n = 0
    for r in rows:
        c = next_crew(r["level"])
        if c:
            db.run("UPDATE examinees SET crew_id=?, updated_at=? WHERE id=?", c["id"], time.time(), r["id"])
            n += 1
    return n


def _match_crew(text: str, level: str):
    text = text.strip()
    if not text:
        return None
    rows = db.q("SELECT * FROM users WHERE (name=? OR nickname=?) AND status!='blocked' ORDER BY level=? DESC, id",
                text, text, level)
    return rows[0] if rows else None


def _same_pending(r: dict):
    """명단에 이미 있는 같은 사람의 진행 중인 행(같은 학교급, 완료일 없음, 이메일 또는 이름+연락처 일치, 회수가 다르지 않음)."""
    if r.get("done_date"):
        return None
    email = r.get("email", "").strip().lower()
    phone = re.sub(r"\D", "", r.get("phone", ""))
    for ex in db.q("SELECT * FROM examinees WHERE level=? AND done_date='' AND name=? ORDER BY id DESC",
                   r["level"], r.get("name", "")):
        same = (email and ex["email"].strip().lower() == email) or \
               (phone and re.sub(r"\D", "", ex["phone"]) == phone) or (not email and not phone)
        if same and not (r.get("round") and ex["round"] and r["round"] != ex["round"]):
            return ex
    return None


def import_rows(rows: list[dict]) -> dict:
    """새 수험생만 추가한다(같은 파일을 다시 올려도 중복되지 않음). 담당자가 비어 있으면 순번대로 배정."""
    added, skipped, linked = 0, 0, 0
    now = time.time()
    for r in rows:
        if db.one("SELECT 1 FROM examinees WHERE src_key=?", r["src_key"]):
            skipped += 1
            continue
        ex = _same_pending(r)
        if ex:
            # 같은 사람이 폼과 시트 양쪽에 있는 경우(아직 완료되지 않은 같은 회차): 새로 만들지 않고 빈 칸만 채운다.
            fill = {k: r[k] for k in TEXT_COLS if r.get(k) and not ex[k]}
            fill["src_key"] = r["src_key"]
            if not ex["crew_id"] and not ex["crew_text"] and r.get("crew_text"):
                c = _match_crew(r["crew_text"], r["level"])
                fill.update(crew_id=c["id"] if c else None, crew_text="" if c else r["crew_text"])
            db.run("UPDATE examinees SET " + ", ".join(f"{k}=?" for k in fill) + ", updated_at=? WHERE id=?",
                   *fill.values(), now, ex["id"])
            linked += 1
            continue
        seq = r.get("seq") or (db.one("SELECT COALESCE(MAX(seq),0) m FROM examinees WHERE level=?", r["level"])["m"] + 1)
        c = _match_crew(r.get("crew_text", ""), r["level"])
        vals = {k: r.get(k, "") for k in TEXT_COLS}
        db.run("INSERT INTO examinees(level,seq,crew_id,crew_text,source,src_key,created_at,updated_at,"
               + ",".join(TEXT_COLS) + ") VALUES(?,?,?,?,?,?,?,?," + ",".join("?" * len(TEXT_COLS)) + ")",
               r["level"], seq, c["id"] if c else None, "" if c else r.get("crew_text", ""), r["source"],
               r["src_key"], now, now, *vals.values())
        added += 1
    assigned = assign_unassigned()
    return {"added": added, "skipped": skipped, "linked": linked, "assigned": assigned}
