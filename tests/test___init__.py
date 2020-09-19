from r3l3453 import get_file_versions
from unittest.mock import mock_open, patch

json_config = """\
{
    "version_paths":  [
        "r3l3453/__init__.py:__version__",
        "setup.py:version"
    ]
}
"""


@patch("builtins.open", mock_open(read_data=json_config))
@patch("io.open", mock_open(
    read_data="__version__ = '0.1'\nversion='0.1'"))  # used in path
def test_get_file_versions():
    with get_file_versions() as fv:
        assert len(fv) == 2
