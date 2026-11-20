#!/usr/bin/env python3
"""Discover the latest Composer release and the maintained PHP base image lines
(versions.json), and emit a GitHub Actions build matrix.

Composer: newest release at least 7 days old (supply-chain cooldown), pinned by image digest
and verified against the getcomposer.org sha256.

One image is built per PHP line, on top of the corresponding
lucidmodules/docker-laravel-php:<line> tag. Tags: <composer>-php<line> and
<composer-minor>-php<line>; the newest PHP line also gets the plain
<composer>, <minor>, <major> and latest tags."""
import datetime as dt
import functools
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IMAGE = os.environ.get("IMAGE", "docker.io/lucidmodules/docker-laravel-composer")
BASE_REPO = "lucidmodules/docker-laravel-php"


RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
REGISTRY = "https://registry-1.docker.io"
REGISTRY_AUTH = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"


def fetch_json(url, headers=None, attempts=5):
    """GET a JSON document, retrying transient failures with exponential backoff.
    Returns (data, response headers)."""
    headers = {"User-Agent": "lucidmodules-ci", **(headers or {})}
    if "api.github.com" in url and (token := os.environ.get("GH_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response), response.headers
        except urllib.error.URLError as exc:  # HTTPError is a subclass
            status = getattr(exc, "code", None)
            if attempt == attempts or (status is not None and status not in RETRY_STATUSES):
                raise
            delay = 2 ** attempt
            print(f"::warning::{url}: {exc}; retrying in {delay}s ({attempt}/{attempts - 1})", file=sys.stderr)
            time.sleep(delay)


def get_json(url):
    return fetch_json(url)[0]


def next_link(link_header, base_url):
    if link_header and (m := re.search(r'<([^>]+)>;\s*rel="next"', link_header)):
        return urllib.parse.urljoin(base_url, m.group(1))
    return None


@functools.lru_cache(maxsize=None)
def hub_tags(repo):
    """All tags of a Docker Hub repository, fetched through the registry API (the same
    endpoint `docker pull` uses). hub.docker.com's web API intermittently answers
    anonymous requests from GitHub-hosted runners with 403, the registry does not."""
    token = get_json(REGISTRY_AUTH.format(repo=repo))["token"]
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{REGISTRY}/v2/{repo}/tags/list"
    tags = []
    while url:
        data, response_headers = fetch_json(url, headers)
        tags += data.get("tags") or []
        url = next_link(response_headers.get("Link"), url)
    return tuple(tags)


# Supply chain: only use Composer releases at least this old; hijacked releases are usually
# caught and pulled within days. PHP itself is not delayed (security fixes go out ASAP).
MIN_RELEASE_AGE_DAYS = 7
COMPOSER_BIN_REPO = "composer/composer"
MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
])


def version_key(version):
    return tuple(map(int, version.split(".")))


def dockerfile_default(arg):
    """Default value of an ARG in the Dockerfile: the reviewed floor we never go below."""
    m = re.search(rf"^ARG {arg}=(\S+)$", open("Dockerfile").read(), re.M)
    if not m:
        sys.exit(f"Dockerfile has no default for ARG {arg}")
    return m.group(1)


def manifest_digest(repo, tag):
    """Digest of the multi-arch image index for repo:tag, or None if the tag doesn't exist."""
    token = get_json(REGISTRY_AUTH.format(repo=repo))["token"]
    req = urllib.request.Request(f"{REGISTRY}/v2/{repo}/manifests/{tag}", method="HEAD", headers={
        "Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT, "User-Agent": "lucidmodules-ci"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.headers["Docker-Content-Digest"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def composer_sha256(version):
    """sha256 of composer.phar as published on getcomposer.org (a second, independent channel)."""
    url = f"https://getcomposer.org/download/{version}/composer.phar.sha256sum"
    req = urllib.request.Request(url, headers={"User-Agent": "lucidmodules-ci"})
    with urllib.request.urlopen(req, timeout=30) as response:
        sha = response.read().decode().split()[0]
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        sys.exit(f"unexpected checksum from {url}: {sha!r}")
    return sha


def latest_composer():
    """Newest stable Composer release that is at least MIN_RELEASE_AGE_DAYS old, not older than
    the Dockerfile default, and published as a composer/composer:<version>-bin image.
    Returns (version, image digest, composer.phar sha256)."""
    floor = dockerfile_default("COMPOSER_VERSION")
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=MIN_RELEASE_AGE_DAYS)
    candidates = []
    for release in get_json("https://api.github.com/repos/composer/composer/releases?per_page=50"):
        version = release["tag_name"].lstrip("v")
        published = dt.datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
        if release["draft"] or release["prerelease"] or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            continue
        if published <= cutoff or version == floor:
            candidates.append(version)
    candidates = sorted((v for v in candidates if version_key(v) >= version_key(floor)), key=version_key, reverse=True)
    for version in candidates:
        if digest := manifest_digest(COMPOSER_BIN_REPO, f"{version}-bin"):
            return version, digest, composer_sha256(version)
        print(f"::warning::{COMPOSER_BIN_REPO}:{version}-bin not published, trying an older release", file=sys.stderr)
    sys.exit(f"no Composer release >= {floor} that is {MIN_RELEASE_AGE_DAYS}+ days old with a -bin image")


def base_exists(line):
    return line in hub_tags(BASE_REPO)


def main():
    lines = json.load(open("versions.json"))["php_lines"]
    lines.sort(key=lambda l: tuple(map(int, l.split("."))))

    composer, composer_digest, composer_sha256_ = latest_composer()
    major, minor, _ = composer.split(".")
    composer_minor = f"{major}.{minor}"

    available = []
    for line in lines:
        if not base_exists(line):
            print(f"::warning::base image {BASE_REPO}:{line} not found, skipping {line}", file=sys.stderr)
            continue
        available.append(line)

    if not available:
        sys.exit("no buildable PHP lines discovered")

    newest = available[-1]
    entries = []
    for line in available:
        tags = [f"{composer}-php{line}", f"{composer_minor}-php{line}"]
        if line == newest:
            tags += [composer, composer_minor, major, "latest"]
        entries.append({
            "php_line": line,
            "composer_version": composer,
            "composer_digest": composer_digest,
            "composer_sha256": composer_sha256_,
            "tags": ",".join(f"{IMAGE}:{t}" for t in tags),
        })

    matrix = json.dumps({"include": entries})
    print(matrix)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as fh:
            fh.write(f"matrix={matrix}\n")


if __name__ == "__main__":
    main()
