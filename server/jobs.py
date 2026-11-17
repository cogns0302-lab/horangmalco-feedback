"""백그라운드 작업 처리: 음성 추출 → 문제 분석 → STT(다중) → 병합 → 피드백 → HWPX.

각 단계 결과를 data/jobs/<id>/ 에 파일로 남기고, 이미 있는 결과는 다시 만들지 않는다.
(그래서 '피드백만 다시 생성'은 STT 비용이 다시 들지 않는다.)
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
from pathlib import Path

from . import config, db
from .pipeline import audio, hwpx, llm, problembank, stats, stt

_wake = threading.Event()
PRICES = {  # USD / 1M tokens (입력, 출력) — 대략적인 비용 표시용
    "claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.1, 0.5),
    "claude-fable-5-2": (8, 40), "claude-opus-5": (5, 25), "claude-opus-4-8": (5, 25),
}


class NeedsMedia(Exception):
    """영상 링크에서 음성을 받지 못했거나 파일이 없다 → 사람이(또는 도우미가) 파일을 붙여 줄 때까지 기다린다."""


def has_audio(job_id: str) -> bool:
    return (config.JOB_DIR / job_id / "audio.mp3").exists()


def attach_media(job_id: str, src: Path, ext: str) -> None:
    """대기 중인 작업에 영상·음성 파일을 붙이고 다시 줄에 세운다."""
    d = job_dir(job_id)
    for old in d.glob("source.*"):
        old.unlink(missing_ok=True)
    src.replace(d / ("source" + ext))
    enqueue(job_id)


def job_dir(job_id: str) -> Path:
    d = config.JOB_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write(p: Path, obj) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def cost_usd(usage: dict) -> float:
    total = 0.0
    for u in usage.values():
        if not isinstance(u, dict) or "model" not in u:
            continue
        pin, pout = next((v for k, v in PRICES.items() if u["model"].startswith(k)), (4, 20))
        total += (u.get("input", 0) * pin + u.get("cache_write", 0) * pin * 1.25
                  + u.get("cache_read", 0) * pin * 0.1 + u.get("output", 0) * pout) / 1e6
    return round(total, 3)


def process(job_id: str) -> None:
    job = db.job(job_id)
    meta, usage = job["meta"], job["usage"]
    d = job_dir(job_id)

    def step(name: str, pct: int) -> None:
        db.update_job(job_id, step=name, progress=pct)

    # 1) 음성 추출
    audio_path = d / "audio.mp3"
    if not audio_path.exists():
        step("음성 추출", 5)
        src = next(iter(sorted(d.glob("source.*"))), None)
        if src is None and meta.get("video_url"):
            try:
                src = audio.download_audio(meta["video_url"], d)
            except RuntimeError as e:
                # 서버에서는 YouTube 가 접속을 막는 일이 잦다. 오류로 끝내지 않고 '영상 파일 대기'로 두어,
                # 작업 화면에서 파일을 붙이거나 관리자 PC의 도우미가 받아 올리면 이어서 처리한다.
                raise NeedsMedia(str(e)) from e
        if src is None:
            raise NeedsMedia("영상(또는 음성) 파일이 없습니다.")
        meta["duration"] = audio.extract_audio(src, audio_path)
        if not config.KEEP_VIDEO:
            src.unlink(missing_ok=True)                 # 원본 영상은 지워 저장 공간을 아낀다
        db.update_job(job_id, meta=meta)
    duration = meta.get("duration") or audio.probe_duration(audio_path)

    # 2) 문제 분석(학생 이름·용어 → STT 힌트, 책 검색어)
    prob_files = sorted((d / "problem").glob("*")) if (d / "problem").exists() else []
    bank = problembank.get(meta.get("problem_id", ""))          # 문제 은행에서 고른 문제(없으면 None)
    if meta.get("problem_id") and bank is None:
        raise RuntimeError("고른 문제를 문제 은행에서 찾지 못했습니다. 관리 화면에서 학습 자료(책)를 다시 받은 뒤 "
                           "'이어서 다시 시도'를 눌러 주세요.")
    blocks = llm.problem_blocks(meta.get("problem_text", ""), prob_files, bank)
    if not blocks:
        raise RuntimeError("문제가 입력되지 않았습니다. 문제를 고르거나 문제 파일을 올려 주세요.")
    prob = _read(d / "problem.json")
    if prob is None:
        step("문제 분석", 12)
        prob, usage["problem"] = llm.analyze_problem(blocks)
        _write(d / "problem.json", prob)
        db.update_job(job_id, usage=usage)
    hints = prob["student_names"] + prob["keywords"]

    # 3) STT (설정된 제공자 동시 호출)
    tr = _read(d / "transcript.json")
    if tr is None:
        step("음성 인식(STT)", 20)
        results, errors = stt.transcribe_all(audio_path, hints)
        for r in results:
            _write(d / f"stt_{r['provider']}.json", r)
        meta["stt_ok"] = [r["provider"] for r in results]
        meta["stt_errors"] = errors
        db.update_job(job_id, meta=meta)

        # 4) 병합
        step("전사문 교차 검증", 55)
        timed = [r for r in results if r["segments"]]
        if not timed:
            segs = llm.approx_segments(results[0]["text"], duration)
            meta["timestamps_approx"] = True
        elif len(results) == 1:
            segs = timed[0]["segments"]
        else:
            segs, usage["merge"] = llm.merge_transcripts(results, prob)
        tr = {"segments": segs, "providers": meta["stt_ok"]}
        _write(d / "transcript.json", tr)
        (d / "transcript.txt").write_text(stats.transcript_text(segs), encoding="utf-8")
        db.update_job(job_id, meta=meta, usage=usage)
    segs = tr["segments"]

    # 5) 피드백
    fb = _read(d / "feedback.json")
    if fb is None:
        step("피드백 작성", 65)
        st = stats.compute(segs, duration, prob["student_names"])
        _write(d / "stats.json", st)
        fb, usage["feedback"] = llm.write_feedback(
            level=meta.get("level", "초특"), problem=blocks, problem_text=prob["problem_text"],
            segments=segs, speech_stats=st, meta=meta, bank=bank)
        _write(d / "feedback.json", fb)
        db.update_job(job_id, usage=usage)

    # 6) 한글 파일
    step("한글 파일 생성", 96)
    (d / "feedback.hwpx").write_bytes(hwpx.build_hwpx(fb))
    usage["cost_usd"] = cost_usd(usage)
    db.update_job(job_id, status="done", step="완료", progress=100, usage=usage, error="")


def _loop() -> None:
    db.conn().execute("UPDATE jobs SET status='queued' WHERE status='running'")   # 재시작 시 이어서
    while True:
        row = db.one("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
        if not row:
            _wake.wait(5)
            _wake.clear()
            continue
        jid = row["id"]
        db.update_job(jid, status="running", error="")
        try:
            process(jid)
        except NeedsMedia as e:
            db.update_job(jid, status="waiting", step="영상 파일 대기", progress=0, error=f"{e}"[:2000])
        except Exception as e:
            traceback.print_exc()
            db.update_job(jid, status="error", error=f"{e}"[:2000])


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="job-worker").start()


def enqueue(job_id: str) -> None:
    db.update_job(job_id, status="queued", step="대기 중", progress=0, error="")
    _wake.set()


def regenerate(job_id: str, what: str) -> None:
    """what='feedback': 피드백만 다시 / 'all': STT부터 다시(음성 파일은 유지)."""
    d = job_dir(job_id)
    names = ["feedback.json", "feedback.hwpx", "stats.json"]
    if what == "all":
        names += ["transcript.json", "transcript.txt", "problem.json"] + [p.name for p in d.glob("stt_*.json")]
    for n in names:
        (d / n).unlink(missing_ok=True)
    enqueue(job_id)


def delete(job_id: str) -> None:
    shutil.rmtree(config.JOB_DIR / job_id, ignore_errors=True)
    db.run("DELETE FROM jobs WHERE id=?", job_id)
