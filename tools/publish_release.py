#!/usr/bin/env python3
"""Publish a numbered release with the package attached before it becomes public."""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path


def next_version(tags):
    versions = [tuple(map(int, tag.split('.'))) for tag in tags
                if re.fullmatch(r'\d+\.\d+', tag)]
    if not versions:
        return '1.0'
    major, minor = max(versions)
    return f'{major}.{minor + 1}'


class GitHub:
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.token = os.environ['GH_TOKEN']
        self.base = f'https://api.github.com/repos/{self.repo}'

    def request(self, method, path, data=None, binary=False):
        url = path if path.startswith('https://') else self.base + path
        headers = {'Authorization': f'Bearer {self.token}',
                   'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28',
                   'Content-Type': 'application/octet-stream' if binary else 'application/json'}
        body = data if binary or data is None else json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    def pages(self, path):
        items = []
        for page in range(1, 1001):
            batch = self.request('GET', f'{path}?per_page=100&page={page}')
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise RuntimeError('Too many pages to enumerate safely')


def publish(api, sha, package):
    marker = f'<!-- auto-build:{sha} -->'
    releases = api.pages('/releases')
    release = next((r for r in releases if marker in (r.get('body') or '')), None)
    if release and not release['draft']:
        if any(a['name'] == package.name for a in release['assets']):
            return release['html_url']
        raise RuntimeError('Published release is missing its package; refusing to overwrite it')
    if release is None:
        for attempt in range(30):
            tags = [t['name'] for t in api.pages('/tags')]
            tag = next_version(tags)
            try:
                api.request('POST', '/git/refs', {'ref': f'refs/tags/{tag}', 'sha': sha})
                break
            except urllib.error.HTTPError as error:
                if error.code != 422 or tag not in [t['name'] for t in api.pages('/tags')]:
                    raise
        else:
            raise RuntimeError('Could not reserve a release number')
        release = api.request('POST', '/releases', {
            'tag_name': tag, 'target_commitish': sha, 'name': f'Release {tag}',
            'draft': True, 'prerelease': False,
            'body': f'Download **release.vescpkg** below and load it in VESC Tool.\n\n'
                    f'Automatic build of `{sha}`. Package checks passed; hardware testing is still required.\n\n{marker}'})
    for asset in release.get('assets', []):
        if asset['name'] == package.name:
            api.request('DELETE', f"/releases/assets/{asset['id']}")
    upload_url = release['upload_url'].split('{')[0] + '?name=release.vescpkg'
    api.request('POST', upload_url, package.read_bytes(), binary=True)
    result = api.request('PATCH', f"/releases/{release['id']}", {'draft': False, 'make_latest': 'true'})
    return result['html_url']


if __name__ == '__main__':
    package = Path('release.vescpkg')
    if not package.is_file() or package.stat().st_size == 0:
        raise SystemExit('Missing or empty release.vescpkg')
    print(publish(GitHub(), os.environ['GITHUB_SHA'], package))
