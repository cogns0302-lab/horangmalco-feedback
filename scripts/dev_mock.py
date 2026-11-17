"""개발용: API 키 없이 화면과 작업 흐름을 확인한다. STT·AI 호출을 가짜 결과로 바꿔 서버를 띄운다.
(운영에서는 쓰지 않는다)   python scripts/dev_mock.py <샘플 피드백.json> [포트]
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402

from server.pipeline import llm, stt  # noqa: E402

sample = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
SEGS = [{"start": 4.0, "end": 9.0, "text": "안녕하십니까, 수업 실연 시작하겠습니다."},
        {"start": 24.0, "end": 31.0, "text": "여러분, 선생님이 오늘 영상을 하나 가지고 왔어요. 음, 같이 볼까요?"},
        {"start": 65.0, "end": 70.0, "text": "우리 다준이가 급식실 카드를 번쩍 들어주었어요."},
        {"start": 299.0, "end": 304.0, "text": "다준이는 실무사님과 함께하도록 합시다. 가영이 뭐가 나왔지?"}]


def fake_stt(audio, hints):
    time.sleep(2)
    return ([{"provider": "clova", "segments": SEGS, "text": " ".join(s["text"] for s in SEGS)},
             {"provider": "rtzr", "segments": SEGS, "text": ""}], {"openai": "HTTPError: 401 (테스트용 가짜 오류)"})


stt.transcribe_all = fake_stt
stt.available = lambda: ["clova", "rtzr"]
llm.analyze_problem = lambda blocks: ({"problem_text": "국어 급식실 도움 요청", "subject": "국어", "topic": "급식실",
                                       "student_names": ["가영", "나은", "다준"], "keywords": ["급식실"]},
                                      {"model": "claude-haiku-5-5", "input": 1500, "output": 800})
llm.merge_transcripts = lambda results, hints: (SEGS, {"model": "claude-haiku-5-5", "input": 9000, "output": 4000})


def fake_feedback(**kw):
    time.sleep(2)
    fb = dict(sample)
    fb.setdefault("review_points", [{"time": "4:59", "what": "실무사에게 역할을 안내할 때 실제로 실무사 쪽을 보고 말하는지 확인"},
                                    {"time": "1:05", "what": "그림 카드(AAC)를 실제로 들어 보였는지 확인"}])
    return fb, {"model": "claude-opus-5-5", "input": 60000, "output": 12000}


llm.write_feedback = fake_feedback
uvicorn.run("server.main:app", host="127.0.0.1", port=int(sys.argv[2]) if len(sys.argv) > 2 else 8000)
