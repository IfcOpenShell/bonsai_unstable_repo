# Bonsai - OpenBIM Blender Add-on
# Copyright (C) 2020, 2021 Dion Moult <dion@thinkmoult.com>
#
# This file is part of Bonsai.
#
# Bonsai is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Bonsai is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Bonsai.  If not, see <http://www.gnu.org/licenses/>.


import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Union
from urllib.parse import urljoin

import markdownify
import requests
from github import Github
from lxml import etree, html

# Useful:
# - https://docs.blender.org/manual/en/latest/advanced/extensions/creating_repository/static_repository.html
# - https://developer.blender.org/docs/features/extensions/api_listing/


PACKAGES_FOLDER = Path(".")
INDEX_PATH = PACKAGES_FOLDER / "index.json"
HTML_PATH = PACKAGES_FOLDER / "index.html"
MD_PATH = PACKAGES_FOLDER / "readme.md"
MD_HEADER_PATH = PACKAGES_FOLDER / "readme_header.md"
INDEX_URL = "https://raw.githubusercontent.com/IfcOpenShell/bonsai_unstable_repo/main/index.json"
BASE_URL = "https://github.com/IfcOpenShell/IfcOpenShell/releases/download/{github_tag}/"
BLENDER_PLATFORMS = ["windows-x64", "macos-x64", "macos-arm64", "linux-x64"]
# Blender doesn't support separate builds for different Python versions :(
PYTHON_VERSION = "py311"


def check_url(url) -> bool:
    try:
        response = requests.head(url, allow_redirects=True)  # Use HEAD request to check URL status
        if response.status_code == 200:
            print(f"URL is reachable: {url}")
            return True
        else:
            print(f"URL returned status code {response.status_code}: {url}")
    except requests.RequestException as e:
        print(f"URL check failed with exception: {e}: {url}")

    return False


def get_platform(filename: str) -> Union[str, None]:
    for platorm in BLENDER_PLATFORMS:
        if platorm in filename:
            return platorm


class ExtensionsRepo:
    github_tag: str

    def __init__(self, github_tag: str):
        self.fetch_urls(github_tag)
        self.run_blender()
        self.patch_repo_files()

    def fetch_urls(self, github_tag: str) -> None:
        g = Github()
        repo = g.get_repo("IfcOpenShell/IfcOpenShell")

        if github_tag == "--last-tag":
            for i, release in enumerate(repo.get_releases()):
                if i >= 10:
                    raise Exception("Couldn't find a release with a valid tag in the last 10 releases.")
                if release.tag_name.startswith("bonsai-"):
                    github_tag = release.tag_name
                    break

        self.github_tag = github_tag
        release = repo.get_release(github_tag)
        platforms_urls: dict[str, str] = {}
        for asset in release.get_assets():
            name = asset.name
            if PYTHON_VERSION not in name:
                continue
            if not (platform := get_platform(name)):
                continue
            platforms_urls[platform] = asset.browser_download_url

        if len(platforms_urls) != len(BLENDER_PLATFORMS):
            missing_platforms = set(BLENDER_PLATFORMS) - set(platforms_urls)
            raise Exception(
                f"Couldn't find in the release '{github_tag}' .zip files for some platforms: '{missing_platforms}'."
            )

        for url in platforms_urls.values():
            print(f"Downloading {url}...")
            with requests.get(url, stream=True) as r, open(PACKAGES_FOLDER / url.rsplit("/", 1)[-1], "wb") as f:
                shutil.copyfileobj(r.raw, f)
        print("Finished downloading .zip packages.")

    def run_blender(self) -> None:
        assert (blender := shutil.which("blender")), "'blender' must be available from the PATH."
        subprocess.check_call(
            f"{Path(blender).name} --command extension server-generate --repo-dir={PACKAGES_FOLDER.absolute().__str__()} --html",
            shell=True,
        )
        print("Finished blender 'extension server-generate'.")

    def patch_repo_files(self) -> None:
        self.replaced_urls = {}
        self.patch_index_json()
        self.patch_index_html()
        self.convert_html_to_md()

    def patch_index_json(self) -> None:
        with open(INDEX_PATH, "rb") as fi:
            index = json.load(fi)

        replaced_urls: dict[str, str] = {}

        for package in index["data"]:
            archive_url = package["archive_url"]
            url = urljoin(BASE_URL.format(github_tag=self.github_tag), archive_url)
            package["archive_url"] = url
            replaced_urls[archive_url] = url

        # Sort platforms for less noisy diff.
        index["data"] = sorted(index["data"], key=lambda p: p["platforms"])
        self.replaced_urls = replaced_urls

        with open(INDEX_PATH, "w") as fo:
            json.dump(index, fo, indent=2)
            fo.write("\n")

        print("Finished updating index.json")

    def patch_index_html(self) -> None:
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            tree = html.parse(f)

        for a in tree.xpath("//a[starts-with(@href, './bonsai_')]"):
            href = a.get("href")
            url, url_arguments = href.split("?")
            # Replace relative urls with absolute urls to the releases.
            # Replace relative index.json url to the repo url.
            url_arguments = url_arguments.replace(".%2Findex.json", INDEX_URL)
            new_href = f"{self.replaced_urls[url]}?{url_arguments}"
            a.set("href", new_href)

        with open(HTML_PATH, "w", encoding="utf-8") as f:
            html_string = etree.tostring(tree, method="html", pretty_print=True, encoding="utf-8").decode("utf-8")
            f.write(html_string)

        print("Finished updating index.html")

    def convert_html_to_md(self) -> None:
        with open(HTML_PATH, "r", encoding="utf-8") as file:
            html_content = file.read()

        parser = etree.HTMLParser()
        tree = etree.fromstring(html_content, parser)

        # Convert HTML to Markdown
        markdown_content = markdownify.markdownify(
            etree.tostring(tree, pretty_print=True, method="html").decode("utf-8")
        )

        with open(MD_HEADER_PATH, "r", encoding="utf-8") as fo:
            with open(MD_PATH, "w", encoding="utf-8") as file:
                file.write(fo.read())
                file.write(markdown_content)

        os.unlink(HTML_PATH)
        print("Finished updating readme.md")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        script_name = Path(__file__).name
        raise Exception(f"Usage: 'py {script_name} <github_releases_tag>' | 'py {script_name} --last-tag'")
    ExtensionsRepo(sys.argv[1])
