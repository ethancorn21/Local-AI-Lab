#!/opt/webtools/bin/python
"""fetch.py URL OUT : download a web page and save its readable text to OUT (for research.ts web_fetch).

HTML -> main content as markdown (trafilatura; boilerplate like navigation dropped, code blocks kept),
PDF -> pdftotext, text/JSON/XML/code -> as is. github.com/.../blob/... URLs are fetched raw.
Prints one JSON line: {"url", "title", "type", "chars"} or {"error"}.
"""
import json, re, subprocess, sys, urllib.request

MAX_BYTES = 8 * 1024 * 1024
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def main():
    url, out = sys.argv[1], sys.argv[2]
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
    if m:
        url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain,application/pdf,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        final, ctype = r.geturl(), (r.headers.get("Content-Type") or "").lower()
        body = r.read(MAX_BYTES + 1)[:MAX_BYTES]
    title = ""
    if "pdf" in ctype or body[:5] == b"%PDF-":
        text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=body, capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        kind = "pdf"
    elif "html" in ctype or body.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html")):
        import trafilatura
        html = body.decode(r.headers.get_content_charset() or "utf-8", "replace")
        text = trafilatura.extract(html, url=final, output_format="markdown", include_formatting=True,
                                   include_tables=True, include_links=False, include_comments=False, favor_recall=True)
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta else "") or ""
        if not text:  # extraction found no main content: fall back to all visible text
            text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
        kind = "html"
    else:
        text = body.decode("utf-8", "replace")
        kind = ctype.split(";")[0] or "text"
    open(out, "w").write(text)
    print(json.dumps({"url": final, "title": title.strip(), "type": kind, "chars": len(text)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # reported to the agent as a tool error
        print(json.dumps({"error": f"{type(e).__name__}: {e}"[:500]}))
