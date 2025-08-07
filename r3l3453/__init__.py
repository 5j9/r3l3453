__version__ = '0.52.0'
from datetime import UTC, datetime
from enum import Enum
from os import chdir, getenv, listdir, remove
from pathlib import Path
from re import IGNORECASE, Match, match, search
from shutil import rmtree
from subprocess import (
    PIPE,
    CalledProcessError,
    TimeoutExpired,
    check_call,
    check_output,
    run,
)
from sys import stderr
from time import sleep
from typing import Annotated, Any

from cyclopts import App, Parameter
from loguru import logger
from tomlkit import TOMLDocument, parse
from tomlkit.container import Container


class ReleaseType(Enum):
    DEV = 'dev'
    PATCH = 'patch'
    MINOR = 'minor'
    MAJOR = 'major'


simulation = False


logger.remove()
logger.add(
    stderr,
    format='<level>{level: <8}</level><blue>{file.path}:{line}</blue>\t{message}',
    colorize=True,
    backtrace=True,  # Optional: to include full backtrace on errors
    diagnose=True,  # Optional: to include variable values in backtrace
)

warning = logger.warning
info = logger.info
debug = logger.debug


project_entries: set[str]


class VersionManager:
    __slots__ = '_init_file', '_offset', '_pyproject', '_trail', '_version'

    def __init__(self, pyproject: TOMLDocument):
        # relative path assuming cwd is root
        self._pyproject = pyproject
        path = f'{pyproject["tool"]["uv"]["build-backend"]["module-name"]}/__init__.py'  # type: ignore
        file = self._init_file = open(
            path, 'r+', newline='\n', encoding='utf8'
        )
        text = file.read()
        if simulation is True:
            info(f'reading {path}')
            from io import StringIO

            self._init_file = StringIO(text)
        match: Match = search(r'\b__version__\s*=\s*([\'"])(.*?)\1', text)  # type: ignore
        self._offset, end = match.span(2)
        self._trail = text[end:]
        self._version: str = match[2]

    @property
    def init_version(self) -> str:
        return self._version

    @init_version.setter
    def init_version(self, version: str):
        if simulation:
            info(f'changing init version from {self._version} to {version}')
        else:
            (file := self._init_file).seek(self._offset)
            file.write(str(version) + self._trail)
            file.truncate()
        self._version = version

    def close(self):
        self._init_file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if simulation:  # restore pyproject version
            write_pyproject(self._pyproject.as_string().encode())
        self.close()

    def _uv_bump(self, *bumps: str):
        args = ['uv', 'version']

        if self._pyproject['project']['name'] == 'r3l3453':  # type: ignore
            # syncing will fail while r3l3453 is running
            args.append('--no-sync')

        for bump in bumps:
            args += ['--bump', bump]

        # --dry-run fials to report correct version bumps during simulation
        # if simulation:
        #     args.append('--dry-run')

        logger.debug(args)
        cp = run(args, stdout=PIPE, check=True)
        out = cp.stdout.decode().rstrip()
        logger.info(out)
        new_version = out.partition(' => ')[2]
        self.init_version = new_version
        return new_version

    def bump(self, release_type: ReleaseType | None):
        if release_type is ReleaseType.DEV:
            if '.dev' in self.init_version:
                return self._uv_bump('dev')
            # stable version
            return self._uv_bump('patch', 'dev')

        if release_type is None:
            release_type = get_release_type(self.init_version)
            if simulation is True:
                info(f'{release_type = }')

        # bumping to major/minor/patch will remove pre-release parts
        if release_type is ReleaseType.PATCH:
            try:
                return self._uv_bump('stable')
            except CalledProcessError:  # already on stable version
                return self._uv_bump('patch')
        if release_type is ReleaseType.MINOR:
            return self._uv_bump('minor')
        return self._uv_bump('major')


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
    entries = project_entries
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
            'Use `source-exclude` in [tool.uv.build-backend] instead of `MANIFEST.in` file.'
            'For example:\n'
            '```\n'
            '[tool.uv.build-backend]\n'
            "source-include = ['doc/**']\n"
            "source-exclude = ['doc/*.html']\n"
            '```\n'
            'For more infor refer to:\n'
            'https://docs.astral.sh/uv/concepts/build-backend/#file-inclusion-and-exclusion'
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


