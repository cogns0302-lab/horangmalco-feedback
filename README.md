# 호랑말코 2차 피드백 (수업실연 · 면접)

특수교사 임용 2차 수업실연 영상에서 **음성만 추출**해 전사하고, 팀의 피드백 틀에 맞춘 **1차 피드백 초안과 한글 파일(.hwpx)** 을 만드는 비공개 웹앱입니다. 동선·표정·시선·제스쳐 등 비언어 항목은 `[2차 영상 검토]` 로 비워 두어 사람이 채웁니다.

## 처리 흐름

1. 영상 업로드(5MB 분할 업로드) 또는 YouTube 링크 → `ffmpeg` 로 음성만 추출(20분 ≈ 7MB), 원본 영상은 삭제
2. 문제(사진·PDF·한글·텍스트)에서 학생 이름·교과 용어를 뽑아 음성 인식 힌트로 사용
3. 설정된 STT를 **동시에** 호출(CLOVA Speech · 리턴제로 · OpenAI · ElevenLabs 중 키가 있는 것)
4. 여러 전사문을 교차 검증해 하나로 병합(반말·간투사는 그대로 보존)
5. 스타일 가이드 + 같은 학교급의 실제 피드백 예시 2건 + 책의 관련 쪽을 참고해 피드백 작성
6. 피드백 틀과 같은 표 구성의 `.hwpx` 생성

각 단계 결과는 `data/jobs/<id>/` 에 남아서, "피드백만 다시 작성"은 STT 비용이 다시 들지 않습니다.

## 실행

```bash
cp .env.example .env        # 관리자 계정, API 키 입력
pip install -r requirements.txt   # ffmpeg 필요
python scripts/sync_knowledge.py  # 드라이브의 피드백·책을 학습 자료로 정리 (data/knowledge)
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Docker: `docker build -t feedback . && docker run -p 8000:8000 --env-file .env -v feedback-data:/data feedback`
(컨테이너 안에서 한 번 `python scripts/sync_knowledge.py` 실행)

- 작업 큐가 프로세스 안에 있으므로 **인스턴스 1개, 영속 디스크(`DATA_DIR`)** 로 운영합니다.
- 반드시 HTTPS 뒤에서 운영합니다(`COOKIE_SECURE=1`).

## 접근 권한

- `.env` 의 `ADMIN_EMAIL` / `ADMIN_PASSWORD` 로 관리자 계정이 만들어집니다.
- 다른 사람은 `/signup` 에서 사용 신청 → 관리자가 `회원 승인` 화면에서 승인해야 로그인됩니다. 차단은 즉시 반영됩니다.
- 검색 엔진 차단(robots, noindex), 로그인 시도 제한, CSRF 토큰, 세션 쿠키 서명.

## 학습 자료

| 위치 | 내용 |
|---|---|
| `knowledge/style_guide.md` | 팀의 실제 피드백 67건을 분석해 만든 스타일 가이드(판정 기준, 코멘트 문법, 반복 지적, 말투) |
| `data/knowledge/examples/<유특·초특·중특>/` | 실제 피드백 문서(텍스트). 같은 학교급·같은 과목 문서를 골라 예시로 사용 |
| `data/knowledge/books/` | 책 본문. 문제와 관련된 쪽을 검색해 참고(문장은 베끼지 않도록 지시) |

`data/` 는 고객 정보와 저작물이 들어 있어 git 에 올리지 않습니다. 새 책(예: 중등특수)을 드라이브 폴더에 넣은 뒤 `python scripts/sync_knowledge.py` 를 다시 실행하면 반영됩니다.

## 구성

```
server/main.py            화면·로그인·승인·업로드
server/jobs.py            작업 큐와 단계별 처리
server/pipeline/audio.py  음성 추출
server/pipeline/stt.py    STT 제공자(다중)
server/pipeline/llm.py    문제 분석 · 전사 병합 · 피드백 작성(Claude)
server/pipeline/hwpx.py   한글 파일 생성
server/pipeline/knowledge.py  예시·책 검색
server/assets/template.hwpx   피드백 틀의 스타일만 남긴 빈 템플릿 (scripts/make_template.py 로 재생성)
scripts/dev_mock.py       API 키 없이 화면 흐름만 확인하는 개발용 실행기
```

면접은 피드백 틀 확정 전이라 버튼과 안내 화면만 있습니다.
