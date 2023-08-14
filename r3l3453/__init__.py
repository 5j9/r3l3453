#!/usr/bin/env bash
__version__ = '0.27.0'
import tomllib
from contextlib import AbstractContextManager, contextmanager
from enum import Enum
from logging import warning
from re import IGNORECASE, match, search
from subprocess import (
    CalledProcessError,
    TimeoutExpired,
    check_call,
    check_output,
)

import typer
from parver import Version
from path import Path


class ReleaseType(Enum):
    DEV = 'dev'
    PATCH = 'patch'
    MINOR = 'minor'
    MAJOR = 'major'


SIMULATE = False


class VersionFile:
    """Wraps around a version variable in a file. Caches reads."""

    __slots__ = '_file', '_offset', '_version', '_trail'

    def __init__(self, path: Path):
        file = self._file = path.open('r+', newline='\n')
        text = file.read()
        if SIMULATE is True:
            print(f'* reading {path}')
            from io import StringIO

            self._file = StringIO(text)
        match = search(r'\b__version__\s*=\s*([\'"])(.*?)\1', text)
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


def check_no_old_conf() -> None:
    files = {f.name for f in Path('.').files()}
    if 'r3l3453.json' in files:
        raise RuntimeError(
            'Remove r3l3453.json as it is not needed anymore.\n'
            'Version path should be specified in setup.cfg.\n'
            '[metadata]\n'
            'version = attr: package.__version__'
        )

    if 'setup.py' in files:
        raise RuntimeError(
            '\nsetup.py was found\nTry `setuptools-py2cfg` to '
            'convert setup.py to setup.cfg and '
            'then convert setup.cfg to pyproject.toml using `ini2toml`'
        )

    if 'setup.cfg' not in files:
        return
    setup_cfg = Path('setup.cfg').open('r', encoding='utf8').read()
    if 'tests_require' in setup_cfg:
        raise RuntimeError(
            '`tests_require` in setup.cfg is deprecated; '
            'use the following sample instead:'
            '\n[options.extras_require]'
            '\ntests ='
            '\n    pytest'
            '\n    pytest-cov'
        )
    if 'setup_requires' in setup_cfg:
        raise RuntimeError('`setup_requires` is deprecated')
    raise RuntimeError('convert setup.cfg to pyproject.toml using `ini2toml`')


@contextmanager
def read_version_file(version_path) -> AbstractContextManager[VersionFile]:
    vf = VersionFile(version_path)
    try:
        yield vf
    finally:
        vf.close()


def get_release_type(base_version: Version) -> ReleaseType:
    """Return release type by analyzing git commits.

    According to https://www.conventionalcommits.org/en/v1.0.0/ .
    """
    try:
        last_version_tag: str = check_output(
            ('git', 'describe', '--match', 'v[0-9]*', '--abbrev=0')
        )[:-1].decode()
        if SIMULATE is True:
            print(f'* {last_version_tag=}')
        log = check_output(
            ('git', 'log', '--format=%B', '-z', f'{last_version_tag}..@')
        )
    except CalledProcessError:  # there are no version tags
        warning('No version tags found. Checking all commits...')
        log = check_output(('git', 'log', '--format=%B'))

    if search(rb'(?:\A|[\0\n])(?:BREAKING CHANGE[(:]|.*?!:)', log):
        if base_version < Version((1,)):
            # Do not bump an early development version to a major release.
            # That type of change should be explicit (via rtype param).
            return ReleaseType.MINOR
        return ReleaseType.MAJOR
    if search(rb'(?:\A|\0)feat[(:]', log, IGNORECASE):
        return ReleaseType.MINOR
    return ReleaseType.PATCH


def get_release_version(
    current_version: Version, release_type: ReleaseType = None
) -> Version:
    """Return the next version according to git log."""
    if release_type is ReleaseType.DEV:
        if current_version.is_devrelease:
            return current_version.bump_dev()
        return current_version.bump_release(index=2).bump_dev()

    base_version = current_version.base_version()  # removes devN

    if release_type is None:
        release_type = get_release_type(base_version)
        if SIMULATE is True:
            print(f'* {release_type}')

    if release_type is ReleaseType.PATCH:
        return base_version
    if release_type is ReleaseType.MINOR:
        return base_version.bump_release(index=1)
    return base_version.bump_release(index=0)


