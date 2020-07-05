#!/usr/bin/env bash
__version__ = '0.1.dev0'


from re import search
from subprocess import check_call, check_output

from parver import Version
from path import Path
from tomlkit import parse


def get_path_vvars() -> list[Path, str]:
    with open('pyproject.toml', 'r') as f:
        toml = parse(f.read())
    path_versions = []
    for path_version in toml['tool']['release']['version_paths']:
        path, version = path_version.split(':', 1)
        path_versions += (Path(path), version),
    return path_versions


path_vvar_tuples = get_path_vvars()


def ask_new_version(old_version: Version) -> Version:
    assert old_version.is_devrelease
    major = old_version.bump_release(index=0).base_version()
    minor = old_version.bump_release(index=1).base_version()
    patch = old_version.bump_release(index=2).base_version()
    index = int(input(
        f'Current version is:\n'
        f'  {old_version}\n'
        'Enter release type:\n'
        f'   0: major: {major}\n'
        f'   1: minor: {minor}\n'
        f'   2: patch: {patch}\n'))
    return (major, minor, patch)[index]


def update_version(
    old_version: Version = None, new_version: Version = None
) -> Version:
    last_version = None
    for path, var_name in path_vvar_tuples:
        with path.open('r+') as f:
            text: str = f.read()
            if old_version is None:
                old_version_match = search(
                    r'\b' + var_name + r'\s*=\s*([\'"])(.*?)\1', text)
                old_version = Version.parse(old_version_match[2])
                s, e = old_version_match.span(2)
                if new_version is None:
                    new_version = ask_new_version(old_version)
                text = text[:s] + str(new_version) + text[e:]
            else:
                if new_version is None:
                    new_version = ask_new_version(old_version)
                text = text.replace(str(old_version), str(new_version), 1)
            f.seek(0)
            f.write(text)
            f.truncate()
        assert last_version is None or last_version == new_version, \
            'versions are not equal'
        last_version = new_version
    return new_version


def commit(v_version: str):
    check_call(('git', 'commit', '--all', f'--message=release: {v_version}'))


def commit_and_tag_version_change(release_version: Version):
    v_version = f'v{release_version}'
    commit(v_version)
    check_call(('git', 'tag', '-a', v_version, '-m', ''))


assert check_output(('git', 'branch', '--show-current')) == b'master\n'
assert check_output(('git', 'status', '--porcelain')) == b''


release_version = update_version()
commit_and_tag_version_change(release_version)


try:
    check_call(('python', 'setup.py', 'sdist', 'bdist_wheel'))
    check_call(('twine', 'upload', 'dist/*'))
finally:
    for d in ('dist', 'build'):
        Path(d).rmtree()

# prepare next dev0
new_dev_version = release_version.bump_release(index=2).bump_dev()
update_version(release_version, new_dev_version)
commit(f'v{str(new_dev_version)}')

check_call(('git', 'push'))
