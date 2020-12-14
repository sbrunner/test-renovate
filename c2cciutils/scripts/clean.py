#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import json
import os
import subprocess
import sys

import requests

import c2cciutils


def clean(image: str, tag: str, token: str) -> None:
    """
    Delete an image on Docker hub
    """

    print("Delete image '{image}:{tag}'.".format(image=image, tag=tag))

    response = requests.head(
        "https://hub.docker.com/v2/repositories/{image}/tags/{tag}/".format(image=image, tag=tag),
        headers={"Authorization": "JWT " + token},
    )
    if response.status_code == 404:
        return
    if not response.ok:
        print("Error checking image '{image}:{tag}' status.".format(image=image, tag=tag))
        print(response.text)
        sys.exit(1)

    response = requests.delete(
        "https://hub.docker.com/v2/repositories/{image}/tags/{tag}/".format(image=image, tag=tag),
        headers={"Authorization": "JWT " + token},
    )
    if not response.ok:
        print("Error on deleting tag: " + tag)
        print(response.text)
        sys.exit(1)


def main() -> None:
    token = requests.post(
        "https://hub.docker.com/v2/users/login/",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {
                "username": os.environ.get(
                    "DOCKERHUB_USERNAME",
                    subprocess.check_output(["gopass", "show", "gs/ci/dockerhub/username"]).decode(),
                ),
                "password": os.environ.get(
                    "DOCKERHUB_PASSWORD",
                    subprocess.check_output(["gopass", "show", "gs/ci/dockerhub/password"]).decode(),
                ),
            }
        ),
    ).json()["token"]

    with open(os.environ["GITHUB_EVENT_PATH"]) as event_file:
        ref = json.loads(event_file.read())["ref"]

    ref = ref.replace("/", "_")

    config = c2cciutils.get_config()

    for image in config.get("publish", {}).get("docker", {}).get("images", []):
        for tag in image.get("tags", []):
            clean(image["name"], tag.format(version=ref), token)


if __name__ == "__main__":
    main()
