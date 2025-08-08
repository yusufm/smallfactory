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


def git_commit_paths(repo_path: Path, paths: list[Path], message: str, delete: bool = False) -> None:
    """
    Stage multiple paths then commit with a message and push if origin exists.

    - If delete is False (default), we run `git add <path>` for each existing path.
    - If delete is True, we run `git rm -f <path>` for each existing path to stage deletions.
      Missing paths are ignored.
    """
    try:
        for p in paths:
            if delete:
                if p.exists():
                    # git rm will remove from working tree and stage deletion
                    subprocess.run(["git", "rm", "-f", str(p)], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    # If it doesn't exist, nothing to stage; ignore
                    continue
            else:
                if p.exists():
                    subprocess.run(["git", "add", str(p)], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", message], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        remotes = subprocess.run(["git", "remote"], cwd=repo_path, capture_output=True, text=True)
        if "origin" in remotes.stdout.split():
            subprocess.run(["git", "push", "origin", "HEAD"], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[smallfactory] Warning: Failed to commit or push changes to git.")
