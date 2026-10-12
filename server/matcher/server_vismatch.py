"""Image matcher server, vismatch edition: any vismatch matcher (default matchanything-roma) on one GPU.

Same HTTP contract as server.py (the transformers ELoFTR server), so fit/matchfit.py in ancient-works can
point MATCHER_URL at either:

POST /match  {"image0": <base64 PNG/JPEG>, "image1": <base64>, "max_side": 832}
  -> {"keypoints0": [[x, y], ...], "keypoints1": [[x, y], ...], "scores": [...], "size0": [w, h], "size1": [w, h]}
     keypoints are in the ORIGINAL pixel frame of each image.
GET  /health -> {"model": ..., "device": ...}

env: MATCHER_NAME (vismatch name, default matchanything-roma), MATCHER_PORT (default 8092), CUDA_VISIBLE_DEVICES.
"""
import base64, io, json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
from PIL import Image
from vismatch import get_matcher

NAME = os.environ.get("MATCHER_NAME", "matchanything-roma")
PORT = int(os.environ.get("MATCHER_PORT", "8092"))
DEV = "cuda" if torch.cuda.is_available() else "cpu"
matcher = get_matcher(NAME, device=DEV)
lock = threading.Lock()


def load(b64, max_side):
    im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    w, h = im.size
    s = min(1.0, max_side / max(w, h))
    small = im.resize((max(8, round(w * s)), max(8, round(h * s))), Image.BILINEAR) if s < 1 else im
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    small.save(f.name)
    try:
        t = matcher.load_image(f.name, resize=None)
    finally:
        os.unlink(f.name)
    # the tensor the matcher sees: keypoints come back in its frame
    th, tw = t.shape[-2], t.shape[-1]
    return t, (w, h), (tw / w, th / h)


def match(req):
    max_side = int(req.get("max_side", 832))
    a, size0, s0 = load(req["image0"], max_side)
    b, size1, s1 = load(req["image1"], max_side)
    with lock, torch.inference_mode():
        r = matcher(a, b)
    k0 = np.asarray(r["matched_kpts0"], float).reshape(-1, 2)
    k1 = np.asarray(r["matched_kpts1"], float).reshape(-1, 2)
    conf = r.get("matched_confidences")
    conf = [float(c) for c in np.asarray(conf).reshape(-1)] if conf is not None else [1.0] * len(k0)
    k0 = k0 / np.array(s0)
    k1 = k1 / np.array(s1)
    return {"keypoints0": k0.round(2).tolist(), "keypoints1": k1.round(2).tolist(), "scores": conf,
            "size0": list(size0), "size1": list(size1)}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"model": NAME, "device": torch.cuda.get_device_name(0) if DEV == "cuda" else "cpu"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/match":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", "0"))
        try:
            req = json.loads(self.rfile.read(n))
            self._send(200, match(req))
        except Exception as e:
            self._send(500, {"error": repr(e)})


if __name__ == "__main__":
    print("matcher %s on %s, port %d" % (NAME, DEV, PORT), flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
