"""Claude 호출: ① 문제 분석 ② 여러 STT 결과 병합 ③ 피드백 작성.

모두 구조화 출력(JSON 스키마)을 쓰고, 긴 출력에 대비해 스트리밍으로 받는다.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic

from .. import config
from . import hwp_text, knowledge, stats

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()          # ANTHROPIC_API_KEY 등 환경에서 자격 증명을 읽는다
    return _client


def _usage(msg) -> dict:
    u = msg.usage
    return {"model": msg.model, "input": u.input_tokens, "output": u.output_tokens,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}


def _ask_json(*, model: str, system, content, schema: dict, effort: str, max_tokens: int) -> tuple[dict, dict]:
    kwargs = dict(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )

    def plain():
        with client().messages.stream(**kwargs) as s:
            return s.get_final_message()

    def with_fallback():
        # 안전 분류기가 요청을 거절하면 서버가 다른 모델로 같은 요청을 이어서 처리한다.
        with client().beta.messages.stream(betas=["server-side-fallback-2026-07-01"],
                                           fallbacks="default", **kwargs) as s:
            return s.get_final_message()

    use_fb = config.USE_FALLBACKS and not model.startswith("claude-haiku")
    try:
        msg = with_fallback() if use_fb else plain()
    except anthropic.BadRequestError:
        if not use_fb:
            raise
        msg = plain()                              # fallbacks 를 지원하지 않는 환경이면 일반 호출로 재시도
    if msg.stop_reason == "refusal":
        raise RuntimeError("AI가 요청을 거절했습니다(refusal). 입력 내용을 확인해 주세요.")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("AI 출력이 길이 제한에 걸려 잘렸습니다. 다시 시도해 주세요.")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text), _usage(msg)


# ── ① 문제 분석 ─────────────────────────────────────────────────────────────
PROBLEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["problem_text", "subject", "topic", "student_names", "keywords"],
    "properties": {
        "problem_text": {"type": "string"},
        "subject": {"type": "string"},
        "topic": {"type": "string"},
        "student_names": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
}


def problem_blocks(problem_text: str, files: list[Path]) -> list[dict]:
    """문제(텍스트/이미지/PDF/HWP)를 Claude 입력 블록으로 바꾼다."""
    blocks: list[dict] = []
    texts = [problem_text.strip()] if problem_text.strip() else []
    for f in files:
        ext = f.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext[1:], "image/" + ext[1:])
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                       "data": base64.standard_b64encode(f.read_bytes()).decode()}})
        elif ext == ".pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                          "data": base64.standard_b64encode(f.read_bytes()).decode()}})
        elif ext == ".hwpx":
            texts.append(hwp_text.hwpx_text(str(f)))
        elif ext == ".hwp":
            texts.append(hwp_text.hwp5_text(str(f)))
        elif ext in (".txt", ".md"):
            texts.append(f.read_text(encoding="utf-8", errors="ignore"))
    if texts:
        blocks.append({"type": "text", "text": "<문제>\n" + "\n\n".join(texts) + "\n</문제>"})
    return blocks


def analyze_problem(blocks: list[dict]) -> tuple[dict, dict]:
    content = blocks + [{"type": "text", "text": (
        "위는 특수교사 임용 2차 수업실연 문제입니다. 음성 인식과 자료 검색에 쓰려고 합니다.\n"
        "- problem_text: 문제 전체를 빠짐없이 그대로 옮겨 적으세요(표는 줄 단위로, 조건 번호와 학생 실태 문구는 원문 그대로).\n"
        "- subject: 과목, topic: 단원·차시 주제와 학습 목표를 한 줄로.\n"
        "- student_names: 문제에 나온 학생 이름 전부(예: 가영, 나은, 다준).\n"
        "- keywords: 수업에서 말로 나올 법한 고유명사·교과 용어·교구 이름 20개 이내.")}]
    return _ask_json(model=config.MERGE_MODEL, system="문서를 정확히 옮겨 적고 정보를 뽑는 보조자입니다.",
                     content=content, schema=PROBLEM_SCHEMA, effort="low", max_tokens=16000)


# ── ② STT 병합 ──────────────────────────────────────────────────────────────
MERGE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["segments"],
    "properties": {"segments": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["start", "text"],
        "properties": {"start": {"type": "string"}, "text": {"type": "string"}}}}},
}

MERGE_SYSTEM = """당신은 한국어 음성 전사 교정자입니다. 같은 수업실연 음성을 서로 다른 음성 인식기가 받아 적은 전사문 여러 개를 받아, 가장 정확한 하나의 전사문을 만듭니다.

