#!/usr/bin/python3
"""Report how a dnf repository is defined on this host, without network access.

Usage: repo_lookup.py <repository id> [keyword ...]

Reads the same .repo files dnf uses, including the redhat.repo file that
subscription-manager generates for Red Hat Satellite and Katello content, and
prints JSON with:
  found       whether the repository is defined
  enabled     whether it is enabled
  file        the .repo file that defines it
  options     its options (e.g. timeout, retries, gpgkey)
  candidates  repository ids containing any keyword (to help fix a wrong id)

REPO_LOOKUP_REPOSDIRS (colon separated) overrides the repository directories;
it exists for testing.
"""
import configparser
import glob
import json
import os
import sys

DEFAULT_REPOSDIRS = ["/etc/yum.repos.d", "/etc/yum/repos.d", "/etc/distro.repos.d"]


def reposdirs():
    override = os.environ.get("REPO_LOOKUP_REPOSDIRS")
    if override:
        return [d for d in override.split(":") if d]
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read("/etc/dnf/dnf.conf")
        value = parser.get("main", "reposdir", fallback="")
    except configparser.Error:
        value = ""
    return [d for d in value.replace(",", " ").split() if d] or DEFAULT_REPOSDIRS


def truthy(value):
    return str(value).strip().lower() in ("1", "yes", "true", "on")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: repo_lookup.py <repository id> [keyword ...]")
    repo_id = sys.argv[1]
    keywords = [k.lower() for k in sys.argv[2:]]

    repos = {}
    for directory in reposdirs():
        for path in sorted(glob.glob(os.path.join(directory, "*.repo"))):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path)
            except configparser.Error:
                continue
            for section in parser.sections():
                if section not in repos:
                    repos[section] = {"file": path, "options": dict(parser.items(section))}

    repo = repos.get(repo_id)
    print(json.dumps({
        "found": repo is not None,
        "enabled": truthy(repo["options"].get("enabled", "1")) if repo else False,
        "file": repo["file"] if repo else None,
        "options": repo["options"] if repo else {},
        "candidates": sorted(r for r in repos if any(k in r.lower() for k in keywords)),
    }))


if __name__ == "__main__":
    main()
