__version__ = '0.44.0'
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from glob import glob
from logging import debug, info, warning
from os import chdir, listdir, remove
from re import IGNORECASE, Match, match, search
from shutil import rmtree
from subprocess import (
    CalledProcessError,
    TimeoutExpired,
    check_call,
    check_output,
    run,
)
from typing import Annotated, Any

from cyclopts import App, Parameter
from parver import Version
from tomlkit import TOMLDocument, parse
from tomlkit.container import Container


class ReleaseType(Enum):
    DEV = 'dev'
    PATCH = 'patch'
    MINOR = 'minor'
    MAJOR = 'major'


simulation = False


class VersionFile:
    """Wraps around a version variable in a file. Caches reads."""

    __slots__ = '_file', '_offset', '_trail', '_version'

    def __init__(self, path: str):
        file = self._file = open(path, 'r+', newline='\n')
        text = file.read()
        if simulation is True:
            print(f'* reading {path}')
            from io import StringIO

            self._file = StringIO(text)
        match: Match = search(r'\b__version__\s*=\s*([\'"])(.*?)\1', text)  # type: ignore
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


def check_setup_cfg():
    setup_cfg = open('setup.cfg', encoding='utf8').read()
    if 'tests_require' in setup_cfg:
        raise SystemExit(
            '`tests_require` in setup.cfg is deprecated; '
            'use the following sample instead:'
            '\n```'
            '\n[options.extras_require]'
            '\ntests ='
            '\n    pytest'
            '\n    pytest-cov'
            '\n```'
        )
    if 'setup_requires' in setup_cfg:
        raise SystemExit('`setup_requires` is deprecated')
    raise SystemExit('convert setup.cfg to pyproject.toml using `ini2toml`')


def check_no_old_conf(ignore_dist: bool) -> None:
    entries = set(listdir('.'))

    if 'r3l3453.json' in entries:
        warning(
            'Removed r3l3453.json as it is not needed anymore.\n'
            'Version path should be specified in pyproject.toml.'
        )
        remove('r3l3453.json')

    if 'setup.py' in entries:
        raise SystemExit(
            '\nsetup.py was found\nTry `setuptools-py2cfg` to '
            'convert setup.py to setup.cfg and '
            'then convert setup.cfg to pyproject.toml using `ini2toml`'
        )

    if 'setup.cfg' in entries:
        check_setup_cfg()

    if 'MANIFEST.in' in entries:
        raise SystemExit(
            'Use [tool.flit.sdist] instead of `MANIFEST.in` file.'
            'For example:\n'
            '```\n'
            '[tool.flit.sdist]\n'
            "include = ['doc/']\n"
            "exclude = ['doc/*.html']\n"
            '```\n'
            'For more infor refer to:\n'
            'https://flit.pypa.io/en/stable/pyproject_toml.html?highlight=exclude#sdist-section'
        )

    if 'pytest.ini' in entries:
        warning(
            'Removed pytest.ini; settings will be added to pyproject.toml.'
        )
        remove('pytest.ini')

    if (
        ignore_dist is False
        and 'dist' in entries
        and (dist_entries := listdir('./dist'))
    ):
        raise SystemExit(
            '`dist` directory exists and is not empty. Entries:\n'
            f'{dist_entries}\n'
            'Clear it or use `--ignore-dist` option.'
        )


@contextmanager
def read_version_file(
    version_path: str,
) -> Generator[VersionFile, Any, Any]:
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
        if simulation is True:
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
    current_version: Version, release_type: ReleaseType | None = None
) -> Version:
    """Return the next version according to git log."""
    if release_type is ReleaseType.DEV:
        if current_version.is_devrelease:
            return current_version.bump_dev()
        return current_version.bump_release(index=2).bump_dev()

    base_version = current_version.base_version()  # removes devN

    if release_type is None:
        release_type = get_release_type(base_version)
        if simulation is True:
            print(f'* {release_type}')

    if release_type is ReleaseType.PATCH:
        return base_version
    if release_type is ReleaseType.MINOR:
        return base_version.bump_release(index=1)
    return base_version.bump_release(index=0)


def update_version(
    version_file: VersionFile,
    release_type: ReleaseType | None = None,
) -> Version:
    """Update all versions specified in config + CHANGELOG.rst."""
    current_ver = version_file.version
    version_file.version = release_version = get_release_version(
        current_ver, release_type
    )
    if simulation is True:  # noinspection PyUnboundLocalVariable
        print(f'* change file version from {current_ver} to {release_version}')
    version_file.version = release_version
    return release_version


def commit(message: str):
    args = ('git', 'commit', '--all', f'--message={message}')
    if simulation is True:
        print('* ' + ' '.join(args))
        return
    check_call(args)


def commit_and_tag(release_version: Version):
    commit(f'release: v{release_version}')
    git_tag = ('git', 'tag', '-a', f'v{release_version}', '-m', '')
    if simulation is True:
        print('* ' + ' '.join(git_tag))
        return
    check_call(git_tag)


