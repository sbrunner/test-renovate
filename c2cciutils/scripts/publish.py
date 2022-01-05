#!/usr/bin/env python3

"""
The publish script.
"""

import argparse
import os
import re
import subprocess  # nosec
import sys
import tarfile
from typing import Match, Optional, cast

import requests

import c2cciutils.configuration
import c2cciutils.publish
import c2cciutils.security
from c2cciutils.publish import GoogleCalendar


def match(tpe: str, base_re: str) -> Optional[Match[str]]:
    """
    Return the match for `GITHUB_REF` basically like: `refs/<tpe>/<base_re>`.

    Arguments:
        tpe: The type of ref we want to match (heads, tag, ...)
        base_re: The regular expression to match the value
    """
    if base_re[0] == "^":
        base_re = base_re[1:]
    if base_re[-1] != "$":
        base_re += "$"
    return re.match(f"^refs/{tpe}/{base_re}", os.environ["GITHUB_REF"])


def to_version(full_config: c2cciutils.configuration.Configuration, value: str, kind: str) -> str:
    """
    Compute publish version from branch name or tag.

    Arguments:
        full_config: The full configuration
        value: The value to be transformed
        kind: The name of the transformer in the configuration
    """
    item_re = c2cciutils.compile_re(
        cast(
            c2cciutils.configuration.VersionTransform, full_config["version"].get(kind + "_to_version_re", [])
        )
    )
    value_match = c2cciutils.match(value, item_re)
    if value_match[0] is not None:
        return c2cciutils.get_value(*value_match)
    return value


