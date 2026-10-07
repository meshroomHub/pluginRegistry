#!/usr/bin/env python3
"""Rebuild meshroomHub.json from the current state of the meshroomHub GitHub org.

A repository is considered a Meshroom plugin if it has a root "meshroom"
folder and a root "pyproject.toml".
Its versions are the names of its newest tags (by commit date), 
or "<default-branch>+<short-sha>" of the latest commit if it has no valid tags.
Its name, publisher, description, authors, requirements and license are read from
the "pyproject.toml" and validated with the same rules as Meshroom's PluginMetadata.
"""
import fnmatch
import json
import math
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request

from pathlib import Path, PurePosixPath, PureWindowsPath

ORG = "meshroomHub"
TOKEN = os.environ["GITHUB_TOKEN"]
REGISTRY_JSON = Path(__file__).resolve().parent.parent / "meshroomHub.json"


# Metadata written to the top of the generated meshroomHub.json.
REGISTRY_NAME = "Meshroom Hub"
REGISTRY_DESCRIPTION = "Meshroom Hub plugin registry"
REGISTRY_URL = "https://github.com/meshroomHub/pluginRegistry"
REGISTRY_FILE_URL = "https://raw.githubusercontent.com/meshroomHub/pluginRegistry/HEAD/meshroomHub.json"

# Number of most-recent tags to keep in each plugin entry's "versions" list.
# For now we cap it at the 5 latest.
MAX_VERSIONS = 5

# Validation rules, mirroring Meshroom's PluginMetadata.
# Plugin name: PEP 621 "[project].name" rule.
PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
# Plugin version: letters, digits, dots and '+' only (e.g. "1.2.3", "v1.2.3", "main+83d0b69").
PLUGIN_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9.+]+$")
# Plugin publisher: letters, digits, hyphen and underscore only.
PLUGIN_PUBLISHER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


GRAPHQL_URL = "https://api.github.com/graphql"

# One request returns everything needed for a page of repos:
# - the default branch head.
# - whether it has a root "meshroom" folder.
# - whether it has a root "pyproject.toml".
# - its root files, to resolve the "license-files" patterns.
# - the repo size.
# - the repo newest tags.
REPOS_QUERY = """
query($org: String!, $after: String, $tags: Int!) {
  rateLimit { cost remaining limit }
  organization(login: $org) {
    repositories(first: 50, after: $after, privacy: PUBLIC, isArchived: false) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        nameWithOwner
        url
        owner { login }
        diskUsage
        defaultBranchRef { name target { oid } }
        meshroomFolder: object(expression: "HEAD:meshroom") { __typename }
        pyproject: object(expression: "HEAD:pyproject.toml") { ... on Blob { text } }
        rootTree: object(expression: "HEAD:") { ... on Tree { entries { name type } } }
        refs(refPrefix: "refs/tags/", first: $tags, orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
          nodes { name }
        }
      }
    }
  }
}
"""

# Number of tags fetched per repo, from which the valid ones are kept (up to MAX_VERSIONS).
TAGS_FETCHED = 30


def graphql(query, variables):
    """POST a GitHub GraphQL query -> its "data". Raises on HTTP or GraphQL errors."""
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "meshroomHub-plugin-registry-bot",
        },
    )
    with urllib.request.urlopen(req) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(f"GitHub GraphQL error: {json.dumps(body['errors'])}")
    return body["data"]


def fetchRepos():
    """Fetch every public, non-archived repo of the org, page by page (50 repos per request)."""
    repos, after = [], None
    while True:
        result = graphql(REPOS_QUERY, {"org": ORG, "after": after, "tags": TAGS_FETCHED})
        rate = result["rateLimit"]
        print(f"GraphQL rate limit: query cost {rate['cost']}, {rate['remaining']}/{rate['limit']} points remaining")
        data = result["organization"]["repositories"]
        repos.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            return repos
        after = data["pageInfo"]["endCursor"]


def parsePyproject(fullName, blob):
    """Parse a repo's root pyproject.toml blob, or None if it is missing or malformed."""
    if not blob or blob.get("text") is None:
        return None
    try:
        return tomllib.loads(blob["text"])
    except tomllib.TOMLDecodeError as e:
        print(f"{fullName}: malformed pyproject.toml: {e}", file=sys.stderr)
        return None