def get_release_type(base_version: str) -> ReleaseType:
    """Return release type by analyzing git commits.

    According to https://www.conventionalcommits.org/en/v1.0.0/ .
    """
    try:
        last_version_tag: str = check_output(
            ('git', 'describe', '--match', 'v[0-9]*', '--abbrev=0')
        )[:-1].decode()
        if simulation is True:
            info(f'{last_version_tag=}')
        # -z: Separate the commits with NULs instead of newlines.
        log = check_output(
            ('git', 'log', '--format=%B', '-z', f'{last_version_tag}..@')
        )
    except CalledProcessError:  # there are no version tags
        warning('No version tags found. Checking all commits...')
        log = check_output(('git', 'log', '--format=%B'))

    if search(rb'(?:\A|\0).*?!:|\nBREAKING CHANGE:', log):
        if base_version.startswith('0.'):
            # Do not bump an early development version to a major release.
            # That type of change should be explicit.
            logger.debug('Ignoring major change in initial development phase.')
            return ReleaseType.MINOR
        return ReleaseType.MAJOR
    if search(rb'(?:\A|\0)feat[(:]', log, IGNORECASE):
        return ReleaseType.MINOR
    return ReleaseType.PATCH


def commit(message: str):
    args = ('git', 'commit', '--all', f'--message={message}')
    if simulation is True:
        info(' '.join(args))
        return
    check_call(args)


def commit_and_tag(release_version: str):
    commit(f'release: v{release_version}')
    git_tag = ('git', 'tag', '-a', f'v{release_version}', '-m', '')
    if simulation is True:
        info(' '.join(git_tag))
        return
    check_call(git_tag)


def get_pypi_token() -> str:
    token = getenv('UV_PUBLISH_TOKEN')
    if token is not None:
        return token

    # uv does not support reading .pypirc file.
    # https://github.com/astral-sh/uv/issues/7676
    pypirc = Path.home() / Path('.pypirc')

    from configparser import ConfigParser, Error

    config = ConfigParser()

    try:
        config.read_string(pypirc.read_bytes().decode())
    except FileNotFoundError:
        raise SystemExit(f"Error: .pypirc file not found at '{pypirc}'")
    except Error as e:
        raise SystemExit(f'Error parsing .pypirc file: {e!r}')

    try:
        return config['pypi']['password']
    except KeyError as e:
        raise SystemExit(f"config['pypi']['password'] raised {e!r}")


def upload_to_pypi(timeout: int):
    # https://docs.astral.sh/uv/guides/package/#building-your-package
    build = ('uv', 'build')
    if simulation is True:
        info(build)
    else:
        check_call(build)

    token = get_pypi_token()
    # https://docs.astral.sh/uv/guides/package/#publishing-your-package
    publish = ['uv', 'publish', '--token']
    if simulation is True:
        # do not print token
        publish.append('<token>')
        info(publish)
        return
    publish.append(token)
    try:
        while True:
            try:
                check_call(publish, timeout=timeout)
            except TimeoutExpired:
                timeout += 30
                info(
                    # use \n to avoid printing at the end of previous line
                    f'\nTimeoutExpired: next timeout: {timeout};'
                    f' retrying until success.'
                )
                continue
            except CalledProcessError:
                info('Retrying CalledProcessError after 2s until success.')
                sleep(2.0)
                continue
            break
    finally:
        rmtree('dist', ignore_errors=True)


def _unreleased_to_version(
    changelog: bytes, release_version: str, ignore_changelog_version: bool
) -> bytes | bool:
    unreleased = match(rb'[Uu]nreleased\n-+\n', changelog)
    if unreleased is None:
        v_match = match(rb'v([\d.]+\w+)\n', changelog)
        if v_match is None:
            raise SystemExit(
                'CHANGELOG.rst does not start with a version or "Unreleased"'
            )
        changelog_version = v_match[1].decode()
        if changelog_version == release_version:
            info("CHANGELOG's version matches release_version")
            return True
        if ignore_changelog_version is not False:
            info('ignoring non-matching CHANGELOG version')
            return True
        raise SystemExit(
            f"CHANGELOG's version ({changelog_version}) does not "
            f'match release_version ({release_version}). '
            'Use --ignore-changelog-version ignore this error.'
        )

    title = f'v{release_version} ({datetime.now(UTC):%Y-%m-%d})'

    if simulation is True:
        info(
            f'replace the "Unreleased" section of "CHANGELOG.rst" with "{title}"'
        )
        return True

    return b'%b\n%b\n%b' % (
        title.encode(),
        b'-' * len(title),
        changelog[unreleased.end() :],
    )


