"""Run extract -> (optional) price/link jobs locally, without the web page.
AWS credentials come from the usual AWS_PROFILE / environment settings.

Usage: python scripts/pipeline_test.py samples/sample_generic.csv [--price] [--link]
"""
import base64
import json
import os
import sys
import time

os.environ.setdefault("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

import handler  # noqa: E402
import jobs  # noqa: E402


def run(body):
    t = time.time()
    job_id = handler.create_job(body, start=jobs.run)  # runs synchronously here
    st = jobs.get(job_id, "status")
    print(f"{body['type']} -> {st['status']} in {time.time() - t:.1f}s")
    if st["status"] != "done":
        print(st)
        sys.exit(1)
    return st["result"]


def show(items):
    for it in items:
        print(f"- [{it['status']}] {it['id']} {it.get('serviceName')} / {it.get('name')} region={it.get('region')} "
              f"tpl={it.get('template')} " + (f"${it['monthly']}" if "monthly" in it else ""))
        for k, v in (it.get("values") or {}).items():
            print(f"      {k} = {json.dumps(v)[:150]}")
        if it.get("questions"):
            print("      QUESTIONS:", it["questions"])
        for e in it.get("errors") or []:
            print("      ERROR:", e)
        for w in it.get("warnings") or []:
            print("      warn:", w)
        if it.get("warning"):
            print("      WARN:", it["warning"])
        if it.get("configSummary"):
            print("      calc:", it["configSummary"][:220])


path = sys.argv[1]
res = run({"type": "extract", "filename": os.path.basename(path),
           "content_b64": base64.b64encode(open(path, "rb").read()).decode()})
show(res["items"])
json.dump(res, open(os.path.join(os.path.dirname(__file__), "..", "dist_extract.json"), "w"), indent=1)

if "--price" in sys.argv or "--link" in sys.argv:
    # Answer every non-blocking question with "use default", as a user clicking through would.
    for it in res["items"]:
        it["skipped"] = [q for q in it.get("questions", []) if q not in it.get("blocking", [])]
    import forms  # noqa: E402
    res["items"] = [forms.normalize(it) for it in res["items"]]
    ready = [it for it in res["items"] if it["status"] == "ready"]
    print("not ready:", [(it["name"], it["status"], it.get("questions"), it.get("errors")) for it in res["items"]
                         if it["status"] != "ready"])
    print(f"\npricing {len(ready)} ready items")
    out = run({"type": "link" if "--link" in sys.argv else "price", "items": ready, "name": "Pipeline test"})
    show(out["items"])
    print("TOTAL", out["total"], "URL", out.get("url"))
