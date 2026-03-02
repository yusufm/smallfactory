from __future__ import annotations

from smallfactory.core.v1 import repo_upgrade as ru
from smallfactory.core.v1 import versioning as vv


def test_parse_semver_like_handles_prerelease_and_build_metadata():
    assert vv._parse_semver_like("1.1-rc1") == (1, 1, 0)
    assert ru._parse_semver_like("1.1-rc1") == (1, 1, 0)
    assert vv._parse_semver_like("2.4.9+build.12") == (2, 4, 9)
    assert ru._parse_semver_like("2.4.9+build.12") == (2, 4, 9)


def test_parse_semver_like_extracts_numeric_components():
    assert vv._parse_semver_like("v1.2.3") == (1, 2, 3)
    assert ru._parse_semver_like("v1.2.3") == (1, 2, 3)
