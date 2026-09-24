#!/usr/bin/env python3
"""
ClawMux — Workspace Template Sync Script.

Synchronizes corporate workspace configuration files (AGENTS.md, openclaw.json,
subagents/*.md, mcp/*.json) from a master template directory to existing OpenClaw instances.

Usage:
  python scripts/sync_workspace_templates.py --all
  python scripts/sync_workspace_templates.py --instance-uuid 30f2aeff-1111-2222-3333-123456789abc
  python scripts/sync_workspace_templates.py --all --dry-run
  python scripts/sync_workspace_templates.py --all --backup
"""

import argparse
import os
import shutil
import sys
import time

# Ensure ClawMux root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.config import settings


def sync_instance_workspace(
    instance_uuid: str,
    template_dir: str,
    base_configs_path: str,
    dry_run: bool = False,
    backup: bool = True,
) -> bool:
    """
    Overwrites/synchronizes workspace template files for a single instance.
    """
    workspace_dir = os.path.join(base_configs_path, instance_uuid, "workspace")
    if not os.path.exists(workspace_dir) and not dry_run:
        os.makedirs(workspace_dir, exist_ok=True)

    print(f"🔄 Syncing instance [{instance_uuid}] -> {workspace_dir}")

    # Optional backup
    if backup and os.path.exists(workspace_dir) and not dry_run:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(base_configs_path, instance_uuid, f"backups_{timestamp}")
        try:
            shutil.copytree(workspace_dir, backup_dir)
            print(f"   📦 Backup created at: {backup_dir}")
        except Exception as e:
            print(f"   ⚠️ Backup failed: {e}", file=sys.stderr)

    synced_files = 0
    for root, _dirs, files in os.walk(template_dir):
        rel_path = os.path.relpath(root, template_dir)
        target_dir = os.path.join(workspace_dir, rel_path) if rel_path != "." else workspace_dir

        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)

        for file in files:
            src_file = os.path.join(root, file)
            dst_file = os.path.join(target_dir, file)

            rel_file_path = os.path.join(rel_path, file) if rel_path != "." else file

            if dry_run:
                print(f"   [DRY-RUN] Would update: {rel_file_path}")
            else:
                try:
                    with open(src_file, encoding="utf-8") as f:
                        content = f.read()
                    content = content.replace("{{UUID}}", instance_uuid)
                    with open(dst_file, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"   ✅ Updated: {rel_file_path}")
                except UnicodeDecodeError:
                    shutil.copy2(src_file, dst_file)
                    print(f"   ✅ Copied binary: {rel_file_path}")
                synced_files += 1

    print(f"✨ Finished instance [{instance_uuid}]: {synced_files} files updated.\n")
    return True


def discover_instance_uuids(base_configs_path: str) -> list[str]:
    """Find all instance UUID directories inside configs base path."""
    if not os.path.exists(base_configs_path):
        return []

    uuids = []
    for entry in os.listdir(base_configs_path):
        full_path = os.path.join(base_configs_path, entry)
        if os.path.isdir(full_path) and not entry.startswith(".") and not entry.startswith("backups"):
            uuids.append(entry)
    return uuids


def main():
    parser = argparse.ArgumentParser(description="Synchronize OpenClaw workspace templates across instances.")
    parser.add_argument("--all", action="store_true", help="Sync all existing instances in workspace base directory.")
    parser.add_argument("--instance-uuid", type=str, help="Sync a specific instance UUID.")
    parser.add_argument(
        "--template-dir", type=str, default=settings.workspace_template_path, help="Path to master template folder."
    )
    parser.add_argument(
        "--base-path", type=str, default=settings.workspace_base_path, help="Path to instances workspace root."
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate updates without writing files.")
    parser.add_argument("--no-backup", action="store_true", help="Disable automatic backups before overwriting.")
    parser.add_argument(
        "--restart-containers",
        action="store_true",
        help="Attempt to restart Docker containers corresponding to updated instances (e.g. 'openclaw-<UUID>').",
    )
    parser.add_argument(
        "--container-prefix",
        type=str,
        default="openclaw-",
        help="Prefix used to match container names when restarting (default: 'openclaw-').",
    )

    args = parser.parse_args()

    if not args.all and not args.instance_uuid:
        parser.print_help()
        sys.exit(1)

    template_dir = os.path.abspath(args.template_dir)
    base_path = os.path.abspath(args.base_path)

    if not os.path.exists(template_dir):
        print(f"❌ Error: Master template directory not found at: {template_dir}", file=sys.stderr)
        sys.exit(1)

    print("🚀 ClawMux Workspace Template Synchronizer")
    print(f"📂 Template Directory : {template_dir}")
    print(f"🗂️  Instances Base Path: {base_path}")
    print(f"⚙️  Dry Run Mode      : {args.dry_run}")
    print(f"💾 Automatic Backups  : {not args.no_backup}")
    print(f"🔄 Restart Containers : {args.restart_containers}\n")

    target_uuids = []
    if args.instance_uuid:
        target_uuids.append(args.instance_uuid)
    elif args.all:
        target_uuids = discover_instance_uuids(base_path)
        if not target_uuids:
            print("⚠️ No instance directories found in base path.")

    count = 0
    for uuid_str in target_uuids:
        if sync_instance_workspace(
            instance_uuid=uuid_str,
            template_dir=template_dir,
            base_configs_path=base_path,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        ):
            count += 1
            if args.restart_containers and not args.dry_run:
                container_name = f"{args.container_prefix}{uuid_str}"
                print(f"   🔄 Restarting container: {container_name}...")
                res = os.system(f"docker restart {container_name} >/dev/null 2>&1")
                if res == 0:
                    print(f"   ✅ Container restarted: {container_name}")
                else:
                    print(f"   ℹ️ Container {container_name} not found or docker inaccessible.")

    print(f"🎉 Complete! Synchronized {count} instance workspaces.")


if __name__ == "__main__":
    main()
