"""쪽 미리보기(PNG)를 줄여 한 장 또는 여러 장 붙임으로 만든다. 사용: preview.py <out.jpg> <쪽...> [--w=900]"""
import sys
from pathlib import Path
from PIL import Image
args = [a for a in sys.argv[1:] if not a.startswith("--")]
w = int(next((a[4:] for a in sys.argv if a.startswith("--w=")), 900))
src = Path(__file__).parent / "build" / "png"
ims = [Image.open(src / f"p{int(n):02d}.png").convert("RGB") for n in args[1:]]
ims = [im.resize((w, round(im.height * w / im.width)), Image.LANCZOS) for im in ims]
sheet = Image.new("RGB", (sum(i.width for i in ims) + 12 * (len(ims) - 1), max(i.height for i in ims)), (60, 60, 60))
x = 0
for im in ims:
    sheet.paste(im, (x, 0)); x += im.width + 12
sheet.save(args[0], quality=90)
print(args[0], sheet.size)
