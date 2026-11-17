"""영상 → 음성(모노 16kHz mp3). 영상 전체를 AI에 보내지 않고 음성만 써서 비용을 줄인다."""
import json
import subprocess
from pathlib import Path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120)
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def extract_audio(src: Path, dst: Path) -> float:
    """src(영상/음성) → dst(.mp3). 20분 영상이 약 7MB가 된다. 길이(초)를 돌려준다."""
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libmp3lame", "-b:a", "48k", str(dst)],
        capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError("음성 추출 실패(ffmpeg): " + r.stderr[-400:])
    return probe_duration(dst)


def download_audio(url: str, dst_dir: Path) -> Path:
    """YouTube 등 링크에서 음성만 내려받는다(yt-dlp). 일부공개 링크는 가능, 비공개는 불가."""
    out = dst_dir / "source.%(ext)s"
    err = ""
    # YouTube 는 일반(https) 음성 형식을 403 으로 막는 경우가 있어, 실패하면 HLS(m3u8) 음성으로 다시 받는다.
    for fmt in ("bestaudio/best", "233/234/bestaudio[protocol*=m3u8]/best[protocol*=m3u8]/worst"):
        for f in dst_dir.glob("source.*"):
            f.unlink(missing_ok=True)
        r = subprocess.run(
            ["yt-dlp", "--no-playlist", "-f", fmt, "-o", str(out), url],
            capture_output=True, text=True, timeout=1800)
        files = [f for f in sorted(dst_dir.glob("source.*")) if not f.name.endswith(".part") and f.stat().st_size > 1000]
        if r.returncode == 0 and files:
            return files[0]
        err = r.stderr[-400:]
    raise RuntimeError("영상 링크에서 음성을 받지 못했습니다(yt-dlp). 영상 파일을 직접 올려 주세요: " + err)
