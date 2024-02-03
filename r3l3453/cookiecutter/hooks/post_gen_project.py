# note: working directory for this hook is root of the generated project.
from os import rename
from subprocess import run

# the original causes error when running ruff
rename('pyproject_template.toml', 'pyproject.toml')
run(('git', 'init')).check_returncode()
run(('git', 'add', '--all')).check_returncode()
run(
    ('git', 'commit', '-m', 'files added by `r3l3453 init`')
).check_returncode()
run(
    (
        'git',
        'remote',
        'add',
        'origin',
        'https://github.com/5j9/{{cookiecutter.project_name}}.git',
    )
).check_returncode()
