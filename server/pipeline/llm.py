"""Claude 호출: ① 문제 분석 ② 여러 STT 결과 병합 ③ 피드백 작성.

모두 구조화 출력(JSON 스키마)을 쓰고, 긴 출력에 대비해 스트리밍으로 받는다.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import anthropic

from .. import config
from . import hwp_text, knowledge, problembank, stats

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


def _fit(obj, schema):
    """모델이 낸 JSON 을 스키마 모양에 맞춘다(빠진 칸은 빈 값, 남는 칸은 버림)."""
    t = schema.get("type")
    if t == "object":
        obj = obj if isinstance(obj, dict) else {}
        return {k: _fit(obj.get(k), s) for k, s in schema["properties"].items()}
    if t == "array":
        return [_fit(x, schema["items"]) for x in obj] if isinstance(obj, list) else []
    if t == "integer":
        try:
            return int(obj)
        except (TypeError, ValueError):
            return 0
    v = "" if obj is None else obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    if "enum" in schema and v not in schema["enum"]:
        v = {"O": "○", "o": "○", "ㅇ": "○", "X": "×", "x": "×"}.get(v.strip(), v.strip())
    if "enum" in schema and v not in schema["enum"]:
        return "-" if "-" in schema["enum"] else schema["enum"][0]
    return v


def _ask_json(*, model: str, system, content, schema: dict, effort: str, max_tokens: int,
              strict: bool = True) -> tuple[dict, dict]:
    """strict=False: 구조화 출력 대신 지시문으로 JSON 을 받는다. 스키마가 커서 API 가
    'compiled grammar is too large' 로 거절하는 피드백 작성에 쓴다."""
    if not strict:
        note = ("\n\n## 출력 형식\n아래 JSON 스키마에 맞는 JSON 객체 하나만 출력합니다. 앞뒤에 설명이나 ``` 를 붙이지 않습니다.\n"
                + json.dumps(schema, ensure_ascii=False))
        system = (list(system) if isinstance(system, list) else [{"type": "text", "text": system}]) \
            + [{"type": "text", "text": note}]
    kwargs = dict(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, **({"format": {"type": "json_schema", "schema": schema}} if strict else {})},
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
    text = "".join(b.text for b in msg.content if b.type == "text")
    if strict:
        return json.loads(text), _usage(msg)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < i:
        raise RuntimeError("AI 출력에서 JSON 을 찾지 못했습니다. 다시 시도해 주세요.")
    return _fit(json.loads(text[i:j + 1]), schema), _usage(msg)


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


def _image_block(f: Path) -> dict:
    ext = f.suffix.lower()[1:]
    mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/" + ext)
    return {"type": "image", "source": {"type": "base64", "media_type": mt,
                                        "data": base64.standard_b64encode(f.read_bytes()).decode()}}


def bank_blocks(bank: dict) -> list[dict]:
    """문제 은행에서 고른 문제 → 입력 블록. 책 쪽 그림이 있으면 그림으로, 없으면 글자 인식 텍스트로."""
    images = problembank.page_images(bank)
    name = f"{bank['instructor']} {bank['level']} {bank['kind']}문제 '{bank['label']}'"
    if images:
        note = (f"아래 그림은 {name} 이(가) 실린 책 쪽입니다. 같은 쪽에 앞뒤 다른 문제나 해설·후기가 함께 보일 수 있는데, "
                f"'{bank['label']}' 문제만 이번 수업실연 문제입니다.")
        return [{"type": "text", "text": note}] + [_image_block(f) for f in images[:6]]
    return [{"type": "text", "text": (
        f"<문제 출처=\"{name}\" 설명=\"스캔본을 글자 인식한 텍스트라 오자가 섞여 있습니다. 문맥으로 바로잡아 읽으세요. "
        f"앞뒤 다른 문제가 섞여 있으면 '{bank['label']}' 문제만 대상으로 합니다.\">\n{bank['text'][:12000]}\n</문제>")}]


def problem_blocks(problem_text: str, files: list[Path], bank: dict | None = None) -> list[dict]:
    """문제(문제 은행/텍스트/이미지/PDF/HWP)를 Claude 입력 블록으로 바꾼다."""
    blocks: list[dict] = bank_blocks(bank) if bank else []
    texts = [problem_text.strip()] if problem_text.strip() else []
    for f in files:
        ext = f.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            blocks.append(_image_block(f))
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
        "- problem_text: 문제 전체를 빠짐없이 그대로 옮겨 적으세요(표는 줄 단위로, 조건 번호와 학생 실태 문구는 원문 그대로). "
        "책 쪽 그림에 다른 문제나 해설·응시 후기가 함께 보이면 안내된 문제만 옮겨 적습니다.\n"
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
- 1차(당신): 영상의 음성을 받아 적은 전사문과 문제만 보고, 말로 드러나는 것을 피드백합니다.
- 2차(담당 크루): 영상을 직접 보며 동선·표정·시선·제스쳐·자세·복장 같은 비언어적인 부분과 화면 캡처가 들어가는 칸을 채우고, 당신의 초안을 다듬어 고객에게 보냅니다.
당신의 초안이 팀 문서의 수준과 말투에 가까울수록 크루의 일이 줄어듭니다. 크루가 "거의 고칠 게 없다"고 느끼는 초안이 목표입니다.

## 가장 무게를 두는 것: 문제와 조건을 말로 드러냈는가
채점관은 고개를 숙이고 채점하며 한 번만 듣습니다. 그래서 이 팀은 "수험생이 문제의 조건을 말로 분명히 언급하고 수행했는가"를 피드백의 중심에 둡니다. 당신의 피드백도 여기에 가장 많은 분량과 정성을 씁니다.
- 문제에 적힌 것을 빠짐없이 점검합니다: 수험생 유의 사항(수업 조건) 하나하나, 실연 범위(도입·전개·정리 중 어디까지), 학습 목표, 제시된 자료, 학생(유아) 실태의 특성 문장 하나하나, 개별화교육계획 목표.
- 조건마다 전사문에서 그 조건이 들리는 발화를 찾아 시각과 함께 근거로 삼습니다. 조건의 핵심 낱말(예: "협력 교수", "보편적 학습 설계", "개별화 목표")이나 그 조건임을 알 수 있는 구체적 행동이 말로 나왔는지를 봅니다. 조건을 모르는 사람이 그 대사만 듣고도 어떤 조건인지 알 수 있어야 ○입니다.
- 말로 드러나지 않았거나 약한 조건은 note 와 <2. 수업> 코멘트에서 "어느 장면에서 어떤 대사로 넣으면 되는지"를 대안 대사로 보여 줍니다. 대안 대사에는 조건의 낱말과 문제의 학생 이름·자료 이름을 그대로 살립니다.
- <2. 수업>의 코멘트는 조건·학생 특성·자료가 이행된(또는 이행됐어야 할) 장면을 우선해서 고르고, 총평의 개선사항도 누락되거나 약하게 드러난 조건부터 적습니다.

## 당신이 볼 수 있는 것과 없는 것
당신은 영상을 볼 수 없습니다. 판서, 교구를 실제로 들었는지, 누구를 바라봤는지, 표정이 어땠는지는 알 수 없습니다. 보지 못한 것을 본 것처럼 쓰면 크루가 사실 확인에 시간을 쓰게 되고, 틀린 내용이 고객에게 나갈 위험이 생깁니다. 그래서:
- 말로 드러난 것만 근거로 삼습니다. "~라고 말씀하셨습니다"는 쓸 수 있지만 "~를 가리키셨습니다"는 쓸 수 없습니다.
- 비언어적 요소와 화면 캡처가 필요한 칸은 피드백하지 않습니다. 태도의 자세·시선처리·제스쳐·공간활용·복장 칸(posture, gaze, gesture, space, attire)은 항상 빈 문자열로 두고, review_points 도 빈 배열로 둡니다. 이 칸들은 담당 크루가 영상을 보고 직접 채웁니다. 다른 칸에서도 표정·시선·몸짓·동선·목소리 크기와 억양처럼 보거나 들어야 아는 것은 평가하거나 추측하지 않습니다.

## 전사문을 읽을 때
전사문은 음성 인식 결과라 오인식이 섞여 있습니다. 학생 이름이나 낱말이 조금 이상하게 적힌 것은 인식 오류일 가능성이 크니 수험생의 실수로 지적하지 않습니다. 수험생 발화를 인용할 때는 전사문 표현을 쓰되 명백한 인식 오타는 바로잡습니다. 수업실연은 학생 없이 교사 혼자 하므로 학생의 대답은 교사가 말로 받아 주거나 재진술한 부분으로만 알 수 있습니다.
시간은 전사문에 있는 타임스탬프만 쓰고 m:ss 형식으로 적습니다. 전사문에 없는 시각을 만들지 않습니다.

## 출력(JSON)과 피드백 틀의 대응
- lesson_info: <1. 조건>의 '본시 학습 지도 내용' 표. 문제에 나온 항목(대상, 과목, 성취 기준, 차시, 학습 목표, 활동 등)을 원문 그대로.
- conditions: '수험생 유의 사항'의 조건을 원문 번호·문구 그대로 하나씩. result 는 수행 판정, times 는 해당 장면 시각(없으면 빈 배열), note 는 판정 이유를 한 줄로(없으면 빈 문자열, 문서에서 "→" 가 자동으로 붙으니 화살표 없이). 수행하지 않은 조건(×)은 times 를 비우고, 들어갔어야 할 자리는 note 에 적습니다.
- students: '학생 실태' 표. 학생별 특성 문구를 원문 그대로 하나씩, 각 특성이 수업에 반영됐는지 판정. name 은 문제 표기 그대로(장애 유형이 괄호로 붙어 있으면 "다준 (자폐, 지적)"처럼 함께). group 은 가/나/다 같은 수준 표기로, 문제에 명시가 없더라도 수행 수준 서술에서 높은 순서가 분명하면 가·나·다로 적고, 판단할 수 없으면 빈 문자열.
- lesson: <2. 수업>. 문제의 실연 범위에 맞춰 단계(도입, 활동1, 활동2, 정리 등)를 나누고, 단계마다 시각별 코멘트. comment 는 평가와 근거, example 은 대안 대사(없으면 빈 문자열, "ex." 접두어는 붙이지 않음). comment 안에서 특히 강하게 짚을 한 줄은 줄 맨 앞에 "!!" 를 붙이면 문서에서 빨간색으로 표시됩니다(문서 전체에서 몇 번만).
- lesson_etc: '기타 — 목표 재언급/왜 배우나/왜 필요한가' 칸.
- participation: <3. 학생 참여>. counts 는 학생별로 교사가 그 학생에게 질문·지시·피드백을 건넨 횟수입니다(전사문에서 직접 셉니다. 한 번의 주고받음은 1회로, 같은 장면에서 이어진 칭찬은 따로 세지 않고, 지원 인력에게 한 말 속에 이름만 나온 것은 세지 않습니다. name 은 괄호 없이 이름만). divergent_questions 끝의 "학생 반응 유도 예시"(심화 질문하기 등 네 가지) 상자는 문서에 자동으로 들어가니 쓰지 않습니다. assessment 는 '자기평가·동료평가' 칸.
- attitude: <4. 태도>. confidence(자신감 및 수업 분위기 — 말의 흐름에서 드러나는 범위만), voice(전사와 통계로 알 수 있는 속도·말의 끊김·문장 끝맺음만. 크기·억양처럼 들어야 아는 부분은 쓰지 않음), pace(수업속도·시간 배분), fillers(간투사), honorifics(경어), contingency(돌발상황 대처). posture·gaze·gesture·space·attire 는 빈 문자열.
- overall: <5. 총평>. attitude 는 말로 판단되는 범위에서 쓰고, improvements 와 tips 는 번호 없이 항목만.
- review_points: 빈 배열.
줄바꿈이 필요하면 \\n 을 씁니다. 마크다운 서식(**굵게**, # 제목, 줄 머리의 - 나 * 불릿)은 한글 문서에 기호가 그대로 찍히므로 쓰지 않습니다. 문장 안의 "->" 나 "학생-학생" 같은 하이픈은 괜찮습니다.

모든 칸은 그대로 고객에게 전달돼도 되는 문장으로 씁니다. "영상 확인 후 덧붙이겠습니다" 같은 내부 메모는 넣지 않습니다. 말만으로는 판정할 수 없는 조건(판서·기자재를 실제로 썼는지 등)은 수행을 "-" 로 두고 note 에 "말로는 확인되지 않음. …라고 말로도 드러내면 좋음"처럼 말로 드러낼 방법을 적습니다.

## 모범답안 · 책 자료 · 지난 피드백
참고 자료로 ① 같은 문제에 대한 강사 책의 예시 답안·스크립트·해설(여러 책에 실린 경우 여러 개) ② 책에서 찾은 관련 쪽 ③ 팀이 지난해에 쓴 실제 피드백 문서가 주어질 수 있습니다.
- 같은 문제의 예시 답안이 있으면, 그 답안들이 각 조건을 어떤 대사와 활동으로 드러내는지 먼저 파악하고, 수험생의 실연이 조건을 드러낸 방식과 견주어 봅니다. 예시 답안끼리 방식이 다르면 정답이 하나가 아니라는 뜻이니, 수험생의 방식이 조건을 충족하면 그대로 인정합니다.
- 문제의 의도와 좋은 활동 구성을 이해하는 데 쓰되 문장을 그대로 옮기지 않습니다. 대안 대사는 이 수험생이 실제로 구성한 활동과 학생 이름에 맞춰 새로 씁니다. 책 이름이나 쪽수, 강사 이름은 피드백에 적지 않습니다.
- 지난 피드백 문서는 판정의 눈높이와 말투를 맞추는 데 씁니다. 같은 문제를 다룬 문서가 있으면 팀이 그 문제에서 어떤 조건을 어떻게 판정했는지 참고합니다.

아래는 팀의 실제 피드백 67건을 분석해 정리한 스타일 가이드입니다. 판정 기준, 코멘트 문법, 자주 짚는 지점, 말투를 이 가이드에 맞춥니다."""