이 전사문은 수업실연 피드백의 근거 자료가 되므로, 화자가 실제로 한 말을 있는 그대로 남기는 것이 목적입니다.
- 인식기끼리 다른 부분은 문맥, 문제에 나온 학생 이름·교과 용어, 다수결을 근거로 실제 발화에 가장 가까운 쪽을 고릅니다.
- 화자의 말을 고쳐 주지 않습니다. 반말("뭐가 나왔지?"), 간투사("음", "어", "그"), 말 더듬음, 반복, 문법 오류는 피드백 대상이므로 한 인식기라도 받아 적었다면 그대로 둡니다.
- 어느 인식기에도 없는 말을 지어내지 않습니다.
- 타임스탬프는 '기준 전사'의 구간을 따릅니다. 구간을 합치거나 나눌 수는 있지만 시작 시각은 기준 전사에 있는 값(m:ss)을 씁니다. 한 구간은 한두 문장 정도로 유지합니다.
- 학생 이름이 비슷한 소리로 잘못 적힌 경우(예: 다준→다중)는 문제의 학생 이름으로 바로잡습니다."""


def merge_transcripts(results: list[dict], hints: dict) -> tuple[list[dict], dict]:
    timed = [r for r in results if r["segments"]]
    base = max(timed, key=lambda r: len(r["segments"]))
    parts = [f"학생 이름: {', '.join(hints.get('student_names', [])) or '(없음)'}",
             f"자주 나올 용어: {', '.join(hints.get('keywords', [])) or '(없음)'}",
             f"\n<기준 전사 인식기=\"{base['provider']}\">\n{stats.transcript_text(base['segments'])}\n</기준 전사>"]
    for r in results:
        if r is base:
            continue
        body = stats.transcript_text(r["segments"]) if r["segments"] else r["text"]
        parts.append(f"\n<비교 전사 인식기=\"{r['provider']}\">\n{body}\n</비교 전사>")
    parts.append("\n위 전사문들을 교차 검증해 하나의 전사문으로 만들어 주세요. start 는 m:ss 형식입니다.")
    out, usage = _ask_json(model=config.MERGE_MODEL, system=MERGE_SYSTEM,
                           content=[{"type": "text", "text": "\n".join(parts)}],
                           schema=MERGE_SCHEMA, effort="low", max_tokens=48000)
    segs = []
    for s in out["segments"]:
        t = stats.parse_mmss(s["start"])
        if t >= 0 and s["text"].strip():
            segs.append({"start": float(t), "end": float(t), "text": s["text"].strip()})
    if len(segs) < max(3, len(base["segments"]) // 4):       # 병합 결과가 비정상적으로 짧으면 기준 전사를 쓴다
        return base["segments"], usage
    segs.sort(key=lambda s: s["start"])
    ends = {round(b["start"]): b["end"] for b in base["segments"]}
    for i, s in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else s["start"] + 4
        s["end"] = min(max(ends.get(round(s["start"]), nxt), s["start"]), nxt) or nxt
    return segs, usage


def approx_segments(text: str, duration: float) -> list[dict]:
    """타임스탬프 없는 전사만 있을 때: 글자 수 비례로 시각을 어림한다."""
    import re
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]
    total = sum(len(s) for s in sents) or 1
    out, pos = [], 0
    for s in sents:
        st = duration * pos / total
        pos += len(s)
        out.append({"start": st, "end": duration * pos / total, "text": s})
    return out


# ── ③ 피드백 작성 ───────────────────────────────────────────────────────────
def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


_S = {"type": "string"}
_SL = {"type": "array", "items": _S}
_RESULT = {"type": "string", "enum": ["○", "△", "×", "-"]}

FEEDBACK_SCHEMA = _obj({
    "lesson_info": {"type": "array", "items": _obj({"label": _S, "value": _S})},
    "conditions": {"type": "array", "items": _obj({"text": _S, "result": _RESULT, "times": _SL, "note": _S})},
    "students": {"type": "array", "items": _obj({
        "name": _S, "group": _S,
        "traits": {"type": "array", "items": _obj({"text": _S, "result": _RESULT, "times": _SL, "note": _S})}})},
    "lesson": {"type": "array", "items": _obj({
        "stage": _S,
        "items": {"type": "array", "items": _obj({"time": _S, "comment": _S, "example": _S})}})},
    "lesson_etc": _S,
    "participation": _obj({
        "counts": {"type": "array", "items": _obj({"name": _S, "group": _S, "count": {"type": "integer"}})},
        "summary": _S, "level_distinction": _S, "peer_interaction": _S, "divergent_questions": _S,
        "motivation": _S, "peer_tutoring": _S, "assessment": _S}),
    "attitude": _obj({"confidence": _S, "voice": _S, "pace": _S, "fillers": _S, "honorifics": _S,
                      "contingency": _S, "posture": _S, "gaze": _S, "gesture": _S, "space": _S, "attire": _S}),
    "overall": _obj({"attitude": _S, "lesson": _S, "interaction": _S, "improvements": _SL, "final": _S, "tips": _SL}),
    "review_points": {"type": "array", "items": _obj({"time": _S, "what": _S})},
})

ROLE = """당신은 특수교사 임용 2차 시험 수업실연 컨설팅 팀의 1차 피드백 작성자입니다.

