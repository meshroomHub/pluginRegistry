#!/usr/bin/env python3
"""Rebuild plugins.json from the current state of the meshroomHub GitHub org.

A repository is considered a Meshroom plugin if it has a root "meshroom"
folder. Its version is the name of its newest tag (by commit date), or
"<default-branch>+<short-sha>" of the latest commit if it has no tags.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

from datetime import datetime
from pathlib import Path

ORG = "meshroomHub"
API_BASE = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]
PLUGINS_JSON = Path(__file__).resolve().parent.parent / "plugins.json"


def gh(url, ignoreStatus=()):
    """GET a GitHub API URL -> (parsed JSON body, Link header). None if status is ignored."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "meshroomHub-plugin-registry-bot",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp), resp.headers.get("Link")
    except urllib.error.HTTPError as e:
        if e.code in ignoreStatus:
            return None, None
        raise


def paginate(url):
    """Follow "next" links to collect all pages of a GitHub list endpoint."""
    results = []
    while url:
        data, link = gh(url)
        results.extend(data)
        match = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        url = match.group(1) if match else None
    return results


def parseDate(dateStr):
    return datetime.fromisoformat(dateStr.replace("Z", "+00:00"))


def hasMeshroomFolder(fullName, branch):
    data, _ = gh(f"{API_BASE}/repos/{fullName}/contents/meshroom?ref={branch}", ignoreStatus=(404,))
    return isinstance(data, list)


def commitDate(fullName, sha):
    """Fetch a commit's committer date, or None if the sha can't be resolved."""
    data, _ = gh(f"{API_BASE}/repos/{fullName}/commits/{sha}", ignoreStatus=(404, 422))
    return parseDate(data["commit"]["committer"]["date"]) if data else None


def tagDatesOf(fullName):
    """Map each tag name to its underlying commit's date."""
    tags = paginate(f"{API_BASE}/repos/{fullName}/tags?per_page=100")
    dates = ((t["name"], commitDate(fullName, t["commit"]["sha"])) for t in tags)
    return {name: date for name, date in dates if date is not None}


def latestCommit(fullName, branch):
    data, _ = gh(f"{API_BASE}/repos/{fullName}/commits/{branch}")
    return data["sha"], parseDate(data["commit"]["committer"]["date"])


def computePluginEntry(repo):
    """Compute the registry entry for a repo, or None if it isn't a plugin."""
    fullName = repo["full_name"]
    branch = repo["default_branch"]

    if not hasMeshroomFolder(fullName, branch):
        return None

    tagDates = tagDatesOf(fullName)
    if tagDates:
        version, _ = max(tagDates.items(), key=lambda kv: kv[1])
    else:
        sha, _ = latestCommit(fullName, branch)
        version = f"{branch}+{sha[:7]}"

    return {"url": repo["html_url"], "version": version}


def main():
    repos = [r for r in paginate(f"{API_BASE}/orgs/{ORG}/repos?type=public&per_page=100") if not r.get("archived")]
    print(f"Found {len(repos)} public repos in {ORG}")

    plugins = []
    for repo in repos:
        entry = computePluginEntry(repo)
        print(f"{repo['full_name']}: {entry['version'] if entry else 'skipped (no meshroom folder)'}")
        if entry:
            plugins.append(entry)

    plugins.sort(key=lambda e: e["url"].lower())
    PLUGINS_JSON.write_text(json.dumps(plugins, indent=4) + "\n")
    print(f"Wrote {len(plugins)} plugins to {PLUGINS_JSON}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"GitHub API error: {e.code} {e.reason} ({e.url})", file=sys.stderr)
        print(e.read().decode(errors="replace"), file=sys.stderr)
        sys.exit(1)
