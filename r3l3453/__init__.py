#!/usr/bin/env bash
__version__ = '0.dev0'

from logging import warning
from re import search
from subprocess import CalledProcessError, check_call, check_output
from typing import Literal

from parver import Version
from path import Path
from tomlkit import parse


def get_path_vvars() -> list[tuple[Path, str]]:
    with open('pyproject.toml', 'r') as f:
        toml = parse(f.read())
    path_versions = []
    for path_version in toml['tool']['r3l3453']['version_paths']:
        path, version = path_version.split(':', 1)
        path_versions += (Path(path), version),
    return path_versions


def get_release_type(last_version) -> Literal[0, 1, 2]:
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
        return 0
    if b'\nBREAKING CHANGE:' in log:
        return 0
    if b'\nfeat:' in b'\n' + log:
        return 1
    return 2


def get_release_version(old_version: Version) -> Version:
    """Return the next version according to git log."""
    assert old_version.is_devrelease
    undev = old_version.base_version()
    release_type = get_release_type(old_version)
    if release_type == 2:
        return undev
    if old_version < Version(1):
        # do not change an early development version to a major release
        # that type of change should be more explicit.
        return undev.bump_release(index=1)
    return undev.bump_release(index=release_type)


def update_version(
    path_vvar_tuples: list[tuple[Path, str]],
    old_version: Version = None, new_version: Version = None
) -> Version:
    """Update all versions specified in config."""
    last_version = None
    for path, var_name in path_vvar_tuples:
        with path.open('r+', newline='\n') as f:
            text: str = f.read()
            if old_version is None:
                old_version_match = search(
                    r'\b' + var_name + r'\s*=\s*([\'"])(.*?)\1', text)
                old_version = Version.parse(old_version_match[2])
                s, e = old_version_match.span(2)
                if new_version is None:
                    new_version = get_release_version(old_version)
                text = text[:s] + str(new_version) + text[e:]
            else:
                if new_version is None:
                    new_version = get_release_version(old_version)
                text = text.replace(str(old_version), str(new_version), 1)
            f.seek(0)
            f.write(text)
            f.truncate()
        assert last_version is None or last_version == new_version, \
            'versions are not equal'
        last_version = new_version
    return new_version


def commit(version: Version):
    check_call(('git', 'commit', '--all', f'--message=release: v{version}'))


def commit_and_tag_version_change(release_version: Version):
    commit(release_version)
    check_call(('git', 'tag', '-a', f'v{release_version}', '-m', ''))


def main():
    assert check_output(('git', 'branch', '--show-current')) == b'master\n'
    assert check_output(('git', 'status', '--porcelain')) == b''

    path_vvar_tuples = get_path_vvars()
    release_version = update_version(path_vvar_tuples)
    commit_and_tag_version_change(release_version)

    try:
        check_call(('python', 'setup.py', 'sdist', 'bdist_wheel'))
        check_call(('twine', 'upload', 'dist/*'))
    finally:
        for d in ('dist', 'build'):
            Path(d).rmtree_p()

    # prepare next dev0
    new_dev_version = release_version.bump_release(index=2).bump_dev()
    update_version(path_vvar_tuples, release_version, new_dev_version)
    commit(new_dev_version)

    check_call(('git', 'push'))


if __name__ == '__main__':
    main()