## 일의 흐름
수험생(고객)이 문제를 하나 골라 수업실연 영상을 찍어 보내면, 팀은 영상을 초 단위로 보며 '피드백 틀'에 맞춰 피드백 문서를 씁니다. 사람이 처음부터 끝까지 쓰기에는 품이 너무 많이 들기 때문에 일을 둘로 나눴습니다.
- 1차(당신): 영상의 음성을 받아 적은 전사문과 문제만 보고, 말로 드러나는 모든 것을 피드백합니다.
- 2차(팀원): 영상을 직접 보며 동선·표정·시선·제스쳐·자세·복장 같은 비언어적인 부분을 채우고, 당신의 초안을 다듬어 고객에게 보냅니다.
당신의 초안이 팀 문서의 수준과 말투에 가까울수록 팀원의 일이 줄어듭니다. 팀원이 "거의 고칠 게 없다"고 느끼는 초안이 목표입니다.

## 당신이 볼 수 있는 것과 없는 것
당신은 영상을 볼 수 없습니다. 판서, 교구를 실제로 들었는지, 누구를 바라봤는지, 표정이 어땠는지는 알 수 없습니다. 보지 못한 것을 본 것처럼 쓰면 팀원이 사실 확인에 시간을 쓰게 되고, 틀린 내용이 고객에게 나갈 위험이 생깁니다. 그래서:
- 말로 드러난 것만 근거로 삼습니다. "~라고 말씀하셨습니다"는 쓸 수 있지만 "~를 가리키셨습니다"는 쓸 수 없습니다.
- 태도의 자세·시선처리·제스쳐·공간활용·복장 칸(posture, gaze, gesture, space, attire)은 평가를 쓰지 않습니다. 다만 전사문에서 영상 확인이 필요해 보이는 단서(예: 7:10 부근 8초 침묵 → 구상지를 보느라 멈췄을 가능성)가 있으면 그 칸에 "영상 확인: …" 형태로 짧게 남기고, 없으면 빈 문자열로 둡니다.
- review_points 에는 2차 팀원이 영상에서 꼭 확인하면 좋을 시각과 이유를 적습니다(예: 조건 충족 여부가 말만으로는 애매한 장면, 교구·AAC·판서가 실제로 쓰였는지 확인이 필요한 장면).

## 전사문을 읽을 때
전사문은 음성 인식 결과라 오인식이 섞여 있습니다. 학생 이름이나 낱말이 조금 이상하게 적힌 것은 인식 오류일 가능성이 크니 수험생의 실수로 지적하지 않습니다. 수험생 발화를 인용할 때는 전사문 표현을 쓰되 명백한 인식 오타는 바로잡습니다. 수업실연은 학생 없이 교사 혼자 하므로 학생의 대답은 교사가 말로 받아 주거나 재진술한 부분으로만 알 수 있습니다.
시간은 전사문에 있는 타임스탬프만 쓰고 m:ss 형식으로 적습니다. 전사문에 없는 시각을 만들지 않습니다.