def sanitizeMatching(value, pattern, field, fullName):
    """Return value if it is a string matching pattern, None (with a warning) if not, None if absent."""
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.match(value):
        print(f"{fullName}: ignoring invalid '{field}': {value!r}", file=sys.stderr)
        return None
    return value


def sanitizeText(value, field, fullName):
    """Return value if it is a string, None (with a warning) if not, None if absent."""
    if value is None:
        return None
    if not isinstance(value, str):
        print(f"{fullName}: ignoring invalid '{field}': {value!r}", file=sys.stderr)
        return None
    return value


def sanitizeAuthors(authors, fullName):
    """Return the author names of a "[project].authors" list (PEP 621 {name, email} tables or plain strings)."""
    if authors is None:
        return []
    if not isinstance(authors, list):
        print(f"{fullName}: ignoring invalid 'authors': {authors!r}", file=sys.stderr)
        return []
    names = []
    for author in authors:
        if isinstance(author, str):
            names.append(author)
        elif isinstance(author, dict) and isinstance(author.get("name"), str):
            names.append(author["name"])
        else:
            print(f"{fullName}: ignoring invalid author entry: {author!r}", file=sys.stderr)
    return names


def licensesFromClassifiers(classifiers):
    """Return the license names of the "License ::" trove classifiers, e.g. "MIT License"."""
    if not isinstance(classifiers, list):
        return []
    names = []
    for classifier in classifiers:
        if not isinstance(classifier, str):
            continue
        segments = [segment.strip() for segment in classifier.split("::")]
        if len(segments) < 2 or segments[0] != "License":
            continue
        name = segments[-1]
        # "License :: OSI Approved" is a category, it does not name a license.
        if name and name != "OSI Approved" and name not in names:
            names.append(name)
    return names


def resolveLicenseFiles(licenseFiles, rootFiles, fullName):
    """Return the root files matching the "[project].license-files" glob patterns.

    Only the root files of the repo are known: a pattern matching none of them
    (e.g. one targeting a subfolder) is returned as written.
    """
    if licenseFiles is None:
        return []
    patterns = licenseFiles
    if isinstance(licenseFiles, dict):
        # Draft form of PEP 639: a table with a "paths" or a "globs" list.
        patterns = licenseFiles.get("paths") or licenseFiles.get("globs") or []
    if not isinstance(patterns, list):
        print(f"{fullName}: ignoring invalid 'license-files': {licenseFiles!r}", file=sys.stderr)
        return []

    files = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            continue
        posixPattern = PurePosixPath(pattern)
        # Patterns must stay inside the plugin folder.
        if posixPattern.is_absolute() or PureWindowsPath(pattern).drive or ".." in posixPattern.parts:
            print(f"{fullName}: ignoring invalid 'license-files' entry: {pattern!r}", file=sys.stderr)
            continue
        matches = sorted(f for f in rootFiles if fnmatch.fnmatchcase(f, posixPattern.as_posix()))
        files += matches or [pattern]
    return files


def resolveLicense(project, rootFiles, fullName):
    """Return a displayable license string, e.g. "MIT (see file(s): LICENSE)", or None if no license is declared.

    The name is the first of: "license" as a string (PEP 639), "license.text" (legacy PEP 621),
    the "License ::" classifiers. The files are those of "license.file" and "license-files".
    """
    declared = project.get("license")
    name = None
    files = []
    if isinstance(declared, str):
        name = declared.strip()
    elif isinstance(declared, dict):
        text = declared.get("text")
        if isinstance(text, str):
            # A full license text may be pasted here: only keep its first non-empty line.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            name = lines[0] if lines else None
        if isinstance(declared.get("file"), str) and declared["file"]:
            files.append(PurePosixPath(declared["file"]).as_posix())
    elif declared is not None:
        print(f"{fullName}: ignoring invalid 'license': {declared!r}", file=sys.stderr)

    # Classifiers are only a fallback: they would repeat the name declared by "license".
    if not name:
        name = ", ".join(licensesFromClassifiers(project.get("classifiers")))

    files += resolveLicenseFiles(project.get("license-files"), rootFiles, fullName)
    # Remove duplicates, keeping the declaration order.
    files = list(dict.fromkeys(files))
    if not files:
        return name or None
    filesText = f"file(s): {', '.join(files)}"
    return f"{name} (see {filesText})" if name else f"See {filesText}"


