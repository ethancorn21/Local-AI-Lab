"""score.py <sp> : which test categories caught each mutant (junit xml per mutant vs the clean baseline)."""
import json, sys, collections, os
import xml.etree.ElementTree as ET

SP = sys.argv[1]
pert = json.load(open(f"{SP}/data/pertest.json"))["pertest"]
muts = json.load(open(f"{SP}/mutants.json"))

def failed(xml):
    out, total = set(), 0
    for tc in ET.parse(xml).getroot().iter("testcase"):
        total += 1
        cls, name = tc.get("classname", ""), tc.get("name", "")
        tid = "tests/" + cls.split(".")[-1] + ".py::" + name.split("[")[0]
        if tc.find("failure") is not None or tc.find("error") is not None:
            out.add(tid)
    return out, total

base_fail, base_n = failed(f"{SP}/base.xml")
def cat(tid):
    k = pert.get(tid)
    if k is None:
        f = tid.split("::")[0]
        k = "browser" if "test_e2e_" in f else "unknown"
    return k

rows = []
for m in muts:
    x = f"{SP}/mut/{m['id']}.xml"
    if not os.path.exists(x):
        rows.append(dict(m, status="not run"))
        continue
    try:
        f, n = failed(x)
    except ET.ParseError:
        rows.append(dict(m, status="no xml"))
        continue
    f -= base_fail
    by = collections.Counter(cat(t) for t in f)
    rows.append(dict(m, status="killed" if f else "survived", nfail=len(f), by=dict(by), tests=sorted(f)[:12], ntests=n))
json.dump(rows, open(f"{SP}/data/score.json", "w"), indent=1)
done = [r for r in rows if r["status"] in ("killed", "survived")]
print("baseline failing:", sorted(base_fail), "of", base_n)
print("run", len(done), "killed", sum(r["status"] == "killed" for r in done))
for k in ("browser", "http", "direct", "golden"):
    print(f"  caught by {k:8s}: {sum(1 for r in done if r.get('by', {}).get(k))}")
print("  caught ONLY by browser e2e:", sum(1 for r in done if r.get("by") and set(r["by"]) <= {"browser"}))
print("  caught by non-browser only:", sum(1 for r in done if r.get("by") and "browser" not in r["by"]))
for r in rows:
    print(r["id"], r["kind"], r["op"], r["file"] + ":" + str(r["line"]), r["status"], r.get("by", ""), "|", r["orig"].strip()[:60])
