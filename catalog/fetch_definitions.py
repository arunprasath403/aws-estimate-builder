"""Download the AWS Pricing Calculator manifest and every service definition into catalog/raw/.

Usage: python catalog/fetch_definitions.py
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://d1qsjq9pzbk1k6.cloudfront.net"
RAW = os.path.join(os.path.dirname(__file__), "raw")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.read()


def save(code, path):
    dest = os.path.join(RAW, f"{code}.json")
    try:
        data = get(path)
        with open(dest, "wb") as f:
            f.write(data)
        return code, len(data), None
    except Exception as e:
        return code, 0, str(e)


def main():
    os.makedirs(RAW, exist_ok=True)
    manifest = json.loads(get("/manifest/en_US.json"))
    with open(os.path.join(RAW, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    jobs = {}
    for s in manifest["awsServices"]:
        jobs[s["serviceCode"]] = s.get("serviceDefinitionUrlPath") or f"/data/{s['serviceCode']}/en_US.json"
    # Group definitions reference sub-services that are not top-level manifest entries; fetch those too.
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda kv: save(*kv), jobs.items()))
    sub = set()
    for code, size, err in results:
        if err:
            continue
        d = json.load(open(os.path.join(RAW, f"{code}.json"), encoding="utf-8"))
        for s in d.get("subServices", []) or []:
            sc = s.get("serviceCode") if isinstance(s, dict) else s
            if sc and sc not in jobs:
                sub.add(sc)
    with ThreadPoolExecutor(16) as ex:
        results += list(ex.map(lambda c: save(c, f"/data/{c}/en_US.json"), sorted(sub)))
    errs = [r for r in results if r[2]]
    print(f"manifest services: {len(jobs)}, extra sub-services: {len(sub)}, failed: {len(errs)}")
    for e in errs[:20]:
        print("  failed", e[0], e[2][:80])


if __name__ == "__main__":
    main()
