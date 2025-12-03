import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from acm_switchover import parse_args


@pytest.mark.unit
def test_cli_flag_manage_auto_import():
    with patch(
        "sys.argv",
        [
            "script.py",
            "--primary-context",
            "p1",
            "--secondary-context",
            "p2",
            "--method",
            "passive",
            "--old-hub-action",
            "secondary",
            "--manage-auto-import-strategy",
        ],
    ):
        args = parse_args()
        assert args.manage_auto_import_strategy is True
