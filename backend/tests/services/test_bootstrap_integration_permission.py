"""Bootstrap: integration.manage is system_admin only."""

from __future__ import annotations


def test_permission_integration_manage_system_admin_only() -> None:
    from app.services.bootstrap import PERMISSION_DEFINITIONS, ROLE_PERMISSION_MATRIX

    assert "integration.manage" in PERMISSION_DEFINITIONS
    assert "integration.manage" in ROLE_PERMISSION_MATRIX["system_admin"]
    assert "integration.manage" not in ROLE_PERMISSION_MATRIX["recruiter_admin"]
    assert "integration.manage" not in ROLE_PERMISSION_MATRIX["interviewer"]
    # Explicit: recruitment / audit / ai_task manage do not imply integration
    assert "recruitment.manage" in ROLE_PERMISSION_MATRIX["recruiter_admin"]
    assert "integration.manage" not in ROLE_PERMISSION_MATRIX["recruiter_admin"]
