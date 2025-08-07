import subprocess
from pathlib import Path


def git_commit_and_push(repo_path: Path, file_path: Path, message: str) -> None:
    """Stage file_path, commit with message, and push if origin exists."""
    try:
        subprocess.run(["git", "add", str(file_path)], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", message], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Check if origin exists
        remotes = subprocess.run(["git", "remote"], cwd=repo_path, capture_output=True, text=True)
        if "origin" in remotes.stdout.split():
            subprocess.run(["git", "push", "origin", "HEAD"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[smallfactory] Warning: Failed to commit or push changes to git.")
