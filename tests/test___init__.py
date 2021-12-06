from r3l3453 import read_version_file
from unittest.mock import mock_open, patch

json_config = """\
{
    "version_path": "r3l3453/__init__.py:__version__"
}
"""


@patch("builtins.open", mock_open(read_data=json_config))
@patch("io.open", mock_open(
    read_data="__version__ = '0.1.2'\n"))
def test_get_file_versions():
    with read_version_file() as fv:
        assert str(fv.version) == '0.1.2'