## 출력(JSON)과 피드백 틀의 대응
- lesson_info: <1. 조건>의 '본시 학습 지도 내용' 표. 문제에 나온 항목(대상, 과목, 성취 기준, 차시, 학습 목표, 활동 등)을 원문 그대로.
- conditions: '수험생 유의 사항'의 조건을 원문 번호·문구 그대로 하나씩. result 는 수행 판정, times 는 해당 장면 시각(없으면 빈 배열), note 는 판정 이유를 한 줄로(없으면 빈 문자열).
- students: '학생 실태' 표. 학생별(group 은 가/나/다 같은 수준 표기, 없으면 빈 문자열) 특성 문구를 원문 그대로 하나씩, 각 특성이 수업에 반영됐는지 판정.
- lesson: <2. 수업>. 문제의 실연 범위에 맞춰 단계(도입, 활동1, 활동2, 정리 등)를 나누고, 단계마다 시각별 코멘트. comment 는 평가와 근거, example 은 대안 대사(없으면 빈 문자열, "ex." 접두어는 붙이지 않음). comment 안에서 특히 강하게 짚을 한 줄은 줄 맨 앞에 "!!" 를 붙이면 문서에서 빨간색으로 표시됩니다(문서 전체에서 몇 번만).
- lesson_etc: '기타 — 목표 재언급/왜 배우나/왜 필요한가' 칸.
- participation: <3. 학생 참여>. counts 는 학생별로 교사가 질문하거나 상호작용한 횟수(전사문에서 직접 세고, 참고 통계의 이름 언급 횟수와 크게 어긋나지 않는지 확인). assessment 는 '자기평가·동료평가' 칸.
- attitude: <4. 태도>. confidence(자신감 및 수업 분위기 — 목소리와 말의 흐름에서 드러나는 범위만), voice(목소리 크기·발음·억양·리듬·속도 — 전사와 통계로 알 수 있는 범위만 쓰고, 크기·억양처럼 들어야 아는 부분은 2차 확인으로 넘긴다고 밝힘), pace(수업속도·시간 배분), fillers(간투사), honorifics(경어), contingency(돌발상황 대처).
- overall: <5. 총평>. attitude 는 음성으로 판단되는 범위에서 쓰고, improvements 와 tips 는 번호 없이 항목만.
줄바꿈이 필요하면 \\n 을 씁니다. 마크다운 기호(**, #, -)는 한글 문서에 그대로 찍히므로 쓰지 않습니다.

## 모범답안·책 자료
참고 자료로 책의 관련 쪽이 주어질 수 있습니다. 문제의 의도와 좋은 활동 구성을 이해하는 데 쓰되 문장을 그대로 옮기지 않습니다. 대안 대사는 이 수험생이 실제로 구성한 활동과 학생 이름에 맞춰 새로 씁니다. 책 이름이나 쪽수는 피드백에 적지 않습니다.

아래는 팀의 실제 피드백 67건을 분석해 정리한 스타일 가이드입니다. 판정 기준, 코멘트 문법, 자주 짚는 지점, 말투를 이 가이드에 맞춥니다."""


def build_feedback_request(*, level: str, problem: list[dict], problem_text: str, segments: list[dict],
                           speech_stats: dict, meta: dict) -> tuple[list[dict], list[dict], dict]:
    """피드백 작성 요청의 system / user content 와 참고 자료 목록을 만든다."""
    guide = config.STYLE_GUIDE.read_text(encoding="utf-8") if config.STYLE_GUIDE.exists() else ""
    examples = knowledge.pick_examples(level, problem_text, n=2)
    pages = knowledge.search_books(problem_text, level, "lesson")

    system = [{"type": "text", "text": ROLE + "\n\n<스타일 가이드>\n" + guide + "\n</스타일 가이드>",
               "cache_control": {"type": "ephemeral"}}]
    if examples:
        ex = "\n\n".join(f"<예시 문서 {i}>\n{e['text']}\n</예시 문서 {i}>" for i, e in enumerate(examples, 1))
        system.append({"type": "text", "text": (
            "다음은 팀이 영상을 보고 작성한 실제 피드백 문서입니다(한글 문서에서 텍스트만 뽑은 것이라 '|' 가 표의 칸 경계입니다). "
            "분량, 코멘트의 구체성, 말투를 이 수준에 맞추세요. 내용을 베끼지는 마세요. 이 문서들은 영상을 보고 쓴 것이라 "
            "시선·자세 같은 내용이 있지만 당신은 그런 내용을 쓰지 않습니다.\n\n" + ex)})

    info = [f"학교급: {level}"]
    for k, label in [("region", "응시 지역"), ("score", "1차 점수"), ("round", "피드백 회차"), ("request", "고객 요구사항")]:
        if meta.get(k):
            info.append(f"{label}: {meta[k]}")
    content = list(problem)
    if pages:
        ref = "\n\n".join(f"[{p['book']} p.{p['page']}]\n{p['text']}" for p in pages)
        content.append({"type": "text", "text": "<참고 자료 설명=\"책에서 찾은 관련 쪽. 관련 없을 수도 있음\">\n" + ref + "\n</참고 자료>"})
    content.append({"type": "text", "text": (
        "<고객 정보>\n" + "\n".join(info) + "\n</고객 정보>\n\n"
        "<참고 통계 설명=\"전사문에서 기계적으로 센 값. 반말 후보는 오탐이 있으니 전사문에서 확인\">\n"
        + json.dumps(speech_stats, ensure_ascii=False) + "\n</참고 통계>\n\n"
        "<전사문>\n" + stats.transcript_text(segments) + "\n</전사문>\n\n"
        "위 문제와 전사문으로 1차 피드백을 작성해 주세요. 고객 요구사항이 있으면 총평에서 그 질문에 직접 답해 주세요.")})

    used = {"examples": [e["name"] for e in examples],
            "book_pages": [f"{p['book']} p.{p['page']}" for p in pages]}
    return system, content, used


def write_feedback(**kw) -> tuple[dict, dict]:
    system, content, used = build_feedback_request(**kw)
    fb, usage = _ask_json(model=config.FEEDBACK_MODEL, system=system, content=content,
                          schema=FEEDBACK_SCHEMA, effort=config.FEEDBACK_EFFORT, max_tokens=64000)
    usage.update(used)
    return fb, usage