def computePluginEntry(repo):
    """Compute the registry entry for a repo, or None if it isn't a plugin."""
    fullName = repo["nameWithOwner"]
    branchRef = repo["defaultBranchRef"]

    # A plugin has a root "meshroom" folder and a root pyproject.toml.
    if not branchRef or not repo["meshroomFolder"] or repo["meshroomFolder"]["__typename"] != "Tree":
        return None
    pyproject = parsePyproject(fullName, repo["pyproject"])
    if pyproject is None:
        return None
    project = pyproject.get("project")
    project = project if isinstance(project, dict) else {}
    tool = pyproject.get("tool")
    meshroom = tool.get("meshroom") if isinstance(tool, dict) else None
    meshroom = meshroom if isinstance(meshroom, dict) else {}

    # Default the name to the repo name and the publisher to the owner.
    name = sanitizeMatching(project.get("name"), PLUGIN_NAME_PATTERN, "name", fullName) or repo["name"]
    if not PLUGIN_NAME_PATTERN.match(name):
        print(f"{fullName}: invalid plugin name '{name}'", file=sys.stderr)
        return None
    publisher = (sanitizeMatching(meshroom.get("publisher"), PLUGIN_PUBLISHER_PATTERN, "publisher", fullName)
                 or repo["owner"]["login"])
    description = sanitizeText(project.get("description"), "description", fullName)
    authors = sanitizeAuthors(project.get("authors"), fullName)
    requirements = sanitizeText(meshroom.get("requirements"), "requirements", fullName)
    rootFiles = [e["name"] for e in (repo.get("rootTree") or {}).get("entries") or [] if e["type"] == "blob"]
    license = resolveLicense(project, rootFiles, fullName)

    # Tags come sorted by commit date, newest first: keep the valid version names.
    versions = [t["name"] for t in repo["refs"]["nodes"] if PLUGIN_VERSION_PATTERN.match(t["name"])]
    if versions:
        # "versions" lists the tags Meshroom can offer to fetch for this plugin.
        versions = versions[:MAX_VERSIONS]
    else:
        # No (valid) tags to offer: fall back to the latest commit on the default branch.
        versions = [f"{branchRef['name']}+{branchRef['target']['oid'][:7]}"]

    # diskUsage is GitHub's size of the whole repo in KB (full git history),
    # so this is only an estimate.
    sizeMB = math.ceil((repo["diskUsage"] or 0) / 1024)

    entry = {"name": name, "url": repo["url"], "versions": versions, "publisher": publisher}
    if description is not None:
        entry["description"] = description
    if authors:
        entry["authors"] = authors
    if requirements is not None:
        entry["requirements"] = requirements
    if license is not None:
        entry["license"] = license
    entry["sizeMB"] = sizeMB
    return entry


def main():
    repos = fetchRepos()
    print(f"Found {len(repos)} public repos in {ORG}")

    plugins = []
    for repo in repos:
        entry = computePluginEntry(repo)
        print(f"{repo['nameWithOwner']}: "
              f"{entry['versions'][0] if entry else 'skipped (not a plugin: no meshroom folder or valid pyproject.toml)'}")
        if entry:
            plugins.append(entry)

    plugins.sort(key=lambda e: e["url"].lower())
    registry = {
        "name": REGISTRY_NAME,
        "description": REGISTRY_DESCRIPTION,
        "url": REGISTRY_URL,
        "fileUrl": REGISTRY_FILE_URL,
        "entries": plugins,
    }
    REGISTRY_JSON.write_text(json.dumps(registry, indent=4) + "\n")
    print(f"Wrote {len(plugins)} plugins to {REGISTRY_JSON}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"GitHub API error: {e.code} {e.reason} ({e.url})", file=sys.stderr)
        print(e.read().decode(errors="replace"), file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