def main() -> None:
    """
    Run the publish.
    """
    parser = argparse.ArgumentParser(description="Publish the project.")
    parser.add_argument("--group", default="default", help="The publishing group")
    parser.add_argument("--version", help="The version to publish to")
    parser.add_argument("--branch", help="The branch from which to compute the version")
    parser.add_argument("--tag", help="The tag from which to compute the version")
    parser.add_argument("--dry-run", action="store_true", help="Don't do the publish")
    parser.add_argument(
        "--type",
        help="The type of version, if no argument provided autodeterminated, can be: "
        "rebuild (in case of rebuild), version_tag, version_branch, feature_branch, feature_tag "
        "(for pull request)",
    )
    args = parser.parse_args()

    config = c2cciutils.get_config()

    if config["publish"].get("print_versions"):
        print("::group::Versions")
        c2cciutils.print_versions(config.get("publish", {}).get("print_versions", {}))
        print("::endgroup::")

    # Describe the kind of release we do: rebuild (specified with --type), version_tag, version_branch,
    # feature_branch, feature_tag (for pull request)
    version: str = ""
    ref = os.environ["GITHUB_REF"]

    if len([e for e in [args.version, args.branch, args.tag] if e is not None]) > 1:
        print("ERROR: you specified more than one of the arguments --version, --branch or --tag")
        sys.exit(1)

    tag_match = c2cciutils.match(
        ref,
        c2cciutils.compile_re(config["version"].get("tag_to_version_re", []), "refs/tags/"),
    )
    branch_match = c2cciutils.match(
        ref,
        c2cciutils.compile_re(config["version"].get("branch_to_version_re", []), "refs/heads/"),
    )
    version_type = args.type
    if args.version is not None:
        version = args.version
    elif args.branch is not None:
        version = to_version(config, args.branch, "branch")
    elif args.tag is not None:
        version = to_version(config, args.tag, "tag")
    elif tag_match[0] is not None:
        if version_type is None:
            version_type = "version_tag"
        else:
            print("WARNING: you specified the argument --type but not one of --version, --branch or --tag")
        version = c2cciutils.get_value(*tag_match)
    elif branch_match[0] is not None:
        if version_type is None:
            version_type = "version_branch"
        else:
            print("WARNING: you specified the argument --type but not one of --version, --branch or --tag")
        version = c2cciutils.get_value(*branch_match)
    elif ref.startswith("refs/heads/"):
        if version_type is None:
            version_type = "feature_branch"
        else:
            print("WARNING: you specified the argument --type but not one of --version, --branch or --tag")
        # By the way we replace '/' by '_' because it isn't supported by Docker
        version = "_".join(ref.split("/")[2:])
    elif ref.startswith("refs/tags/"):
        if version_type is None:
            version_type = "feature_tag"
        else:
            print("WARNING: you specified the argument --type but not one of --version, --branch or --tag")
        # By the way we replace '/' by '_' because it isn't supported by Docker
        version = "_".join(ref.split("/")[2:])
    else:
        print(
            f"WARNING: {ref} is not supported, only ref starting with 'refs/heads/' or 'refs/tags/' "
            "are supported, ignoring"
        )
        sys.exit(0)

    if version_type is None:
        print("ERROR: you specified one of the arguments --version, --branch or --tag but not the --type")
        sys.exit(1)

    if version_type is not None:
        print(f"Create release type {version_type}: {version}")

    success = True
    pypi_config = cast(
        c2cciutils.configuration.PublishPypiConfig,
        config.get("publish", {}).get("pypi", {}) if config.get("publish", {}).get("pypi", False) else {},
    )
    if pypi_config:
        for package in pypi_config["packages"]:
            if package.get("group") == args.group:
                publish = version_type in pypi_config.get("versions", [])
                if args.dry_run:
                    print(
                        f"{'Publishing' if publish else 'Checking'} "
                        f"'{package.get('path')}' to pypi, skipping (dry run)"
                    )
                else:
                    success &= c2cciutils.publish.pip(package, version, version_type, publish)

    google_calendar = None
    google_calendar_config = cast(
        c2cciutils.configuration.PublishGoogleCalendarConfig,
        config.get("publish", {}).get("google_calendar", {})
        if config.get("publish", {}).get("google_calendar", False)
        else {},
    )

    docker_config = cast(
        c2cciutils.configuration.PublishDockerConfig,
        config.get("publish", {}).get("docker", {}) if config.get("publish", {}).get("docker", False) else {},
    )
    if docker_config:
        latest = False
        if os.path.exists("SECURITY.md") and docker_config["latest"] is True:
            with open("SECURITY.md", encoding="utf-8") as security_file:
                security = c2cciutils.security.Security(security_file.read())
            version_index = security.headers.index("Version")
            latest = security.data[-1][version_index] == version

        for image_conf in docker_config.get("images", []):
            if image_conf.get("group", "") == args.group:
                for tag_config in image_conf.get("tags", []):
                    tag_src = tag_config.format(version="latest")
                    tag_dst = tag_config.format(version=version)
                    for name, conf in docker_config.get("repository", {}).items():
                        if version_type in conf.get("versions", []):
                            if args.dry_run:
                                print(
                                    f"Publishing {image_conf['name']}:{tag_dst} to {name}, "
                                    "skipping (dry run)"
                                )
                                if latest:
                                    print(
                                        f"Publishing {image_conf['name']}:{tag_src} to {name}, "
                                        "skipping (dry run)"
                                    )
                            else:
                                success &= c2cciutils.publish.docker(
                                    conf, name, image_conf, tag_src, tag_dst, latest
                                )
                    if version_type in google_calendar_config.get("on", []):
                        if not google_calendar:
                            google_calendar = GoogleCalendar()
                        summary = f"{image_conf['name']}:{tag_dst}"
                        description = (
                            f"Published on: {', '.join(docker_config['repository'].keys())}\n"
                            f"For version type: {version_type}"
                        )

                        google_calendar.create_event(summary, description)

    helm_config = cast(
        c2cciutils.configuration.PublishHelmConfig,
        config.get("publish", {}).get("helm", {}) if config.get("publish", {}).get("helm", False) else {},
    )
    if helm_config and helm_config["folders"] and version_type in helm_config.get("versions", []):
        url = "https://github.com/helm/chart-releaser/releases/download/v1.2.1/chart-releaser_1.2.1_linux_amd64.tar.gz"
        response = requests.get(url, stream=True)
        with tarfile.open(fileobj=response.raw, mode="r:gz") as file:
            file.extractall(path=os.path.expanduser("~/.local/bin"))

        owner, repo = c2cciutils.get_repository().split("/")
        commit_sha = (
            subprocess.run(["git", "rev-parse", "HEAD"], check=True, stdout=subprocess.PIPE)
            .stdout.strip()
            .decode()
        )
        token = (
            os.environ["GITHUB_TOKEN"].strip()
            if "GITHUB_TOKEN" in os.environ
            else c2cciutils.gopass("gs/ci/github/token/gopass")
        )
        assert token is not None
        if version_type == "version_branch":
            last_tag = (
                subprocess.run(
                    ["git", "describe", "--abbrev=0", "--tags"], check=True, stdout=subprocess.PIPE
                )
                .stdout.strip()
                .decode()
            )
            expression = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
            while expression.match(last_tag) is None:
                last_tag = (
                    subprocess.run(
                        ["git", "describe", "--abbrev=0", "--tags", f"{last_tag}^"],
                        check=True,
                        stdout=subprocess.PIPE,
                    )
                    .stdout.strip()
                    .decode()
                )

            versions = last_tag.split(".")
            versions[-1] = str(int(versions[-1]) + 1)
            version = ".".join(versions)

        for folder in helm_config["folders"]:
            success &= c2cciutils.publish.helm(folder, version, owner, repo, commit_sha, token)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