def update_version(
    version_file: VersionFile,
    release_type: ReleaseType = None,
) -> Version:
    """Update all versions specified in config + CHANGELOG.rst."""
    current_ver = version_file.version
    version_file.version = release_version = get_release_version(
        current_ver, release_type
    )
    if SIMULATE is True:  # noinspection PyUnboundLocalVariable
        print(f'* change file version from {current_ver} to {release_version}')
    version_file.version = release_version
    return release_version


def commit(message: str):
    args = ('git', 'commit', '--all', f'--message={message}')
    if SIMULATE is True:
        print('* ' + ' '.join(args))
        return
    check_call(args)


def commit_and_tag(release_version: Version):
    commit(f'release: v{release_version}')
    git_tag = ('git', 'tag', '-a', f'v{release_version}', '-m', '')
    if SIMULATE is True:
        print('* ' + ' '.join(git_tag))
        return
    check_call(git_tag)


def upload_to_pypi(timeout):
    build = ('python', '-m', 'build', '--no-isolation')
    twine = ('twine', 'upload', 'dist/*')
    if SIMULATE is True:
        print(f"* {' '.join(build)}\n* {' '.join(twine)}")
        return
    try:
        check_call(build)
        while True:
            try:
                check_call(twine, timeout=timeout)
            except TimeoutExpired:
                print('\n* TimeoutExpired: will retry until success')
                continue
            break
    finally:
        for d in ('dist', 'build'):
            Path(d).rmtree_p()


def check_update_changelog(
    changelog: bytes, release_version: Version, ignore_changelog_version: bool
) -> bytes | bool:
    unreleased = match(br'[Uu]nreleased\n-+\n', changelog)
    if unreleased is None:
        v_match = match(br'v([\d.]+\w+)\n', changelog)
        if v_match is None:
            raise RuntimeError(
                'CHANGELOG.rst does not start with a version or "Unreleased"'
            )
        changelog_version = Version.parse(v_match[1].decode())
        if changelog_version == release_version:
            print("* CHANGELOG's version matches release_version")
            return True
        if ignore_changelog_version is not False:
            print('* ignoring non-matching CHANGELOG version')
            return True
        raise RuntimeError(
            f"CHANGELOG's version ({changelog_version}) does not "
            f"match release_version ({release_version}). "
            "Use --ignore-changelog-version ignore this error."
        )

    if SIMULATE is True:
        print(
            '* replace the "Unreleased" section of "CHANGELOG.rst" with '
            f'v{release_version}'
        )
        return True

    ver_bytes = f'v{release_version}'.encode()
    return b'%b\n%b\n%b' % (
        ver_bytes,
        b'-' * len(ver_bytes),
        changelog[unreleased.end() :],
    )


def update_changelog(release_version: Version, ignore_changelog_version: bool):
    """Change the title of initial "Unreleased" section to the new version.

    Note: "Unreleased" and "CHANGELOG" are the recommendations of
        https://keepachangelog.com/ .
    """
    try:
        with open('CHANGELOG.rst', 'rb+') as f:
            changelog = f.read()
            new_changelog = check_update_changelog(
                changelog, release_version, ignore_changelog_version
            )
            if new_changelog is True:
                return
            f.seek(0)
            f.write(new_changelog)
            f.truncate()
    except FileNotFoundError:
        if SIMULATE is True:
            print('* CHANGELOG.rst not found')


ISORT = """
[tool.isort]
profile = "black"
line_length = 79
combine_as_imports = true
"""

# 66.1.0 is required for correct handling of sdist files, see:
# https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html#dynamic-metadata
REQUIRED_SETUPTOOLS_VERSION = '66.1.0'

PYPROJECT_TOML = f"""\
[build-system]
requires = [
    "setuptools>={REQUIRED_SETUPTOOLS_VERSION}",
    "wheel",
]
build-backend = "setuptools.build_meta"
{ISORT}
"""


def check_build_system_requires(build_system):
    try:
        requires = build_system['requires']
    except KeyError:
        raise RuntimeError(
            f'[build-system] requires not found {PYPROJECT_TOML}'
        )

    for i in requires:
        if i.startswith('setuptools'):
            _, _, d_ver = i.partition('>=')
            d_ver = Version.parse(d_ver)
            break
    else:
        d_ver = None

    if d_ver is None or (d_ver < Version.parse(REQUIRED_SETUPTOOLS_VERSION)):
        raise RuntimeError(
            f'[build-system] requires `setuptools>={REQUIRED_SETUPTOOLS_VERSION}` {PYPROJECT_TOML}'
        )


