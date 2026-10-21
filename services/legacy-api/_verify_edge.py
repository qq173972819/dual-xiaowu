import httpx, io
from PIL import Image

BASE = "http://127.0.0.1:8080"
url = BASE + "/api/uploads/dd7dc647-dcec-4e97-93a4-07fd54bb1e28"
params = {
    "persona": "MY_ADAPTIVE",
    "w": 648, "h": 480, "colors": 4,
    "no_cache": "1",
    "mode_override": '{"image_url":"' + url + '"}',
}
r = httpx.get(BASE + "/api/preview", params=params, timeout=180)
print("status:", r.status_code, "bytes:", len(r.content))
if r.status_code != 200:
    print(r.text[:500])
    exit(1)

img = Image.open(io.BytesIO(r.content)).convert("RGB")
w, h = img.size
print("size:", w, h)

# Right edge 2px
rc = img.crop((w - 2, 0, w, h))
rc_set = {rc.getpixel((x, y)) for y in range(h) for x in range(2)}
all_white_r = all(c == (255, 255, 255) for c in rc_set)
print(f"right 2px: all_white={all_white_r}, unique_colors={len(rc_set)}")

# Left edge 2px
lc = img.crop((0, 0, 2, h))
lc_set = {lc.getpixel((x, y)) for y in range(h) for x in range(2)}
all_white_l = all(c == (255, 255, 255) for c in lc_set)
print(f"left 2px: all_white={all_white_l}, unique_colors={len(lc_set)}")

img.save("_verify_edge.png")
print("saved _verify_edge.png")
