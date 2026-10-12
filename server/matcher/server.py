"""Image matcher server: MatchAnything-ELoFTR (zju-community/matchanything_eloftr, Apache-2.0) on the RTX 5060 Ti.

Cross-modality dense matching (drawing vs map vs aerial). Stdlib HTTP, one model loaded once.

POST /match  {"image0": <base64 PNG/JPEG>, "image1": <base64>, "threshold": 0.2, "max_side": 832}
  -> {"keypoints0": [[x, y], ...], "keypoints1": [[x, y], ...], "scores": [...], "size0": [w, h], "size1": [w, h]}
     keypoints are in the ORIGINAL pixel frame of each image.
GET  /health -> {"model": ..., "device": ...}
"""
import base64, io, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForKeypointMatching

MODEL = os.environ.get("MATCHER_MODEL", "zju-community/matchanything_eloftr")
PORT = int(os.environ.get("MATCHER_PORT", "8091"))
DEV = "cuda" if torch.cuda.is_available() else "cpu"
proc = AutoImageProcessor.from_pretrained(MODEL)
model = AutoModelForKeypointMatching.from_pretrained(MODEL).to(DEV).eval()
lock = threading.Lock()  # one match at a time on the card


def load(b64, max_side):
    im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    w, h = im.size
    s = min(1.0, max_side / max(w, h))
    small = im.resize((max(8, round(w * s)), max(8, round(h * s))), Image.BILINEAR) if s < 1 else im
    return small, (w, h), s


def match(req):
    max_side = int(req.get("max_side", 832))
    a, size0, s0 = load(req["image0"], max_side)
    b, size1, s1 = load(req["image1"], max_side)
    inputs = proc([[a, b]], return_tensors="pt").to(DEV)
    with lock, torch.inference_mode():
        out = model(**inputs)
    res = proc.post_process_keypoint_matching(out, [[(a.height, a.width), (b.height, b.width)]],
                                              threshold=float(req.get("threshold", 0.2)))[0]
    k0 = (res["keypoints0"].float().cpu() / s0).tolist()
    k1 = (res["keypoints1"].float().cpu() / s1).tolist()
    return {"keypoints0": k0, "keypoints1": k1, "scores": res["matching_scores"].float().cpu().tolist(),
            "size0": list(size0), "size1": list(size1)}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"model": MODEL, "device": torch.cuda.get_device_name(0) if DEV == "cuda" else "cpu"})
        self._send(404, {"error": "GET /health or POST /match"})

    def do_POST(self):
        if self.path != "/match":
            return self._send(404, {"error": "POST /match"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            t = time.time(); r = match(req); r["seconds"] = round(time.time() - t, 3)
            self._send(200, r)
        except Exception as e:  # a bad request must not kill the server
            self._send(400, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    print(f"matcher {MODEL} on {DEV} at 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
