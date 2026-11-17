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
    r = subprocess.run(
        ["yt-dlp", "--no-playlist", "-f", "bestaudio/best", "-o", str(out), url],
        capture_output=True, text=True, timeout=1800)
    files = sorted(dst_dir.glob("source.*"))
    if r.returncode != 0 or not files:
        raise RuntimeError("영상 링크에서 음성을 받지 못했습니다(yt-dlp): " + r.stderr[-400:])
    return files[0]