def check_build_system_backend(build_system):
    try:
        backend = build_system['build-backend']
    except KeyError:
        backend = None

    if backend != 'setuptools.build_meta':
        raise RuntimeError(
            '`build-backend = "setuptools.build_meta"` not found in '
            f'[build-system] of pyproject.toml {PYPROJECT_TOML}'
        )


def check_build_system(d):
    try:
        build_system = d['build-system']
    except KeyError:
        raise RuntimeError(
            f'[build-system] not found in pyproject.toml {PYPROJECT_TOML}'
        )
    check_build_system_backend(build_system)
    check_build_system_requires(build_system)


def check_isort(tool: dict):
    try:
        isort = tool['isort']
    except KeyError:
        with open('pyproject.toml', 'a', encoding='utf8') as f:
            f.write(ISORT)
        raise RuntimeError('[tool.isort] was added to pyproject.toml')

    if isort != {
        'profile': 'black',
        'line_length': 79,
        'combine_as_imports': True,
    }:
        raise RuntimeError(
            f'[tool.isort] is parameters are incomplete. Add {ISORT}'
        )

    output = check_output(['isort', '.'])
    if b'Fixing ' in output:
        raise RuntimeError('commit isort modifications')


def check_setuptools(tool: dict) -> Path:
    attr = tool['setuptools']['dynamic']['version']['attr']
    return Path(attr.removesuffix('.__version__')) / '__init__.py'


def check_tool(d) -> Path:
    tool = d['tool']
    check_isort(tool)
    return check_setuptools(tool)


def check_pyproject_toml() -> Path:
    # https://packaging.python.org/tutorials/packaging-projects/
    try:
        with open('pyproject.toml', 'rb') as f:
            d = tomllib.load(f)
    except FileNotFoundError:
        with open('pyproject.toml', 'w', encoding='utf8') as f:
            f.write(PYPROJECT_TOML)
        raise FileNotFoundError('pyproject.toml was not found; sample created')

    check_build_system(d)
    return check_tool(d)


def check_git_status(ignore_git_status: bool):
    status = check_output(('git', 'status', '--porcelain'))
    if status:
        if ignore_git_status:
            print(f'* ignoring git {status=}')
        else:
            raise RuntimeError(
                'git status is not clean. '
                'Use --ignore-git-status to ignore this error.'
            )
    branch = (
        check_output(('git', 'branch', '--show-current')).rstrip().decode()
    )
    if branch != 'master':
        if ignore_git_status:
            print(f'* ignoring git branch ({branch} != master)')
        else:
            raise RuntimeError(
                f'git is on {branch} branch. '
                'Use --ignore-git-status to ignore this error.'
            )


def reset_and_delete_tag(release_version):
    print('* reset_and_delete_tag')
    check_call(['git', 'reset', '@^'])
    check_call(['git', 'tag', '--delete', f'v{release_version}'])


def version_callback(value: bool):
    if not value:
        return
    print(f"{__version__}")
    raise typer.Exit()


def main(
    rtype: ReleaseType = None,
    upload: bool = True,
    push: bool = True,
    simulate: bool = False,
    path: str = None,
    ignore_changelog_version: bool = False,
    ignore_git_status: bool = False,
    timeout: int = 30,
    version: bool = typer.Option(None, "--version", callback=version_callback),
):
    global SIMULATE
    SIMULATE = simulate
    print(f'* r3l3453 v{__version__}')
    if path is not None:
        Path(path).chdir()

    check_no_old_conf()
    version_path = check_pyproject_toml()
    check_git_status(ignore_git_status)

    with read_version_file(version_path) as version_file:
        release_version = update_version(version_file, rtype)
        update_changelog(release_version, ignore_changelog_version)
        commit_and_tag(release_version)

        if upload is True:
            try:
                upload_to_pypi(timeout)
            except Exception as e:
                reset_and_delete_tag(release_version)
                raise e

        # prepare next dev0
        new_dev_version = update_version(version_file, ReleaseType.DEV)
        commit(f'chore(__version__): bump to {new_dev_version}')

    if push is True:
        if SIMULATE is True:
            print('* git push')
        else:
            check_call(('git', 'push', '--follow-tags'))


def console_scripts_entry_point():
    typer.run(main)
