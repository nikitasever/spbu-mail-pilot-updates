import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release', pathlib.Path(__file__).parents[1] / 'release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.old_cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.old_cwd)
        pathlib.Path('dist').mkdir()
        pathlib.Path('sdk/build-tools/35.0.0').mkdir(parents=True)
        self.apk = b'signed fixture APK'
        self.data = dict(versionName='0.43', versionCode=47, packageId=release.PACKAGE,
                         sha256=release.hashlib.sha256(self.apk).hexdigest(),
                         url='https://github.com/' + release.REPO + '/releases/download/v0.43/spbu-mail-pilot-0.43.apk')
        pathlib.Path('dist/spbu-mail-pilot-0.43.apk').write_bytes(self.apk)
        pathlib.Path('dist/update.json').write_text(json.dumps(self.data))
        pathlib.Path('dist/release-notes.txt').write_text('fixture')
        self.existing = None
        self.latest = None
        self.remote = {}
        self.mutations = []
        self.api_error = None
        self.fail_upload = False

    def info(self, draft=False, assets=()):
        return dict(tag_name='v0.43', draft=draft, assets=[dict(name=name) for name in assets])

    def seed(self, draft=False, assets=('spbu-mail-pilot-0.43.apk', 'update.json')):
        self.existing = self.info(draft, assets)
        for name in assets:
            self.remote[('v0.43', name)] = pathlib.Path('dist', name).read_bytes()

    def latest_code(self, code):
        self.latest = dict(tag_name='v0.42', draft=False, assets=[dict(name='update.json')])
        self.remote[('v0.42', 'update.json')] = json.dumps(dict(versionCode=code, packageId=release.PACKAGE)).encode()

    def run_command(self, args, **kwargs):
        if args[1] == 'api':
            if self.api_error:
                return subprocess.CompletedProcess(args, 1, '', self.api_error)
            result = self.latest if args[-1].endswith('/latest') else self.existing
            return subprocess.CompletedProcess(args, 0 if result else 1, json.dumps(result) if result else '', '' if result else 'gh: Not Found (HTTP 404)')
        if args[2] == 'download':
            tag = args[3]
            name = args[args.index('--pattern') + 1]
            directory = pathlib.Path(args[args.index('--dir') + 1])
            if (tag, name) not in self.remote:
                raise subprocess.CalledProcessError(1, args)
            (directory / name).write_bytes(self.remote[(tag, name)])
        else:
            self.mutations.append(args)
            if args[2] == 'create':
                self.existing = self.info(draft=True)
            elif args[2] == 'upload':
                if self.fail_upload:
                    raise subprocess.CalledProcessError(1, args)
                for path in args[4:args.index('--repo')]:
                    name = pathlib.Path(path).name
                    if ('v0.43', name) in self.remote:
                        raise AssertionError('Attempted to replace asset')
                    self.remote[('v0.43', name)] = pathlib.Path(path).read_bytes()
                    self.existing['assets'].append(dict(name=name))
            elif args[2] == 'edit':
                self.existing['draft'] = False
                if '--latest' in args:
                    self.latest = self.existing
        return subprocess.CompletedProcess(args, 0)

    def tool_output(self, args, **kwargs):
        if args[0].endswith('apksigner'):
            return release.CERTIFICATE
        return "package: name='" + release.PACKAGE + "' versionCode='47' versionName='0.43'"

    def publish(self):
        with patch.dict(os.environ, ANDROID_HOME=str(self.root / 'sdk'), GITHUB_SHA='fixture-commit'), patch.object(release.subprocess, 'run', side_effect=self.run_command), patch.object(release.subprocess, 'check_output', side_effect=self.tool_output), contextlib.redirect_stdout(io.StringIO()):
            release.main()

    def test_new_release_created_as_draft_then_uploaded_then_published(self):
        self.publish()
        self.assertEqual([args[2] for args in self.mutations], ['create', 'upload', 'edit'])
        self.assertIn('--draft', self.mutations[0])
        self.assertIn('--latest', self.mutations[-1])

    def test_complete_published_release_has_no_mutation(self):
        self.seed()
        self.latest = self.existing
        self.publish()
        self.assertEqual(self.mutations, [])

    def test_draft_missing_metadata_is_completed_without_replacing_apk(self):
        self.seed(draft=True, assets=('spbu-mail-pilot-0.43.apk',))
        self.publish()
        self.assertEqual([args[2] for args in self.mutations], ['upload', 'edit'])
        self.assertEqual(self.mutations[0][4:5], ['dist/update.json'])
        self.assertNotIn('--clobber', self.mutations[0])

    def test_published_missing_asset_is_repaired_without_replacing_existing_asset(self):
        self.seed(assets=('update.json',))
        self.latest = self.existing
        self.publish()
        self.assertEqual(len(self.mutations), 1)
        self.assertEqual(self.mutations[0][4:5], ['dist/spbu-mail-pilot-0.43.apk'])

    def test_existing_conflicting_asset_stops_before_mutation(self):
        self.seed()
        self.remote[('v0.43', 'spbu-mail-pilot-0.43.apk')] = b'different APK'
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.publish()
        self.assertEqual(self.mutations, [])

    def test_older_code_does_not_replace_latest(self):
        self.latest_code(48)
        self.publish()
        self.assertIn('--latest=false', self.mutations[-1])
        self.assertEqual(self.latest['tag_name'], 'v0.42')

    def test_equal_code_other_tag_does_not_replace_latest(self):
        self.latest_code(47)
        self.publish()
        self.assertIn('--latest=false', self.mutations[-1])

    def test_newer_code_promoted(self):
        self.latest_code(46)
        self.publish()
        self.assertIn('--latest', self.mutations[-1])

    def test_api_failure_is_not_mistaken_for_missing_release(self):
        self.api_error = 'HTTP 403: forbidden'
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertEqual(self.mutations, [])

    def test_missing_latest_metadata_stops_before_mutation(self):
        self.latest_code(46)
        self.remote.clear()
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertEqual(self.mutations, [])

    def test_failed_upload_can_be_resumed_from_draft(self):
        self.fail_upload = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.publish()
        self.assertTrue(self.existing['draft'])
        self.fail_upload = False
        self.mutations.clear()
        self.publish()
        self.assertEqual([args[2] for args in self.mutations], ['upload', 'edit'])
        self.assertFalse(self.existing['draft'])

    def test_invalid_checksum_stops_before_any_mutation(self):
        pathlib.Path('dist/spbu-mail-pilot-0.43.apk').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.publish()
        self.assertEqual(self.mutations, [])

    def test_complete_draft_is_published_without_upload(self):
        self.seed(draft=True)
        self.latest_code(48)
        self.publish()
        self.assertEqual([args[2] for args in self.mutations], ['edit'])
        self.assertIn('--draft=false', self.mutations[0])
        self.assertIn('--latest=false', self.mutations[0])

    def test_invalid_latest_code_stops_before_mutation(self):
        self.latest_code(True)
        with self.assertRaisesRegex(ValueError, 'versionCode'):
            self.publish()
        self.assertEqual(self.mutations, [])

    def test_wrong_certificate_stops_before_mutation(self):
        with patch.object(self, 'tool_output', return_value='wrong certificate'):
            with self.assertRaisesRegex(ValueError, 'certificate'):
                self.publish()
        self.assertEqual(self.mutations, [])

    def test_manifest_mismatch_stops_before_mutation(self):
        original = self.tool_output
        with patch.object(self, 'tool_output', side_effect=lambda args, **kwargs: original(args, **kwargs) if args[0].endswith('apksigner') else 'different version'):
            with self.assertRaisesRegex(ValueError, 'Manifest'):
                self.publish()
        self.assertEqual(self.mutations, [])


if __name__ == '__main__':
    unittest.main()
