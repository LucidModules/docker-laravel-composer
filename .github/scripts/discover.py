#!/usr/bin/env python3
"""Discover the latest Composer release and the maintained PHP base image lines
(versions.json), and emit a GitHub Actions build matrix.

One image is built per PHP line, on top of the corresponding
lucidmodules/docker-laravel-php:<line> tag. Tags: <composer>-php<line> and
<composer-minor>-php<line>; the newest PHP line also gets the plain
<composer>, <minor>, <major> and latest tags."""
import json
import os
import re
import sys
import urllib.request

IMAGE = os.environ.get("IMAGE", "docker.io/lucidmodules/docker-laravel-composer")
BASE_REPO = "lucidmodules/docker-laravel-php"


def get_json(url):
    headers = {"User-Agent": "lucidmodules-ci"}
    if "api.github.com" in url and (token := os.environ.get("GH_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def hub_tags(repo, name_filter):
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100&name={name_filter}"
    while url:
        data = get_json(url)
        yield from (t["name"] for t in data["results"])
        url = data.get("next")


def latest_composer():
    release = get_json("https://api.github.com/repos/composer/composer/releases/latest")
    version = release["tag_name"].lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        sys.exit(f"unexpected composer version: {version}")
    return version


def base_exists(line):
    return any(t == line for t in hub_tags(BASE_REPO, line))


def main():
    lines = json.load(open("versions.json"))["php_lines"]
    lines.sort(key=lambda l: tuple(map(int, l.split("."))))

    composer = latest_composer()
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
            "tags": ",".join(f"{IMAGE}:{t}" for t in tags),
        })

    matrix = json.dumps({"include": entries})
    print(matrix)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a") as fh:
            fh.write(f"matrix={matrix}\n")


if __name__ == "__main__":
    main()
