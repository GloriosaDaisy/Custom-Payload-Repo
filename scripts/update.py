#!/usr/bin/env python3
"""Keep repository.json in sync with the latest upstream GitHub releases.

For every entry in sources.json it asks GitHub for the author's latest release,
downloads the matching .elf/.bin, computes the SHA-256 and rewrites the entry in
repository.json. Nothing is changed unless a new release (or a changed file) is
found. Entries are keyed by their stable "id", so favorites and autoload slots in
Payload Manager survive updates.
"""
import fnmatch
import hashlib
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "repository.json")
SOURCES = os.path.join(ROOT, "sources.json")


def http_get(url, accept=None):
    headers = {"User-Agent": "payload-repo-updater"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = "Bearer " + token
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def get_latest_release(repo):
    data = http_get("https://api.github.com/repos/%s/releases/latest" % repo,
                    "application/vnd.github+json")
    return json.loads(data)


def download(url):
    return http_get(url)


def pick_asset(release, pattern):
    matches = [a for a in release.get("assets", [])
               if fnmatch.fnmatch(a["name"], pattern)]
    if len(matches) != 1:
        raise RuntimeError("pattern %r matched %d assets (need exactly 1): %s" % (
            pattern, len(matches), [a["name"] for a in release.get("assets", [])]))
    return matches[0]


def check_binary(name, blob):
    if name.lower().endswith(".elf") and not blob.startswith(b"\x7fELF"):
        raise RuntimeError("%s is not a valid ELF file" % name)
    if len(blob) == 0:
        raise RuntimeError("%s is empty" % name)


def update_source(src, entry):
    """Return an updated entry dict, or None when nothing changed."""
    release = get_latest_release(src["repo"])
    asset = pick_asset(release, src["asset_pattern"])
    version = release["tag_name"]
    digest = (asset.get("digest") or "").removeprefix("sha256:")

    if entry and entry.get("version") == version and (
            not digest or entry.get("checksum") == digest):
        return None

    blob = download(asset["browser_download_url"])
    check_binary(asset["name"], blob)
    checksum = hashlib.sha256(blob).hexdigest()
    if digest and digest != checksum:
        raise RuntimeError("download hash %s does not match GitHub digest %s" % (
            checksum, digest))
    if entry and entry.get("version") == version and entry.get("checksum") == checksum:
        return None

    new = dict(entry) if entry else {
        "id": src["id"],
        "name": src["name"],
        "description": src.get("description", ""),
        "category": src.get("category", "Uncategorized"),
        "author": src.get("author", src["repo"].split("/")[0]),
        "source": "https://github.com/%s/releases" % src["repo"],
    }
    new.update({
        "filename": asset["name"],
        "url": asset["browser_download_url"],
        "version": version,
        "last_update": (release.get("published_at") or "")[:10] or new.get("last_update", ""),
        "checksum": checksum,
    })
    return {k: v for k, v in new.items() if v != ""}


def main():
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(SOURCES, encoding="utf-8") as f:
        sources = json.load(f)

    payloads = manifest["payloads"]
    index = {p["id"]: i for i, p in enumerate(payloads) if "id" in p}
    changed, failed = False, False

    for src in sources:
        pid = src["id"]
        try:
            entry = payloads[index[pid]] if pid in index else None
            new = update_source(src, entry)
        except Exception as exc:  # keep going so one bad source doesn't block the rest
            print("ERROR %s: %s" % (pid, exc), file=sys.stderr)
            failed = True
            continue
        if new is None:
            print("up to date: %s" % pid)
            continue
        if pid in index:
            payloads[index[pid]] = new
        else:
            payloads.append(new)
            index[pid] = len(payloads) - 1
        print("updated: %s -> %s" % (pid, new["version"]))
        changed = True

    if changed:
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
