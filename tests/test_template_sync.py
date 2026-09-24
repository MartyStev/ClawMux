from unittest.mock import MagicMock, patch

import pytest

from src.core.config import settings
from src.services.provisioner import InstanceProvisioner


@pytest.mark.anyio
async def test_seed_workspace_template(tmp_path):
    # Setup temp template and configs dir
    template_dir = tmp_path / "templates" / "default_workspace"
    template_dir.mkdir(parents=True)
    (template_dir / "AGENTS.md").write_text("# Hello {{USER_ID}} in {{PROVIDER}}", encoding="utf-8")
    sub_dir = template_dir / "subagents"
    sub_dir.mkdir()
    (sub_dir / "sec.md").write_text("Security subagent for {{UUID}}", encoding="utf-8")

    base_configs_dir = tmp_path / "configs"

    mapping = MagicMock()
    provisioner = InstanceProvisioner(mapping)

    with (
        patch.object(settings, "workspace_template_path", str(template_dir)),
        patch.object(settings, "workspace_base_path", str(base_configs_dir)),
    ):
        await provisioner._seed_workspace_template("inst-uuid-777", "telegram", "user-999")

        dest_workspace = base_configs_dir / "inst-uuid-777" / "workspace"
        assert dest_workspace.exists()

        agents_content = (dest_workspace / "AGENTS.md").read_text(encoding="utf-8")
        assert agents_content == "# Hello user-999 in telegram"

        sec_content = (dest_workspace / "subagents" / "sec.md").read_text(encoding="utf-8")
        assert sec_content == "Security subagent for inst-uuid-777"
