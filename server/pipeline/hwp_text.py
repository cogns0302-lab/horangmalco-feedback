"""Extract plain text from .hwp (HWP5 OLE) and .hwpx files. Usage: hwp_text.py <src> -> stdout"""
import sys, zlib, struct, zipfile, re
import xml.etree.ElementTree as ET

def hwp5_text(path):
    import olefile
    ole = olefile.OleFileIO(path)
    hdr = ole.openstream("FileHeader").read()
    compressed = bool(hdr[36] & 1)
    secs = sorted([e for e in ole.listdir() if e[0] == "BodyText"], key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))
    out = []
    for s in secs:
        data = ole.openstream(s).read()
        if compressed: data = zlib.decompress(data, -15)
        i = 0
        while i < len(data):
            h = struct.unpack_from("<I", data, i)[0]; i += 4
            tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, (h >> 20) & 0xFFF
            if size == 0xFFF:
                size = struct.unpack_from("<I", data, i)[0]; i += 4
            rec = data[i:i+size]; i += size
            if tag == 67:  # HWPTAG_PARA_TEXT
                txt = []; j = 0
                while j + 1 < len(rec):
                    c = struct.unpack_from("<H", rec, j)[0]
                    if c in (1,2,3,11,12,14,15,16,17,18,21,22,23):  # extended/inline controls: 8 wchar
                        j += 16
                        if c == 11: txt.append("⁣")  # table/drawing anchor
                        continue
                    if c in (4,5,6,7,8,9,19,20):
                        j += 16
                        if c == 9: txt.append("\t")
                        continue
                    if c in (10, 13): j += 2; continue
                    if c == 24: txt.append("-")
                    elif c in (30, 31): txt.append(" ")
                    elif c >= 32: txt.append(chr(c))
                    j += 2
                out.append((level, "".join(txt)))
    return "\n".join(t for _, t in out)

def hwpx_text(path):
    z = zipfile.ZipFile(path)
    names = sorted([n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n)], key=lambda n: int(re.sub(r"\D", "", n)))
    out = []
    for n in names:
        root = ET.fromstring(z.read(n))
        def walk(el, buf):
            tag = el.tag.split("}")[-1]
            if tag == "t":
                buf.append("".join(el.itertext()))
                return
            if tag == "p":
                b = []
                for c in el: walk(c, b)
                out.append("".join(b)) if False else buf.append("\n" + "".join(b))
                return
            if tag == "tc": buf.append("\n|")
            for c in el: walk(c, buf)
        b = []
        walk(root, b)
        out.append("".join(b))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))

if __name__ == "__main__":
    p = sys.argv[1]
    t = hwpx_text(p) if p.lower().endswith(".hwpx") else hwp5_text(p)
    sys.stdout.buffer.write(t.encode("utf-8", "ignore"))
