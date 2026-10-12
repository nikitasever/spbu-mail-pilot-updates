import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile

REPO = 'nikitasever/spbu-mail-pilot-updates'
PACKAGE = 'ru.local.spbumailpilot'
CERTIFICATE = '197ecdabd94c7217afa45df6f02300f1f0ff12474b3146fc4018a3adbb8ce7b6'


def release_info(endpoint):
    result = subprocess.run(['gh', 'api', 'repos/' + REPO + '/releases/' + endpoint], capture_output=True, text=True)
    if result.returncode:
        if re.search(r'\bHTTP 404\b', result.stderr):
            return None
        raise RuntimeError('Cannot inspect GitHub release: ' + result.stderr.strip())
    return json.loads(result.stdout)


def download(release, name, directory):
    subprocess.run(['gh', 'release', 'download', release['tag_name'], '--repo', REPO,
                    '--pattern', name, '--dir', str(directory)], check=True)
    return (directory / name).read_bytes()


def main():
    metadata = pathlib.Path('dist/update.json')
    if not metadata.exists():
        print('No APK submitted yet.')
        return
    data = json.loads(metadata.read_text())
    name, code = data['versionName'], data['versionCode']
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}', name) or type(code) != int or code < 1 or data['packageId'] != PACKAGE:
        raise ValueError('Invalid release metadata')
    tag = 'v' + name
    filename = 'spbu-mail-pilot-' + name + '.apk'
    apk = pathlib.Path('dist') / filename
    if data['url'] != 'https://github.com/' + REPO + '/releases/download/' + tag + '/' + filename or hashlib.sha256(apk.read_bytes()).hexdigest() != data['sha256']:
        raise ValueError('Invalid APK checksum or URL')
    tools = sorted((pathlib.Path(os.environ['ANDROID_HOME']) / 'build-tools').iterdir())[-1]
    verified = subprocess.check_output([str(tools / 'apksigner'), 'verify', '--print-certs', str(apk)], text=True)
    if CERTIFICATE not in verified:
        raise ValueError('Wrong signing certificate')
    badging = subprocess.check_output([str(tools / 'aapt'), 'dump', 'badging', str(apk)], text=True)
    if "package: name='" + PACKAGE + "' versionCode='" + str(code) + "' versionName='" + name + "'" not in badging:
        raise ValueError('Manifest mismatch')

    release = release_info('tags/' + tag)
    latest = release_info('latest')
    promote = latest is None
    if latest is not None and latest['tag_name'] != tag:
        with tempfile.TemporaryDirectory() as directory:
            previous = json.loads(download(latest, 'update.json', pathlib.Path(directory)))
        previous_code = previous.get('versionCode')
        if type(previous_code) != int or previous_code < 1 or previous.get('packageId') != PACKAGE:
            raise ValueError('Cannot determine latest release versionCode')
        promote = code > previous_code

    required = [apk, metadata]
    present = {asset['name'] for asset in release.get('assets', [])} if release else set()
    # Never replace existing assets, even on a draft: a mismatch requires investigation.
    for path in required:
        if path.name in present:
            with tempfile.TemporaryDirectory() as directory:
                existing = download(release, path.name, pathlib.Path(directory))
            if existing != path.read_bytes():
                raise ValueError('Existing release asset differs: ' + path.name)

    if release is None:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', REPO, '--target', os.environ['GITHUB_SHA'],
                        '--title', 'Почта · тест ' + name, '--notes-file', 'dist/release-notes.txt', '--draft'], check=True)
    missing = [str(path) for path in required if path.name not in present]
    if missing:
        subprocess.run(['gh', 'release', 'upload', tag, *missing, '--repo', REPO], check=True)
    if release is None or release.get('draft'):
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPO, '--draft=false',
                        '--latest' if promote else '--latest=false'], check=True)
    elif promote:
        subprocess.run(['gh', 'release', 'edit', tag, '--repo', REPO, '--latest'], check=True)
    print('Release assets verified and publication completed.')


if __name__ == '__main__':
    main()
