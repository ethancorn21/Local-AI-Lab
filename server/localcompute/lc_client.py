"""lc_client: what the `lc` CLI and the MCP server (lc-mcp) share to reach the local-compute service (lcd.py).

Where the service is and who the caller is, in this order: the LC_URL and LC_TOKEN environment variables, then
~/.config/localcompute/url and ~/.config/localcompute/token (admin actions: ~/.config/localcompute/admin-token).
Without a url, http://127.0.0.1:8080 (on the AI box itself).
"""
import io, json, os, subprocess, tarfile, urllib.error, urllib.request

CONF = os.path.expanduser("~/.config/localcompute")
CACHE = os.path.expanduser("~/.cache/localcompute")
UPLOAD_MAX = 64 << 20
FILE_MAX = 2 << 20
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache",
             ".tox", ".next", "target"}


def conf(name, env):
    if os.environ.get(env):
        return os.environ[env].strip()
    try:
        return open(os.path.join(CONF, name)).read().strip()
    except FileNotFoundError:
        return None


class Client:
    def __init__(self, admin=False):
        self.url = (conf("url", "LC_URL") or "http://127.0.0.1:8080").rstrip("/")
        self.token = conf("admin-token", "LC_ADMIN_TOKEN") if admin else conf("token", "LC_TOKEN")
        if not self.token:
            raise SystemExit(f"no {'admin ' if admin else ''}token: put it in {CONF}/{'admin-token' if admin else 'token'}")

    def call(self, method, path, body=None, raw=None, timeout=900):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/gzip" if raw is not None else "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error")
            except ValueError:
                msg = e.reason
            raise RuntimeError(f"{e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"the service at {self.url} does not answer ({e.reason})") from None

    def wait(self, jid, seconds):
        """the job, waiting up to seconds for it to end (the server holds each request up to 10 minutes)"""
        import time
        end = time.time() + max(0, seconds)
        while True:
            left = int(end - time.time())
            j = self.call("GET", f"/jobs/{jid}" + (f"?wait={min(left, 600)}" if left > 0 else ""), timeout=660)
            if j["state"] in ("done", "failed", "cancelled") or time.time() >= end:
                return j


def files_of(root):
    """the files a job should see: git's tracked and unignored files when root is in a repository, else a walk
    that skips build and cache folders"""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout.decode()
        return sorted({f for f in out.split("\0") if f})
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    found = []
    for r, ds, fs in os.walk(root):
        ds[:] = sorted(d for d in ds if d not in SKIP_DIRS and not d.startswith("."))
        found += [os.path.relpath(os.path.join(r, f), root) for f in sorted(fs)]
    return found


def pack(path):
    """-> (the folder as .tar.gz bytes, files packed, files skipped as too large)"""
    root = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(root):
        raise RuntimeError(f"{path} is not a folder")
    buf, n, skipped = io.BytesIO(), 0, []
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for rel in files_of(root):
            full = os.path.join(root, rel)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            if os.path.getsize(full) > FILE_MAX:
                skipped.append(rel); continue
            t.add(full, arcname=rel, recursive=False); n += 1
    data = buf.getvalue()
    if len(data) > UPLOAD_MAX:
        raise RuntimeError(f"{path} packs to {len(data) >> 20} MB, over the {UPLOAD_MAX >> 20} MB limit: point at a subfolder")
    return data, n, skipped


def remember(jid, **kw):
    """what the caller's side knows about a job (the folder it came from), for later"""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, "jobs.json")
    try:
        d = json.load(open(p))
    except (FileNotFoundError, ValueError):
        d = {}
    d[jid] = kw
    json.dump(d, open(p, "w"), indent=1)


def recall(jid):
    try:
        return json.load(open(os.path.join(CACHE, "jobs.json"))).get(jid, {})
    except (FileNotFoundError, ValueError):
        return {}


def apply_command(folder, patch_file):
    """how to apply a job's patch to the folder it was made from"""
    try:
        top = subprocess.run(["git", "-C", folder, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True).stdout.strip()
        prefix = subprocess.run(["git", "-C", folder, "rev-parse", "--show-prefix"], capture_output=True, text=True,
                                check=True).stdout.strip().rstrip("/")
        return f"git -C {top} apply {'--directory=' + prefix + ' ' if prefix else ''}{patch_file}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"patch -p1 -d {folder} < {patch_file}"


def describe(j, folder=None):
    """a job as text for a person or a model: state, and its result once done (long parts saved to files)"""
    lines = [f"job {j['id']} ({j['kind']} on {j['setup']}): {j['state']}"]
    if j.get("progress"):
        lines.append(f"progress: {j['progress']}")
    if j.get("error"):
        lines.append(f"error: {j['error']}")
    r = j.get("result")
    if not r:
        return "\n".join(lines)
    os.makedirs(CACHE, exist_ok=True)
    if j["kind"] == "chat":
        u = r.get("usage") or {}
        lines.append(f"({u.get('prompt_tokens')} tokens in, {u.get('completion_tokens')} out, finish {r.get('finish_reason')})")
        lines += ["", r.get("content", "")]
    elif j["kind"] == "swarm":
        full = os.path.join(CACHE, f"{j['id']}.json")
        json.dump(r, open(full, "w"), indent=1)
        fs = r["findings"]
        head = (f"{len(fs)} findings from {r['reports']} reports over {r['files']} files, {r['lines']} lines, "
                f"{r['areas']} areas ({r['at_once']} scouts at once), {r['wall_s'] / 60:.1f} min. Brief: {r['brief']}")
        if r.get("verified"):
            head += f". Checked by main: {r.get('kept')} kept" + (" (check cut short)" if r["verified"] == "partial" else "")
        lines += [head, f"all of it: {full}", ""]
        for f in fs[:80]:
            v = f" [{f['verdict']}]" if f.get("verdict") else ""
            n = f" ({f['scouts']} scouts)" if f["scouts"] > 1 else ""
            lines.append(f"{f['path']}:{f['line']}{v}{n} {f['finding']}")
        if len(fs) > 80:
            lines.append(f"... {len(fs) - 80} more in {full}")
    elif j["kind"] == "implement":
        tries = ", ".join(f"{a['end']} after {a['turns']} turns" for a in r["attempts"])
        lines.append(f"tests {'PASS' if r['passed'] else 'never passed'}; attempts: {tries}")
        patches = [r["patch"]] if r.get("passed") else r.get("patches", [])
        for i, p in enumerate(patches):
            if not p:
                continue
            pf = os.path.join(CACHE, f"{j['id']}{'' if r.get('passed') else f'-attempt{i + 1}'}.patch")
            open(pf, "w").write(p)
            lines.append(f"patch: {pf} ({p.count(chr(10))} lines)")
            if folder:
                lines.append(f"apply: {apply_command(folder, pf)}")
        if r.get("summary"):
            lines.append(f"agent's summary: {r['summary']}")
        out = r.get("test_output") or (r.get("test_outputs") or [""])[-1]
        lines += ["", "end of the last test run:", out[-2500:]]
        if r.get("passed") and len(r["patch"]) < 12000:
            lines += ["", r["patch"]]
    return "\n".join(lines)