def upload_to_pypi(timeout: int):
    build = ('python', '-m', 'flit', 'build')
    if simulation is True:
        print(build)
    else:
        check_call(build)
    # using `twine` instead of `flit publish` because it has --skip-existing
    # option. See:
    # https://github.com/pypa/flit/issues/678#issuecomment-2156286057
    publish = (
        'python',
        '-m',
        'twine',
        'upload',
        '--skip-existing',
        *glob('dist/*'),
    )
    if simulation is True:
        print(publish)
        return
    try:
        while True:
            try:
                check_call(publish, timeout=timeout)
            except TimeoutExpired:
                timeout += 30
                print(
                    f'\n* TimeoutExpired: next timeout: {timeout};'
                    f' retrying until success.'
                )
                continue
            break
    finally:
        for d in ('dist', 'build'):
            rmtree(d, ignore_errors=True)


def _unreleased_to_version(
    changelog: bytes, release_version: Version, ignore_changelog_version: bool
) -> bytes | bool:
    unreleased = match(rb'[Uu]nreleased\n-+\n', changelog)
    if unreleased is None:
        v_match = match(rb'v([\d.]+\w+)\n', changelog)
        if v_match is None:
            raise SystemExit(
                'CHANGELOG.rst does not start with a version or "Unreleased"'
            )
        changelog_version = Version.parse(v_match[1].decode())
        if changelog_version == release_version:
            print("* CHANGELOG's version matches release_version")
            return True
        if ignore_changelog_version is not False:
            print('* ignoring non-matching CHANGELOG version')
            return True
        raise SystemExit(
            f"CHANGELOG's version ({changelog_version}) does not "
            f'match release_version ({release_version}). '
            'Use --ignore-changelog-version ignore this error.'
        )

    title = f'v{release_version} ({datetime.now(UTC):%Y-%m-%d})'

    if simulation is True:
        print(
            f'* replace the "Unreleased" section of "CHANGELOG.rst" with "{title}"'
        )
        return True

    return b'%b\n%b\n%b' % (
        title.encode(),
        b'-' * len(title),
        changelog[unreleased.end() :],
    )


def changelog_unreleased_to_version(
    release_version: Version, ignore_changelog_version: bool
) -> bool:
    """Change the title of initial "Unreleased" section to the new version.

    Return False if changelog does not exist, True otherwise.

    "Unreleased" and "CHANGELOG" are the recommendations of
        https://keepachangelog.com/ .
    """
    try:
        with open('CHANGELOG.rst', 'rb+') as f:
            changelog = f.read()
            new_changelog = _unreleased_to_version(
                changelog, release_version, ignore_changelog_version
            )
            if new_changelog is True:
                return True
            f.seek(0)
            f.write(new_changelog)  # type: ignore
            f.truncate()
    except FileNotFoundError:
        if simulation is True:
            print('* CHANGELOG.rst not found')
        return False
    return True


def changelog_add_unreleased():
    if simulation is True:
        print('* adding Unreleased section to CHANGELOG.rst')
        return
    with open('CHANGELOG.rst', 'rb+') as f:
        changelog = f.read()
        f.seek(0)
        f.write(b'Unreleased\n----------\n* \n\n' + changelog)


with open(
    f'{__file__}/../cookiecutter/{{{{cookiecutter.project_name}}}}/pyproject_template.toml',
    'rb',
) as f:
    cc_pyproject_content = f.read()
cc_pyproject: TOMLDocument = parse(cc_pyproject_content)


def check_build_system(pyproject: TOMLDocument) -> None:
    try:
        build_system = pyproject['build-system']
    except KeyError:
        info('skipping [build-system] (not found)')
        return
    # https://github.com/sdispater/tomlkit/issues/331
    build_system.update(cc_pyproject['build-system'])  # type: ignore


def check_pyright(tool: Container) -> None:
    pyright = tool.get('pyright')
    cc_pyright: Any = cc_pyproject['tool']['pyright']  # type: ignore
    if pyright is None:
        tool['pyright'] = cc_pyright
        return
    if pyright.keys() < cc_pyright.keys():
        pyright |= cc_pyright | pyright


def check_ruff(tool: Container):
    if 'isort' in tool:
        del tool['isort']
        warning('[isort] was removed from pyproject; use ruff instead.')

    tool['ruff'] = cc_pyproject['tool']['ruff']  # type: ignore

    format_output = check_output(['ruff', 'format', '.'])
    if b' reformatted' in format_output:
        warning('ruff reformatted files')
    elif b' left unchanged' not in format_output:
        warning(
            f'Unexpected ruff format output: `{format_output.rstrip().decode()}`'
        )

    # ruff may add a unified command for linting and formatting.
    # Waiting for https://github.com/astral-sh/ruff/issues/8232 .
    if run(['ruff', 'check', '--fix']).returncode != 0:
        warning('ruff check --fix returned non-zero')


def check_pytest(tool: Container):
    cc_pytest: Any = cc_pyproject['tool']['pytest']['ini_options']  # type: ignore
    tool['pytest']['ini_options'] |= cc_pytest  # type: ignore


