#!/usr/bin/env bash
__version__ = '0.9.0'

from contextlib import contextmanager
from enum import Enum
from json import load
from logging import warning
from re import IGNORECASE, search
from subprocess import CalledProcessError, check_call, check_output

from path import Path
from parver import Version
from typer import run


class ReleaseType(Enum):
    DEV = 'dev'
    PATCH = 'patch'
    MINOR = 'minor'
    MAJOR = 'major'


DEV = ReleaseType.DEV
PATCH = ReleaseType.PATCH
MINOR = ReleaseType.MINOR
MAJOR = ReleaseType.MAJOR


SIMULATE = False


class FileVersion:
    """Wraps around a version variable in a file. Caches reads."""
    __slots__ = '_file', '_offset', '_version', '_trail'

    def __init__(self, path: Path, variable: str):
        file = self._file = path.open('r+', newline='\n')
        text = file.read()
        if SIMULATE is True:
            print(f'reading {path}')
            from io import StringIO
            self._file = StringIO(text)
        match = search(r'\b' + variable + r'\s*=\s*([\'"])(.*?)\1', text)
        self._offset, end = match.span(2)
        self._trail = text[end:]
        self._version = Version.parse(match[2])

    @property
    def version(self) -> Version:
        return self._version

    @version.setter
    def version(self, version: Version):
        (file := self._file).seek(self._offset)
        file.write(str(version) + self._trail)
        file.truncate()
        self._version = version

    def close(self):
        self._file.close()


@contextmanager
def get_file_versions() -> list[FileVersion]:
    with open('r3l3453.json', 'r') as f:
        json_config = load(f)

    append = (file_versions := []).append
    for path_version in json_config['version_paths']:
        path, variable = path_version.split(':', 1)
        append(FileVersion(Path(path), variable))

    versions = [fv.version for fv in file_versions]
    assert versions.count(versions[0]) == len(versions),\
        "file versions don't match"

    try:
        yield file_versions
    finally:
        for fv in file_versions:
            fv.close()


def get_release_type() -> ReleaseType:
    """Return 0 for major, 1 for minor and 2 for a patch release.

    According to https://www.conventionalcommits.org/en/v1.0.0/ .
    """
    try:
        last_version_tag: str = check_output(
            ('git', 'describe', '--match', 'v[0-9]*', '--abbrev=0')
        )[:-1].decode()
        if SIMULATE is True:
            print(f'{last_version_tag=}')
        log = check_output(
            ('git', 'log', '--format=%B', '-z', f'{last_version_tag}..@'))
    except CalledProcessError:  # there are no version tags
        warning('No version tags found. Checking all commits...')
        log = check_output(('git', 'log', '--format=%B'))
    if search(
            rb'(?:\A|[\0\n])(?:BREAKING CHANGE[(:]|.*?!:)', log):
        return MAJOR
    if search(rb'(?:\A|\0)feat[(:]', log, IGNORECASE):
        return MINOR
    return PATCH


def get_release_version(
    current_version: Version, release_type: ReleaseType = None
) -> Version:
    """Return the next version according to git log."""
    if release_type is DEV:
        if current_version.is_devrelease:
            return current_version.bump_dev()
        return current_version.bump_release(index=2).bump_dev()
    if release_type is None:
        release_type = get_release_type()
        if SIMULATE is True:
            print(f'get_release_type returned {release_type}')
    base_version = current_version.base_version()  # removes devN
    if release_type is PATCH:
        return base_version
    if release_type is MINOR or current_version < Version(1):
        # do not change an early development version to a major release
        # that type of change should be more explicit (edit versions).
        return base_version.bump_release(index=1)
    return base_version.bump_release(index=0)


def update_versions(
    file_versions: list[FileVersion],
    release_type: ReleaseType = None,
) -> Version:
    """Update all versions specified in config + CHANGELOG.rst."""
    file_version.version = release_version = get_release_version(
        (current_ver := (file_version := file_versions[0]).version),
        release_type)
    if SIMULATE is True:  # noinspection PyUnboundLocalVariable
        print(f'change file versions from {current_ver} to {release_version}')
    for file_version in file_versions[1:]:
        file_version.version = release_version
    return release_version


def commit(version: Version):
    args = ('git', 'commit', '--all', f'--message=release: v{version}')
    if SIMULATE is True:
        print(' '.join(args))
        return
    check_call(args)


def commit_and_tag_version_change(release_version: Version):
    commit(release_version)
    git_tag = ('git', 'tag', '-a', f'v{release_version}', '-m', '')
    if SIMULATE is True:
        print(' '.join(git_tag))
        return
    check_call(git_tag)


def upload_to_pypi():
    setup = ('python', 'setup.py', 'sdist', 'bdist_wheel')
    twine = ('twine', 'upload', 'dist/*')
    if SIMULATE is True:
        print(f"{' '.join(setup)}\n{' '.join(twine)}")
        return
    try:
        check_call(setup)
        check_call(twine)
    finally:
        for d in ('dist', 'build'):
            Path(d).rmtree_p()


def update_changelog(release_version: Version):
    """Change the title of initial "Unreleased" section to the new version.

    Note: "Unreleased" and "CHANGELOG" are the recommendations of
        https://keepachangelog.com/ .
    """
    try:
        with open('CHANGELOG.rst', 'rb+') as f:
            changelog = f.read()
            if changelog[:22] != b'Unreleased\n----------\n':
                return
            if SIMULATE is True:
                print(
                    'Replace the "Unreleased" section of "CHANGELOG.rst" with '
                    f'v{release_version}')
                return
            ver_bytes = f'v{release_version}'.encode()
            f.seek(0)
            f.write(b'%b\n%b\n%b' % (
                ver_bytes, b'-' * len(ver_bytes), changelog[22:]))
            f.truncate()
    except FileNotFoundError:
        if SIMULATE is True:
            print('CHANGELOG.rst not found')


def main(
    type: ReleaseType = None, upload: bool = True, push: bool = True,
    simulate: bool = False, path: str = None,
):
    global SIMULATE
    SIMULATE = simulate

    if path is not None:
        Path(path).chdir()

    assert check_output(('git', 'branch', '--show-current')) == b'master\n'
    assert check_output(('git', 'status', '--porcelain')) == b''

    with get_file_versions() as file_versions:
        release_version = update_versions(file_versions, type)
        update_changelog(release_version)
        commit_and_tag_version_change(release_version)

        if upload is True:
            upload_to_pypi()

        # prepare next dev0
        new_dev_version = update_versions(file_versions, DEV)
        commit(new_dev_version)

    if push is True:
        if SIMULATE is True:
            print('git push')
        else:
            check_call(('git', 'push', '--follow-tags'))


def console_scripts_entry_point():
    run(main)