def changelog_unreleased_to_version(
    release_version: str, ignore_changelog_version: bool
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
            info('CHANGELOG.rst not found')
        return False
    return True


def changelog_add_unreleased():
    if simulation is True:
        info('adding Unreleased section to CHANGELOG.rst')
        return
    with open('CHANGELOG.rst', 'rb+') as f:
        changelog = f.read()
        f.seek(0)
        f.write(b'Unreleased\n----------\n* \n\n' + changelog)


this_dir = Path(__file__).parent
cc_pyproject_content = (
    this_dir
    / 'cookiecutter/{{cookiecutter.project_name}}/pyproject_template.toml'
).read_bytes()
cc_pyproject: TOMLDocument = parse(cc_pyproject_content)


def check_build_system(pyproject: TOMLDocument) -> None:
    """Check build system and update/fix uv build-backend settings.

    Project structure must be flat. (src is not supported yet).
    Namespace packages are not currently supported.

    See:
    https://docs.astral.sh/uv/concepts/build-backend/#namespace-packages
    https://docs.astral.sh/uv/reference/settings/#build-backend_module-name
    """
    try:
        build_system = pyproject['build-system']
    except KeyError:
        info('skipping [build-system] (not found)')
        return
    build_system |= cc_pyproject['build-system']  # type: ignore

    tool: Container = pyproject['tool']  # type: ignore
    project_name: str = pyproject['project']['name']  # type: ignore
    module_name = project_name.replace('.', '_').replace('-', '_')
    uv = {'build-backend': {'module-name': module_name, 'module-root': ''}}
    try:
        tool['uv'] |= uv  # type: ignore
    except KeyError:
        tool['uv'] = uv


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
        raise SystemExit('ruff reformatted files')
    elif b' left unchanged' not in format_output:
        raise SystemExit('Unexpected ruff format output.')

    # ruff may add a unified command for linting and formatting.
    # Waiting for https://github.com/astral-sh/ruff/issues/8232 .
    if run(['ruff', 'check', '--fix']).returncode != 0:
        raise SystemExit('ruff check --fix returned non-zero')


def check_pytest(pyproject: TOMLDocument, tool: Container):
    pytest = tool.get('pytest')

    if pytest is None:
        if 'tests' not in project_entries:
            return
        tool['pytest'] = cc_pyproject['tool']['pytest']  # type: ignore
        return

    cc_pio: Any = cc_pyproject['tool']['pytest']['ini_options']  # type: ignore
    pio: Container = pytest['ini_options']
    pio['addopts'] = cc_pio['addopts']
    dep_groups = pyproject.get('dependency-groups')
    if dep_groups is None:
        return
    dev: list | None = dep_groups.get('dev')
    if dev is None:
        return
    for dep in dev:
        if dep.startswith('pytest-asyncio'):
            break
    else:
        return
    pio['asyncio_mode'] = 'auto'
    pio['asyncio_default_test_loop_scope'] = 'session'
    pio['asyncio_default_fixture_loop_scope'] = 'session'


def check_flit(tool: Container):
    flit = tool.get('flit')
    if flit is None:
        return
    # more migration assistant could be implemented here
    del tool['flit']
    raise SystemExit(
        '[tool.flit] settings found. Migrate the build-backend from flit to uv.\n'
        'https://docs.astral.sh/uv/concepts/build-backend/#choosing-a-build-backend'
    )


def check_tool(pyproject: TOMLDocument) -> None:
    try:
        tool: Container = pyproject['tool']  # type: ignore
    except KeyError:
        pyproject['tool'] = cc_pyproject['tool']
        return

    check_flit(tool)
    check_pyright(tool)
    check_ruff(tool)
    check_pytest(pyproject, tool)
    if tool.get('setuptools') is not None:
        warning('Removing setuptools from pyproject; use uv instead.')
        del tool['setuptools']


def check_version(project: TOMLDocument):
    if 'version' not in project:
        raise SystemExit('add project.version')

    # uv does not support dynamic version
    dynamic = project.get('dynamic')
    if dynamic is None:
        return
    if 'version' in dynamic:
        raise SystemExit('remove version from project.dynamic')


def check_project(pyproject: TOMLDocument) -> None:
    project = pyproject.get('project')
    if project is None:
        pyproject['project'] = cc_pyproject['project']
        raise SystemExit(
            'pyproject.toml did not have a [project] section. '
            '`requires-python` field is required.'
        )
    check_version(project)
    if project.get('requires-python') is None:
        required_python = input(
            'What is the minimum required python version for this project? (e.g. 3.12)\n'
        )
        project['requires-python'] = required_python
    if project.get('urls') is None:
        if (name := project.get('name')) is not None:
            warning('adding GitHub link to project urls')
            project['urls'] = {'GitHub': f'https://github.com/5j9/{name}'}


# @cache
# def fill_cookiecutter_template(match: Match):
#     return input(f'Enter the replacement value for {match[0]}:\n')


def write_pyproject(content: bytes):
    debug('writing to pyproject.toml')
    with open('pyproject.toml', 'wb') as f:
        f.write(content)


def update_pyproject_toml() -> TOMLDocument:
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
        check_tool(pyproject)
        check_build_system(pyproject)
    finally:
        new_pyproject_content = pyproject.as_string().encode()
        if new_pyproject_content != pyproject_content:
            write_pyproject(new_pyproject_content)

    return pyproject


def check_git_status(ignore_git_status: bool):
    status = check_output(('git', 'status', '--porcelain'))
    if status:
        if ignore_git_status:
            info(f'ignoring git status:\n{status.decode()}')
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
            info(f'ignoring git branch ({branch} not being main or master.')
        else:
            raise SystemExit(
                f'git is on {branch} branch (not main or master). '
                'Use --ignore-git-status to ignore this error.'
            )


def reset_and_delete_tag(release_version):
    info('reset_and_delete_tag')
    check_call(['git', 'reset', '@^'])
    check_call(['git', 'tag', '--delete', f'v{release_version}'])


app = App(version=__version__)


@app.default
def main(
    *,
    release_type: ReleaseType | None = None,
    upload: bool = True,
    push: bool = True,
    simulate: Annotated[bool, Parameter(('--simulate', '-s'))] = False,
    path: str | None = None,
    ignore_changelog_version: bool = False,
    ignore_git_status: Annotated[
        bool, Parameter(('--ignore-git-status', '-i'))
    ] = False,
    ignore_dist: bool = False,
    timeout: int = 90,
):
    global simulation, project_entries
    simulation = simulate
    info(f'r3l3453 v{__version__}')
    if path is not None:
        chdir(path)

    project_entries = set(listdir('.'))
    check_no_old_conf(ignore_dist)
    pyproject = update_pyproject_toml()

    check_git_status(ignore_git_status)

    if 'build-system' not in pyproject:
        return

    with VersionManager(pyproject) as version_manager:
        release_version = version_manager.bump(release_type)
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
                    info('KeyboardInterrupt')
                    return
                raise e

        # prepare next dev0
        new_dev_version = version_manager.bump(ReleaseType.DEV)
        if changelog_exists:
            changelog_add_unreleased()
        commit(f'chore(__version__): bump to {new_dev_version}')

    if push is False:
        return

    if simulation is True:
        info('git push')
        return

    while True:
        try:
            run(
                ('git', 'push', '--follow-tags'),
                capture_output=True,
                check=True,
            )
        except CalledProcessError as err:
            if err.stderr.startswith(
                b'fatal: No configured push destination.'
            ):
                raise SystemExit(
                    'No configured push destination. Try:\n'
                    'git remote add origin https://github.com/your-username/your-repo-name.git'
                )
            print(err.stdout.decode())
            print(err.stderr.decode())
            warning(
                'CalledProcessError on git push. Will retry after 5s until success.'
            )
            sleep(5)
            continue
        break


@app.command
def init():
    from cookiecutter.main import cookiecutter

    cookiecutter_dir = this_dir / 'cookiecutter'
    cookiecutter(str(cookiecutter_dir))
