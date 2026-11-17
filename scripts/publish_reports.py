#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Push the local git index to GitHub through the Git Data API.

Use this when plain `git push` is blocked by the network but the API works.
Reads the tracked file list from `git ls-files`, so the remote tree always
matches the local commit exactly.

Usage: python scripts/publish_reports.py "commit message" [--branch main]
Token: $GH_TOKEN, otherwise `gh auth token`.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request


REPO_DIR = Path(__file__).resolve().parents[1]
REPO = "zhshy/divergence-trade-signals"
API = "https://api.github.com"
# 绕过系统代理：本机代理会重置 api.github.com 的 TLS 握手
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def token():
    value = os.environ.get("GH_TOKEN", "").strip()
    if value:
        return value
    try:
        return subprocess.run(["gh", "auth", "token"], check=True, capture_output=True,
                              text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("缺少 GH_TOKEN，且 gh auth token 不可用")


def call(auth, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Authorization", "token " + auth)
    request.add_header("Accept", "application/vnd.github+json")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with OPENER.open(request, timeout=60) as response:
            body = response.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        sys.exit(f"HTTP {error.code} on {method} {path}\n{error.read().decode()[:600]}")


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def tracked_blobs(root):
    """读 Git 索引里的字节（LF），避免 Windows 工作区 CRLF 造成全库行尾抖动。"""
    output = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True, capture_output=True,
    ).stdout
    paths = sorted(p for p in output.decode("utf-8").split("\0") if p)
    blobs = {}
    for path in paths:
        blobs[path] = subprocess.run(
            ["git", "-C", str(root), "show", f":{path}"],
            check=True, capture_output=True,
        ).stdout
    return blobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--tag", default="-")
    parser.add_argument("--repo", default=REPO, help="owner/name，默认本仓库")
    parser.add_argument("--repo-dir", default=str(REPO_DIR), help="本地 Git 工作副本")
    args = parser.parse_args()
    auth = token()

    head = call(auth, "GET", f"/repos/{args.repo}/git/ref/heads/{args.branch}")["object"]["sha"]
    base_tree = call(auth, "GET", f"/repos/{args.repo}/git/commits/{head}")["tree"]["sha"]
    remote = {}
    for entry in call(auth, "GET", f"/repos/{args.repo}/git/trees/{base_tree}?recursive=1")["tree"]:
        if entry["type"] == "blob":
            remote[entry["path"]] = entry["sha"]

    raw = tracked_blobs(args.repo_dir)
    local = {path: blob_sha(data) for path, data in raw.items()}

    added = sorted(p for p in local if p not in remote)
    modified = sorted(p for p in local if p in remote and remote[p] != local[p])
    deleted = sorted(p for p in remote if p not in local)
    print(f"新增 {len(added)} / 修改 {len(modified)} / 删除 {len(deleted)}")
    for path in added:
        print("  +", path)
    for path in modified:
        print("  ~", path)
    for path in deleted:
        print("  -", path)
    if not (added or modified or deleted):
        print("远端已是最新，无需提交。")
        return 0

    tree = []
    for path in added + modified:
        blob = call(auth, "POST", f"/repos/{args.repo}/git/blobs",
                    {"content": base64.b64encode(raw[path]).decode(), "encoding": "base64"})
        if blob["sha"] != local[path]:
            sys.exit(f"blob sha 不一致: {path}")
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    for path in deleted:
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})

    new_tree = call(auth, "POST", f"/repos/{args.repo}/git/trees",
                    {"base_tree": base_tree, "tree": tree})["sha"]
    commit = call(auth, "POST", f"/repos/{args.repo}/git/commits",
                  {"message": args.message, "tree": new_tree, "parents": [head]})
    call(auth, "PATCH", f"/repos/{args.repo}/git/refs/heads/{args.branch}", {"sha": commit["sha"]})
    print(f"已推送 {commit['sha'][:12]} -> {args.branch}")

    if args.tag not in ("-", "none", ""):
        try:
            call(auth, "POST", f"/repos/{args.repo}/git/refs",
                 {"ref": f"refs/tags/{args.tag}", "sha": commit["sha"]})
            print(f"已打标签 {args.tag}")
        except SystemExit as error:
            print(f"打标签失败（可能已存在）: {error}")

    remote = {}
    for entry in call(auth, "GET", f"/repos/{args.repo}/git/trees/{commit['sha']}?recursive=1")["tree"]:
        if entry["type"] == "blob":
            remote[entry["path"]] = entry["sha"]
    diff = [p for p in local if remote.get(p) != local[p]]
    extra = [p for p in remote if p not in local]
    print("✅ 逐字节一致" if not diff and not extra else f"❌ 不一致 {diff} 多余 {extra}")
    return 0 if not diff and not extra else 1


if __name__ == "__main__":
    sys.exit(main())