def model_answers(bank: dict | None, max_chars: int = 18000) -> list[dict]:
    """같은 문제에 대한 강사 책의 예시 답안·해설: ① 같은 책 이론편에 실린 예시 ② 문제 뒤에 이어지는 해설·스크립트·후기
    ③ 같은 기출을 실은 다른 강사 책의 해당 부분. 글자 인식 텍스트라 오자가 섞여 있다."""
    if not bank:
        return []
    out = []
    if bank.get("ref_text"):
        out.append({"source": f"{bank['instructor']} 책의 예시 답안", "text": bank["ref_text"]})
    if len(bank["pages"]) > 1:
        out.append({"source": f"{bank['instructor']} 책의 문제와 이어지는 해설", "text": bank["text"]})
    for s in problembank.siblings(bank):
        out.append({"source": f"{s['instructor']} 책에 실린 같은 기출({s['label']})과 해설·후기",
                    "text": (s.get("ref_text", "") + "\n" + s["text"]).strip()})
    share = max_chars // max(1, len(out))
    return [{"source": o["source"], "text": o["text"][:share]} for o in out]


def build_feedback_request(*, level: str, problem: list[dict], problem_text: str, segments: list[dict],
                           speech_stats: dict, meta: dict, bank: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    """피드백 작성 요청의 system / user content 와 참고 자료 목록을 만든다."""
    guide = config.STYLE_GUIDE.read_text(encoding="utf-8") if config.STYLE_GUIDE.exists() else ""
    title = " ".join(x for x in (meta.get("problem_title", ""), bank["label"] if bank else "") if x)
    examples = knowledge.pick_examples(level, problem_text, n=2, title=title, exclude=meta.get("customer", ""))
    answers = model_answers(bank)
    pages = knowledge.search_books(problem_text, level, "lesson", max_chars=14000 if not answers else 9000)

    system = [{"type": "text", "text": ROLE + "\n\n<스타일 가이드>\n" + guide + "\n</스타일 가이드>",
               "cache_control": {"type": "ephemeral"}}]
    if examples:
        ex = "\n\n".join(f"<지난 피드백 {i} 파일=\"{e['name']}\">\n{e['text']}\n</지난 피드백 {i}>"
                         for i, e in enumerate(examples, 1))
        system.append({"type": "text", "text": (
            "다음은 팀이 지난해 영상을 보고 작성한 실제 피드백 문서입니다(한글 문서에서 텍스트만 뽑은 것이라 '|' 가 표의 칸 경계입니다). "
            "분량, 코멘트의 구체성, 판정의 눈높이, 말투를 이 수준에 맞추세요. 내용을 베끼지는 마세요. 이 문서들은 영상을 보고 쓴 것이라 "
            "시선·자세 같은 내용이 있지만 당신은 그런 내용을 쓰지 않습니다.\n\n" + ex)})

    info = [f"학교급: {level}"]
    for k, label in [("region", "응시 지역"), ("score", "1차 점수"), ("round", "피드백 회차"), ("request", "고객 요구사항")]:
        if meta.get(k):
            info.append(f"{label}: {meta[k]}")
    content = list(problem)
    if answers:
        ref = "\n\n".join(f"<예시 {i} 출처=\"{a['source']}\">\n{a['text']}\n</예시 {i}>" for i, a in enumerate(answers, 1))
        content.append({"type": "text", "text": (
            "<같은 문제의 예시 답안·해설 설명=\"강사 책에서 이 문제를 다룬 부분. 스캔본을 글자 인식한 것이라 오자가 있고, "
            "앞뒤 다른 문제의 내용이 조금 섞여 있을 수 있음\">\n" + ref + "\n</같은 문제의 예시 답안·해설>")})
    if pages:
        ref = "\n\n".join(f"[{p['book']} p.{p['page']}]\n{p['text']}" for p in pages)
        content.append({"type": "text", "text": "<참고 자료 설명=\"책에서 찾은 관련 쪽. 관련 없을 수도 있음\">\n" + ref + "\n</참고 자료>"})
    content.append({"type": "text", "text": (
        "<고객 정보>\n" + "\n".join(info) + "\n</고객 정보>\n\n"
        "<참고 통계 설명=\"전사문에서 기계적으로 센 값. 반말 후보는 오탐이 있으니 전사문에서 확인\">\n"
        + json.dumps(speech_stats, ensure_ascii=False) + "\n</참고 통계>\n\n"
        "<전사문>\n" + stats.transcript_text(segments) + "\n</전사문>\n\n"
        "위 문제와 전사문으로 1차 피드백을 작성해 주세요. 문제의 조건·학생 실태·자료를 말로 분명히 언급하고 수행했는지를 "
        "가장 꼼꼼히 봐 주세요. 고객 요구사항이 있으면 총평에서 그 질문에 직접 답해 주세요.")})

    used = {"examples": [e["name"] for e in examples],
            "model_answers": [a["source"] for a in answers],
            "book_pages": [f"{p['book']} p.{p['page']}" for p in pages]}
    return system, content, used


def write_feedback(**kw) -> tuple[dict, dict]:
    system, content, used = build_feedback_request(**kw)
    fb, usage = _ask_json(model=config.FEEDBACK_MODEL, system=system, content=content,
                          schema=FEEDBACK_SCHEMA, effort=config.FEEDBACK_EFFORT, max_tokens=64000, strict=False)
    usage.update(used)
    return fb, usage
