#!/usr/bin/env python3
"""
tune-release-prefs.py — retune Radarr/Sonarr release selection for yggdrasil.

Decisions being applied:
  * allow 4K        : enable 2160p qualities in the default profile (id 9)
  * prefer HEVC     : x264 200 -> 0,  x265/HEVC 0 -> 200
  * allow HDR       : HDR -500 -> 0   (Samsung 2021 + Shield both do HDR10)
  * keep DV banned  : but ONLY true Dolby Vision without an HDR10 fallback,
                      so DoVi+HDR10 hybrids (most 4K WEB-DL) are no longer rejected
  * unchanged       : AV1 -10000 (no hw decode), Remux -500 (space)

Cutoff is deliberately NOT raised. Existing 1080p files stay at cutoff, so this
does not trigger a mass re-download of the library. New grabs still take 4K when
it is the best available release.

Run with --apply to write changes; default is a dry run.
"""
import json, subprocess, sys, urllib.request

APPLY = "--apply" in sys.argv
SERVICES = [("radarr", 7878), ("sonarr", 8989)]
PROFILES = [9, 10]

SCORES = {"x264": 0, "x265/HEVC": 200, "HDR": 0}       # Dolby Vision / AV1 / Remux untouched
ENABLE_QUALITIES = {"HDTV-2160p", "WEB 2160p", "Bluray-2160p"}

DV_SPECS = [
    {"name": "Dolby Vision", "implementation": "ReleaseTitleSpecification",
     "negate": False, "required": True,
     "fields": [{"name": "value", "value": r"\bDoVi\b|\bDV\b|Dolby.?Vision"}]},
    {"name": "No HDR10 fallback", "implementation": "ReleaseTitleSpecification",
     "negate": True, "required": True,
     "fields": [{"name": "value", "value": r"\bHDR|\bHLG\b"}]},
]

def api_key(svc):
    return subprocess.check_output(
        ["docker", "exec", svc, "sed", "-n",
         r"s#.*<ApiKey>\(.*\)</ApiKey>.*#\1#p", "/config/config.xml"]).decode().strip()

def req(base, key, path, method="GET", body=None):
    r = urllib.request.Request(base + path, method=method,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw.strip() else None)

for svc, port in SERVICES:
    base, key = f"http://127.0.0.1:{port}", api_key(svc)
    print(f"\n########## {svc} ##########")

    # --- 1. narrow the Dolby Vision format so hybrids stop being rejected ---
    _, cfs = req(base, key, "/api/v3/customformat")
    dv = next((c for c in cfs if c["name"].startswith("Dolby Vision")), None)
    if dv:
        already = len(dv.get("specifications", [])) > 1
        print(f"  Dolby Vision format: {'already narrowed' if already else 'blanket match -> narrowing'}")
        if not already:
            dv["specifications"] = DV_SPECS
            dv["name"] = "Dolby Vision (no HDR10 fallback)"
            if APPLY:
                st, _ = req(base, key, f"/api/v3/customformat/{dv['id']}", "PUT", dv)
                print(f"    PUT {st}")
    else:
        print("  Dolby Vision format: not found, skipping")

    # --- 2. profiles: scores + 2160p ---
    for pid in PROFILES:
        try:
            _, pr = req(base, key, f"/api/v3/qualityprofile/{pid}")
        except Exception:
            print(f"  profile {pid}: not present, skipping")
            continue

        changes = []
        for f in pr.get("formatItems", []):
            want = SCORES.get(f.get("name"))
            if want is not None and f.get("score") != want:
                changes.append(f"{f['name']} {f.get('score')}->{want}")
                f["score"] = want

        for item in pr.get("items", []):
            name = item["quality"]["name"] if item.get("quality") else item.get("name")
            if name in ENABLE_QUALITIES and not item.get("allowed"):
                item["allowed"] = True
                changes.append(f"allow {name}")

        print(f"  profile {pid} ({pr['name']}): cutoff={pr['cutoff']} (unchanged)")
        if changes:
            for c in changes:
                print(f"    - {c}")
            if APPLY:
                st, _ = req(base, key, f"/api/v3/qualityprofile/{pid}", "PUT", pr)
                print(f"    PUT {st}")
        else:
            print("    - no changes needed")

print("\nDRY RUN — re-run with --apply to write." if not APPLY else "\nAPPLIED.")
