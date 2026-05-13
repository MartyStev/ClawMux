import os
import re

import sys

ROOT_DIR = "/Users/martystev/VS/work/homeapp/opanclaw/ws_router"
SRC_DIR = os.path.join(ROOT_DIR, "src")

MAPPING = {
    "config": "core",
    "database": "core",
    "models": "core",
    "mattermost": "services",
    "openclaw_client": "services",
    "ws_manager": "services",
    "file_manager": "services",
    "mapping": "services",
    "claw_aggregator": "utils",
    "health": "utils",
    "metrics": "utils",
}

def move_files():
    for dest in set(MAPPING.values()):
        os.makedirs(os.path.join(SRC_DIR, dest), exist_ok=True)
    
    for filename, dest in MAPPING.items():
        src_path = os.path.join(SRC_DIR, filename + ".py")
        dest_path = os.path.join(SRC_DIR, dest, filename + ".py")
        if os.path.exists(src_path):
            os.rename(src_path, dest_path)
            print(f"Moved {src_path} to {dest_path}")

def update_imports():
    patterns = []
    for module, dest in MAPPING.items():
        # Match 'from src.module' -> 'from src.dest.module'
        patterns.append((re.compile(fr'from\s+src\.{module}\b'), f'from src.{dest}.{module}'))
        # Match 'import src.module' -> 'import src.dest.module'
        patterns.append((re.compile(fr'import\s+src\.{module}\b'), f'import src.{dest}.{module}'))

    # Get all python files
    python_files = []
    for root, dirs, files in os.walk(SRC_DIR):
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    
    alembic_env = os.path.join(ROOT_DIR, "alembic", "env.py")
    if os.path.exists(alembic_env):
        python_files.append(alembic_env)

    for file_path in python_files:
        with open(file_path, "r") as f:
            content = f.read()
            
        new_content = content
        for pattern, replacement in patterns:
            new_content = pattern.sub(replacement, new_content)
            
        if content != new_content:
            with open(file_path, "w") as f:
                f.write(new_content)
            print(f"Updated imports in {file_path}")

if __name__ == '__main__':
    move_files()
    update_imports()
