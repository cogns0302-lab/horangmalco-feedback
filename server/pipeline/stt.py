"""음성 인식(STT). 키가 설정된 제공자를 동시에 호출하고, 성공한 결과만 모아 돌려준다.

각 제공자 결과 형식: {"provider": str, "segments": [{"start": 초, "end": 초, "text": str}], "text": str}

간투사("음", "어")는 피드백 항목이므로, 제공자의 '간투사 제거' 옵션은 모두 끈다.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .. import config


def _clova(audio: Path, hints: list[str]) -> dict:
    # NAVER CLOVA Speech 장문 인식 (파일 업로드, 동기)
    params = {"language": "ko-KR", "completion": "sync", "wordAlignment": True, "fullText": True,
              "noiseFiltering": True, "diarization": {"enable": False}}
    if hints:
        params["boostings"] = [{"words": ",".join(hints[:100])}]
    with audio.open("rb") as f:
        r = requests.post(
            config.CLOVA_INVOKE_URL.rstrip("/") + "/recognizer/upload",
            headers={"X-CLOVASPEECH-API-KEY": config.CLOVA_SECRET},
            files={"media": (audio.name, f, "audio/mpeg"),
                   "params": (None, json.dumps(params, ensure_ascii=False), "application/json")},
            timeout=1800)
    r.raise_for_status()
    d = r.json()
    if d.get("result") not in (None, "COMPLETED", "SUCCEEDED"):
        raise RuntimeError(f"CLOVA: {d.get('result')} {d.get('message')}")
    segs = [{"start": s["start"] / 1000, "end": s["end"] / 1000, "text": s["text"].strip()}
            for s in d.get("segments", []) if s.get("text", "").strip()]
    return {"segments": segs, "text": d.get("text", "")}


def _rtzr(audio: Path, hints: list[str]) -> dict:
    # 리턴제로(VITO) 일반 STT: 토큰 발급 → 전사 요청 → 결과 폴링
    base = "https://openapi.vito.ai/v1"
    tok = requests.post(base + "/authenticate",
                        data={"client_id": config.RTZR_CLIENT_ID, "client_secret": config.RTZR_CLIENT_SECRET},
                        timeout=60)
    tok.raise_for_status()
    h = {"Authorization": "Bearer " + tok.json()["access_token"]}
    cfg = {"model_name": "sommers", "use_diarization": False, "use_itn": True,
           "use_disfluency_filter": False, "use_profanity_filter": False, "use_paragraph_splitter": True}
    if hints:
        cfg["keywords"] = hints[:100]
    with audio.open("rb") as f:
        r = requests.post(base + "/transcribe", headers=h,
                          files={"file": (audio.name, f, "audio/mpeg")},
                          data={"config": json.dumps(cfg, ensure_ascii=False)}, timeout=600)
    r.raise_for_status()
    tid = r.json()["id"]
    for _ in range(360):
        time.sleep(5)
        g = requests.get(f"{base}/transcribe/{tid}", headers=h, timeout=60)
        g.raise_for_status()
        d = g.json()
        if d.get("status") == "completed":
            us = d["results"]["utterances"]
            segs = [{"start": u["start_at"] / 1000, "end": (u["start_at"] + u["duration"]) / 1000,
                     "text": u["msg"].strip()} for u in us if u.get("msg", "").strip()]
            return {"segments": segs, "text": " ".join(s["text"] for s in segs)}
        if d.get("status") == "failed":
            raise RuntimeError("RTZR 전사 실패: " + json.dumps(d, ensure_ascii=False)[:300])
    raise RuntimeError("RTZR 전사 시간 초과")


def _whisper_compatible(audio: Path, hints: list[str], *, url: str, key: str, model: str) -> dict:
    # OpenAI 형식의 전사 API. whisper 계열은 구간 타임스탬프를 주고, gpt-4o-transcribe 계열은 텍스트만 준다.
    data = [("model", model), ("language", "ko")]
    if hints:
        # 프롬프트에 간투사를 넣어 두면 Whisper가 "음", "어"를 덜 지운다.
        data.append(("prompt", "음, 어, 그, 자, 특수교사 임용 2차 수업 실연입니다. " + ", ".join(hints[:50])))
    if "whisper" in model:
        data += [("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"),
                 ("timestamp_granularities[]", "word"), ("temperature", "0")]
    else:
        data.append(("response_format", "json"))
    with audio.open("rb") as f:
        r = requests.post(url, headers={"Authorization": "Bearer " + key},
                          files={"file": (audio.name, f, "audio/mpeg")}, data=data, timeout=1800)
    r.raise_for_status()
    d = r.json()
    segs = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
            for s in d.get("segments") or [] if s.get("text", "").strip()]
    return {"segments": _by_sentence(segs, d.get("words") or []), "text": d.get("text", "")}


def _by_sentence(segs: list[dict], words: list[dict]) -> list[dict]:
    """Whisper 구간은 20~30초씩 묶이는 일이 많다. 단어 타임스탬프로 문장마다 시작 시각을 다시 잡는다."""
    if not words:
        return segs
    out = []
    for seg in segs:
        sents = [x.strip() for x in re.split(r"(?<=[.?!])\s+", seg["text"]) if x.strip()]
        if len(sents) <= 1:
            out.append(seg)
            continue
        ws = [w for w in words if seg["start"] - 0.05 <= w["start"] < seg["end"] + 0.05]
        total = sum(len(x.split()) for x in sents) or 1
        pos = 0
        for i, sent in enumerate(sents):
            wi = min(len(ws) - 1, round(pos * len(ws) / total)) if ws else -1
            start = ws[wi]["start"] if wi >= 0 else seg["start"] + (seg["end"] - seg["start"]) * pos / total
            pos += len(sent.split())
            wj = min(len(ws) - 1, max(wi, round(pos * len(ws) / total) - 1)) if ws else -1
            end = ws[wj]["end"] if wj >= 0 else seg["end"]
            out.append({"start": max(start, seg["start"]) if i else seg["start"], "end": end, "text": sent})
    return out


def _openai(audio: Path, hints: list[str]) -> dict:
    return _whisper_compatible(audio, hints, url="https://api.openai.com/v1/audio/transcriptions",
                               key=config.OPENAI_API_KEY, model=config.OPENAI_STT_MODEL)


def _groq(audio: Path, hints: list[str]) -> dict:
    # Groq 의 Whisper large-v3 (빠르고 저렴, 구간 타임스탬프 제공)
    return _whisper_compatible(audio, hints, url="https://api.groq.com/openai/v1/audio/transcriptions",
                               key=config.GROQ_API_KEY, model=config.GROQ_STT_MODEL)


def _elevenlabs(audio: Path, hints: list[str]) -> dict:
    # ElevenLabs Scribe: 단어 단위 타임스탬프 → 문장 단위로 묶는다.
    with audio.open("rb") as f:
        r = requests.post("https://api.elevenlabs.io/v1/speech-to-text",
                          headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                          files={"file": (audio.name, f, "audio/mpeg")},
                          data=[("model_id", config.ELEVENLABS_STT_MODEL), ("language_code", "ko"),
                                ("timestamps_granularity", "word"), ("tag_audio_events", "false"),
                                ("diarize", "false"), ("no_verbatim", "false")]       # 간투사를 지우지 않는다
                               + [("keyterms", h) for h in hints[:100] if len(h) <= 50], timeout=1800)
    r.raise_for_status()
    d = r.json()
    segs, cur = [], None
    for w in d.get("words", []):
        if w.get("type") == "audio_event":
            continue
        t = w.get("text", "")
        if cur is None:
            if not t.strip():
                continue
            cur = {"start": w.get("start", 0.0), "end": w.get("end", 0.0), "text": ""}
        gap = (w.get("start", cur["end"]) - cur["end"]) if w.get("type") == "word" else 0
        if gap > 1.2 and cur["text"].strip():
            segs.append(cur)
            cur = {"start": w["start"], "end": w["end"], "text": ""}
        cur["text"] += t
        cur["end"] = w.get("end", cur["end"])
        if t.strip().endswith((".", "?", "!", "。")) and len(cur["text"]) > 12:
            segs.append(cur)
            cur = None
    if cur and cur["text"].strip():
        segs.append(cur)
    for s in segs:
        s["text"] = s["text"].strip()
    return {"segments": segs, "text": d.get("text", "")}


PROVIDERS = {
    "clova": ("NAVER CLOVA Speech", _clova, lambda: bool(config.CLOVA_INVOKE_URL and config.CLOVA_SECRET)),
    "rtzr": ("리턴제로 VITO", _rtzr, lambda: bool(config.RTZR_CLIENT_ID and config.RTZR_CLIENT_SECRET)),
    "groq": ("Groq Whisper", _groq, lambda: bool(config.GROQ_API_KEY)),
    "openai": ("OpenAI", _openai, lambda: bool(config.OPENAI_API_KEY)),
    "elevenlabs": ("ElevenLabs Scribe", _elevenlabs, lambda: bool(config.ELEVENLABS_API_KEY)),
}


def available() -> list[str]:
    return [p for p in config.STT_PROVIDERS if p in PROVIDERS and PROVIDERS[p][2]()]


def transcribe_all(audio: Path, hints: list[str]) -> tuple[list[dict], dict[str, str]]:
    """설정된 모든 STT를 병렬 호출. (성공 결과 목록, {제공자: 오류}) 를 돌려준다."""
    names = available()
    if not names:
        raise RuntimeError("STT API 키가 하나도 설정되어 있지 않습니다. .env 에 CLOVA/RTZR/GROQ/OPENAI/ELEVENLABS 키 중 "
                           "하나 이상을 넣어 주세요.")
    results, errors = [], {}

    def call(name: str):
        try:
            out = PROVIDERS[name][1](audio, hints)
            if not out["segments"] and not out["text"].strip():
                raise RuntimeError("빈 결과")
            out["provider"] = name
            return name, out, None
        except Exception as e:  # 한 제공자가 실패해도 나머지로 계속한다
            detail = ""
            if isinstance(e, requests.HTTPError) and e.response is not None:
                detail = " " + e.response.text[:300]
            return name, None, f"{type(e).__name__}: {e}{detail}"

    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        for name, out, err in ex.map(call, names):
            if out:
                results.append(out)
            else:
                errors[name] = err
    if not results:
        raise RuntimeError("모든 STT 호출이 실패했습니다: " + json.dumps(errors, ensure_ascii=False))
    return results, errors
