import tempfile
import unittest
import urllib.error
from pathlib import Path
from publish_release import next_version, publish


class FakeGitHub:
    def __init__(self, release=None, collision=False, fail_upload=False):
        self.release = release
        self.tags = ['1.0']
        self.calls = []
        self.collision = collision
        self.fail_upload = fail_upload

    def pages(self, path):
        if path == '/releases':
            return [self.release] if self.release else []
        return [{'name': tag} for tag in self.tags]

    def request(self, method, path, data=None, binary=False):
        self.calls.append((method, path, data))
        if path == '/git/refs':
            self.tags.append(data['ref'].split('/')[-1])
            if self.collision:
                self.collision = False
                raise urllib.error.HTTPError(path, 422, 'Already exists', {}, None)
            return {}
        if path == '/releases':
            self.release = dict(data, id=1, assets=[], upload_url='https://uploads.github.com/test{?name}', html_url='https://github.com/test/release')
            return self.release
        if path.startswith('https://uploads.github.com/'):
            if self.fail_upload:
                raise RuntimeError('upload failed')
            assert data == b'package bytes'
            self.release['assets'] = [{'name': 'release.vescpkg'}]
            return {}
        if method == 'PATCH':
            self.release.update(data)
            return self.release
        return None


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.package = Path(self.tmp.name) / 'release.vescpkg'
        self.package.write_bytes(b'package bytes')

    def test_numbering(self):
        self.assertEqual(next_version([]), '1.0')
        self.assertEqual(next_version(['1.0']), '1.1')
        self.assertEqual(next_version(['1.9', '1.10', 'v99', 'test']), '1.11')
        self.assertEqual(next_version(['2.0', '1.99']), '2.1')

    def test_publish_after_upload_and_rerun_does_not_duplicate(self):
        api = FakeGitHub()
        publish(api, 'abc', self.package)
        self.assertEqual(api.release['name'], 'Release 1.1')
        self.assertEqual(api.release['target_commitish'], 'abc')
        self.assertEqual(api.calls[-1][0], 'PATCH')
        calls = len(api.calls)
        publish(api, 'abc', self.package)
        self.assertEqual(len(api.calls), calls)

    def test_simultaneous_build_reserves_next_unused_tag(self):
        api = FakeGitHub(collision=True)
        publish(api, 'abc', self.package)
        self.assertEqual(api.release['tag_name'], '1.2')

    def test_failed_upload_stays_draft_and_can_resume(self):
        api = FakeGitHub(fail_upload=True)
        with self.assertRaises(RuntimeError):
            publish(api, 'abc', self.package)
        self.assertTrue(api.release['draft'])
        api.fail_upload = False
        publish(api, 'abc', self.package)
        self.assertEqual(api.release['tag_name'], '1.1')
        self.assertFalse(api.release['draft'])


if __name__ == '__main__':
    unittest.main()