def check_tool(pyproject: TOMLDocument) -> None:
    tool: Container = pyproject['tool']  # type: ignore
    check_pyright(tool)
    check_ruff(tool)
    check_pytest(tool)
    if tool.get('setuptools') is not None:
        warning('Removing setuptools from pyproject; use flit instead.')
        del tool['setuptools']


def check_project(pyproject: TOMLDocument) -> None:
    project = pyproject.get('project')
    if project is None:
        pyproject['project'] = cc_pyproject['project']
        raise SystemExit(
            'pyproject.toml did not have a [project] section. '
            '`requires-python` field is required.'
        )
    if project.get('requires-python') is None:
        required_python = input(
            'What is the minimum required python version for this project? (e.g. 3.12)\n'
        )
        project['requires-python'] = required_python
    if project.get('urls') is None:
        if (name := project.get('name')) is not None:
            warning('adding Homepage to project urls')
            project['urls'] = {'Homepage': f'https://github.com/5j9/{name}'}


# @cache
# def fill_cookiecutter_template(match: Match):
#     return input(f'Enter the replacement value for {match[0]}:\n')


def write_pyproject(content: bytes):
    debug('writing to pyproject.toml')
    with open('pyproject.toml', 'wb') as f:
        f.write(content)


def check_pyproject_toml() -> TOMLDocument:
    # https://packaging.python.org/tutorials/packaging-projects/
    try:
        with open('pyproject.toml', 'rb') as f:
            pyproject_content = f.read()
    except FileNotFoundError:
        write_pyproject(cc_pyproject_content)
        raise SystemExit('pyproject.toml did not exist. Template was created.')

    pyproject = parse(pyproject_content)

    try:
        check_project(pyproject)
        check_build_system(pyproject)
        check_tool(pyproject)
    finally:
        new_pyproject_content = pyproject.as_string().encode()
        if new_pyproject_content != pyproject_content:
            write_pyproject(new_pyproject_content)

    return pyproject


def get_version_path(pyproject: TOMLDocument) -> str | None:
    try:
        # Package may have different names for installation and import, see:
        # https://flit.pypa.io/en/stable/pyproject_toml.html#module-section
        name = pyproject['tool']['flit']['module']['name']  # type: ignore
    except KeyError:
        name = pyproject['project'].get('name')  # type: ignore
    if name is None:
        return None
    return f'{name}/__init__.py'


def check_git_status(ignore_git_status: bool):
    status = check_output(('git', 'status', '--porcelain'))
    if status:
        if ignore_git_status:
            print(f'* ignoring git {status=}')
        else:
            raise SystemExit(
                'git status is not clean. '
                'Use --ignore-git-status to ignore this error.'
            )
    branch = (
        check_output(('git', 'branch', '--show-current')).rstrip().decode()
    )
    if branch not in ('master', 'main'):
        if ignore_git_status:
            print(f'* ignoring git branch ({branch} not being main or master.')
        else:
            raise SystemExit(
                f'git is on {branch} branch (not main or master). '
                'Use --ignore-git-status to ignore this error.'
            )


def reset_and_delete_tag(release_version):
    print('* reset_and_delete_tag')
    check_call(['git', 'reset', '@^'])
    check_call(['git', 'tag', '--delete', f'v{release_version}'])


app = App(version=__version__)


@app.default
def main(
    *,
    rtype: ReleaseType | None = None,
    upload: bool = True,
    push: bool = True,
    simulate: Annotated[bool, Parameter(('--simulate', '-s'))] = False,
    path: str | None = None,
    ignore_changelog_version: bool = False,
    ignore_git_status: bool = False,
    ignore_dist: bool = False,
    timeout: int = 90,
):
    global simulation
    simulation = simulate
    print(f'* r3l3453 v{__version__}')
    if path is not None:
        chdir(path)

    check_no_old_conf(ignore_dist)
    pyproject = check_pyproject_toml()

    version_path = get_version_path(pyproject)
    if version_path is None:
        info('skipping rest of checks since version_path was not found')
        return

    check_git_status(ignore_git_status)

    with read_version_file(version_path) as version_file:
        release_version = update_version(version_file, rtype)
        changelog_exists = changelog_unreleased_to_version(
            release_version, ignore_changelog_version
        )
        commit_and_tag(release_version)

        if upload is True:
            try:
                upload_to_pypi(timeout)
            except BaseException as e:
                reset_and_delete_tag(release_version)
                if isinstance(e, KeyboardInterrupt):
                    print('KeyboardInterrupt')
                    return
                raise e

        # prepare next dev0
        new_dev_version = update_version(version_file, ReleaseType.DEV)
        if changelog_exists:
            changelog_add_unreleased()
        commit(f'chore(__version__): bump to {new_dev_version}')

    if push is False:
        return

    if simulation is True:
        print('* git push')
        return

    while True:
        try:
            check_call(('git', 'push', '--follow-tags'))
        except CalledProcessError:
            warning(
                'CalledProcessError on git push. Will retry until success.'
            )
            continue
        break


@app.command
def init():
    from pathlib import Path

    from cookiecutter.main import cookiecutter

    cookiecutter_dir = Path(__file__).parent / 'cookiecutter'
    cookiecutter(str(cookiecutter_dir))


def console_scripts_entry_point():
    app()
