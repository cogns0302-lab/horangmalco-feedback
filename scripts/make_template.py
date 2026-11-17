"""피드백 틀(HWPX)에서 '빈 템플릿'을 만든다.

사용법:  python scripts/make_template.py <원본 피드백.hwpx> server/assets/template.hwpx

원본의 스타일(header.xml: 글꼴·문단·표 테두리)만 가져오고, 본문 내용·이미지·미리보기는 모두 버린다.
section0.xml 에는 용지 설정이 들어 있는 첫 문단만 남긴다(런타임에 server/pipeline/hwpx.py 가 본문을 채운다).
"""
import re
import sys
import zipfile


def main(src: str, dst: str) -> None:
    zin = zipfile.ZipFile(src)
    sec = zin.read("Contents/section0.xml").decode("utf-8")
    head_end = sec.index("<hp:p ")
    first_end = sec.index("</hp:p>", head_end) + len("</hp:p>")
    first_p = sec[head_end:first_end]
    # 첫 문단의 제목 글자와 줄 배치 캐시는 런타임에 다시 쓴다.
    first_p = re.sub(r"<hp:t>.*?</hp:t>", "<hp:t>{{TITLE}}</hp:t>", first_p, flags=re.S)
    first_p = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", first_p, flags=re.S)
    skeleton = sec[:head_end] + first_p + "{{BODY}}</hs:sec>"

    hpf = zin.read("Contents/content.hpf").decode("utf-8")
    hpf = re.sub(r'<opf:item id="image\d+"[^>]*/>', "", hpf)
    hpf = re.sub(r"<opf:title>.*?</opf:title>", "<opf:title>수업실연 피드백</opf:title>", hpf, flags=re.S)
    hpf = re.sub(r'(<opf:meta name="lastsaveby" content="text">).*?(</opf:meta>)', r"\1feedback-app\2", hpf, flags=re.S)

    with zipfile.ZipFile(dst, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"), compress_type=zipfile.ZIP_STORED)
        for name in zin.namelist():
            if name == "mimetype" or name.startswith("BinData/") or name == "Preview/PrvImage.png":
                continue
            if name == "Contents/section0.xml":
                data = skeleton.encode("utf-8")
            elif name == "Contents/content.hpf":
                data = hpf.encode("utf-8")
            elif name == "Preview/PrvText.txt":
                data = "수업실연 피드백".encode("utf-8")
            else:
                data = zin.read(name)
            zout.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
