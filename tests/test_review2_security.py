"""
Tests for review-round-2 item 1: path-traversal hardening in
container_path_to_host and validation of provisioner-returned
instance_uuid/instance_url before they are trusted.
"""

import os
import uuid

import pytest

from src.core.config import settings
from src.services.file_manager import container_path_to_host
from src.services.provisioner import ProvisioningError, _validate_provisioned_target

GOOD_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point WORKSPACE_BASE_PATH at a temp dir with one tenant uuid."""
    base = tmp_path / "configs"
    (base / GOOD_UUID / "workspace" / "output").mkdir(parents=True)
    (base / GOOD_UUID / "workspace" / "output" / "report.txt").write_text("ok")
    other = base / "66666666-7777-8888-9999-aaaaaaaaaaaa"
    other.mkdir()
    (other / "secret.txt").write_text("other tenant")
    monkeypatch.setattr(settings, "workspace_base_path", str(base))
    return base


# ── container_path_to_host ─────────────────────────────────────────


def test_normal_container_path_resolves(workspace):
    host = container_path_to_host("/home/node/.openclaw/workspace/output/report.txt", GOOD_UUID)
    assert host == f"{workspace}/{GOOD_UUID}/workspace/output/report.txt"
    assert os.path.isfile(host)


def test_outside_openclaw_root_rejected(workspace):
    assert container_path_to_host("/etc/passwd", GOOD_UUID) == ""


def test_relative_traversal_rejected(workspace):
    attack = "/home/node/.openclaw/../../../../etc/passwd"
    assert container_path_to_host(attack, GOOD_UUID) == ""


def test_cross_tenant_traversal_rejected(workspace):
    other = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    attack = f"/home/node/.openclaw/../{other}/secret.txt"
    assert container_path_to_host(attack, GOOD_UUID) == ""
    # Even a "cleaned up" path that lands in another tenant dir must fail:
    sneaky = f"/home/node/.openclaw/workspace/../../{other}/secret.txt"
    assert container_path_to_host(sneaky, GOOD_UUID) == ""


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_escape_rejected(workspace):
    uuid_dir = workspace / GOOD_UUID
    link = uuid_dir / "workspace" / "out"
    os.symlink(str(workspace.parent), link)  # tenant-controlled symlink to tmp root
    # Lexically inside the tenant dir, but realpath() resolves through the
    # symlink to a location outside WORKSPACE_BASE_PATH/<uuid> — must reject:
    direct = container_path_to_host("/home/node/.openclaw/workspace/out/passwd", GOOD_UUID)
    assert direct == ""
    # A path that stays inside after normalization is still allowed:
    ok = container_path_to_host("/home/node/.openclaw/workspace/ghost/../../workspace/output/report.txt", GOOD_UUID)
    assert ok.endswith("workspace/output/report.txt")


# ── _validate_provisioned_target ───────────────────────────────────


def test_valid_ws_url_accepted():
    _validate_provisioned_target(GOOD_UUID, f"ws://openclaw-gw-{GOOD_UUID}:18789/ws")
    _validate_provisioned_target(GOOD_UUID, "wss://claw.example.com/ws")


def test_bad_scheme_rejected():
    with pytest.raises(ProvisioningError, match="scheme"):
        _validate_provisioned_target(GOOD_UUID, "http://evil.example.com/ws")


def test_non_ws_protocols_rejected():
    for url in ("file:///etc/passwd", "gopher://x:1111/_", "ws:/x"):
        with pytest.raises(ProvisioningError):
            _validate_provisioned_target(GOOD_UUID, url)


def test_bad_uuid_rejected():
    with pytest.raises(ProvisioningError, match="instance_uuid"):
        _validate_provisioned_target("../../etc/x", "ws://ok:18789/ws")
    with pytest.raises(ProvisioningError, match="instance_uuid"):
        _validate_provisioned_target("not-a-uuid", "ws://ok:18789/ws")


def test_host_allowlist_enforced(monkeypatch):
    monkeypatch.setattr(settings, "provisioning_allowed_instance_hosts", "claw.internal, OpenClaw-GW ")
    _validate_provisioned_target(GOOD_UUID, "ws://claw.internal:18789/ws")  # case-insensitive
    _validate_provisioned_target(GOOD_UUID, "wss://openclaw-gw/ws")
    with pytest.raises(ProvisioningError, match="allowlist"):
        _validate_provisioned_target(GOOD_UUID, "ws://169.254.169.254/ws")


def test_empty_allowlist_keeps_scheme_check_only(monkeypatch):
    monkeypatch.setattr(settings, "provisioning_allowed_instance_hosts", "")
    _validate_provisioned_target(GOOD_UUID, "ws://anything.internal:1234/ws")
    with pytest.raises(ProvisioningError, match="scheme"):
        _validate_provisioned_target(GOOD_UUID, "ftp://anything.internal:1234/ws")


def test_generated_uuids_stay_valid():
    # Mock driver and webhook fallback generate uuid4 — make sure validation
    # does not reject the canonical form they produce.
    _validate_provisioned_target(str(uuid.uuid4()), "ws://localhost:18789/ws")
