"""One-shot diagnostic: shows exactly what the JSearch search endpoint returns.
Run from the project folder:  python test_jsearch.py
"""
import os, json, pathlib, requests

# get key from env or .streamlit/secrets.toml
key = os.getenv("RAPIDAPI_KEY")
if not key:
    try:
        import tomllib
        p = pathlib.Path(__file__).parent / ".streamlit" / "secrets.toml"
        if p.exists():
            key = tomllib.load(open(p, "rb")).get("RAPIDAPI_KEY")
    except Exception as e:
        print("secrets load error:", e)

print("RAPIDAPI_KEY loaded:", bool(key))
if not key:
    raise SystemExit("No key found — set it in .streamlit/secrets.toml or env first.")

headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
base = "https://jsearch.p.rapidapi.com"

for path, extra in [("/search-v2", {}), ("/search", {"page": "1", "num_pages": "1"})]:
    params = {"query": "product manager in Bengaluru, India",
              "country": "in", "date_posted": "week", **extra}
    r = requests.get(base + path, headers=headers, params=params, timeout=30)
    print(f"\n=== GET {path}  ->  HTTP {r.status_code} ===")
    if r.status_code != 200:
        print(r.text[:300])
        continue
    data = r.json()
    print("top-level type:", type(data).__name__)
    if isinstance(data, dict):
        print("top-level keys:", list(data.keys()))
    # print a trimmed view of the structure so we can see where jobs live
    print(json.dumps(data, indent=2)[:1200])
    break
