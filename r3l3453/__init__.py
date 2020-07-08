#!/usr/bin/env bash
__version__ = '0.5.0.dev0'

from contextlib import contextmanager
from enum import Enum
from logging import warning
from re import search
from subprocess import CalledProcessError, check_call, check_output

from path import Path
from parver import Version
from tomlkit import parse
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


class FileVersion:
    """Wraps around a version variable in a file. Caches reads."""
    __slots__ = '_file', '_offset', '_version', '_trail'

    def __init__(self, path: Path, variable: str):
        file = self._file = path.open('r+', newline='\n')
        text = file.read()
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
        file.flush()
        self._version = version

    def close(self):
        self._file.close()


@contextmanager
def get_file_versions() -> list[FileVersion]:
    with open('pyproject.toml', 'r') as f:
        toml = parse(f.read())
    append = (file_versions := []).append
    for path_version in toml['tool']['r3l3453']['version_paths']:
        path, variable = path_version.split(':', 1)
        append(FileVersion(Path(path), variable))
    try:
        yield file_versions
    finally:
        for fv in file_versions:
            fv.close()


def get_release_type(last_version) -> ReleaseType:
    """Return 0 for major, 1 for minor and 2 for a patch release.

    According to https://www.conventionalcommits.org/en/v1.0.0/ .
    """
    try:
        log = check_output(
            ('git', 'log', '--format=%B', f'v{last_version}..@'))
    except CalledProcessError:
        warning('tag `v%s` not found', last_version)
        try:
            last_version_tag = check_output(
                ('git', 'describe', '--match', 'v[0-9]*'))
            warning('using `%s` instead', last_version_tag)
            log = check_output(
                ('git', 'log', '--format=%B', f'{last_version_tag}..@'))
        except CalledProcessError:  # there are no version tags
            warning('no version tags found\nchecking all commits')
            log = check_output(('git', 'log', '--format=%B'))
    if rb'!:' in log:
        return MAJOR
    if b'\nBREAKING CHANGE:' in log:
        return MAJOR
    if b'\nfeat:' in b'\n' + log:
        return MINOR
    return PATCH


def get_release_version(
    old_version: Version, release_type: ReleaseType = None
) -> Version:
    """Return the next version according to git log."""
    if release_type is DEV:
        return old_version.bump_dev()
    base_version = old_version.base_version()  # removes devN
    if release_type is None:
        release_type = get_release_type(old_version)
    if release_type is PATCH:
        return base_version
    if old_version < Version(1) or release_type is MINOR:
        # do not change an early development version to a major release
        # that type of change should be more explicit (edit versions).
        return base_version.bump_release(index=1)
    return base_version.bump_release(index=0)


def update_versions(
    file_versions: list[FileVersion],
    release_type: ReleaseType = None,
) -> Version:
    """Update all versions specified in config."""
    for file_version in file_versions:
        file_version.version = get_release_version(
            file_version.version, release_type)
    # noinspection PyUnboundLocalVariable
    return file_version.version


def commit(version: Version):
    check_call(('git', 'commit', '--all', f'--message=release: v{version}'))


def commit_and_tag_version_change(release_version: Version):
    commit(release_version)
    check_call(('git', 'tag', '-a', f'v{release_version}', '-m', ''))


def upload_to_pypi():
    try:
        check_call(('python', 'setup.py', 'sdist', 'bdist_wheel'))
        check_call(('twine', 'upload', 'dist/*'))
    finally:
        for d in ('dist', 'build'):
            Path(d).rmtree_p()


def main(type: ReleaseType = None, upload: bool = True, push: bool = True):
    assert check_output(('git', 'branch', '--show-current')) == b'master\n'
    assert check_output(('git', 'status', '--porcelain')) == b''

    with get_file_versions() as file_versions:
        release_version = update_versions(file_versions, type)
        commit_and_tag_version_change(release_version)

        if upload is True:
            upload_to_pypi()

        # prepare next dev0
        new_dev_version = update_versions(file_versions, DEV)
        commit(new_dev_version)

    if push is True:
        check_call(('git', 'push'))


def console_scripts_entry_point():
    run(main)
