import subprocess
from pathlib import Path

# Note: git_commit_and_push was removed. Use git_commit_paths (commit-only)
# and orchestrate push via higher-level transaction guard in web/CLI.
def git_commit_paths(repo_path: Path, paths: list[Path], message: str, delete: bool = False) -> None:
    """
    Stage multiple paths then commit with a message (commit-only; no push).

    - If delete is False (default), we run `git add <path>` for each existing path.
    - If delete is True, we run `git rm -f <path>` for each existing path to stage deletions.
      Missing paths are ignored.
    """
    try:
        for p in paths:
            if delete:
                if p.exists():
                    # git rm will remove from working tree and stage deletion
                    subprocess.run(["git", "rm", "-fr", str(p)], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    # If it doesn't exist, nothing to stage; ignore
                    continue
            else:
                if p.exists():
                    subprocess.run(["git", "add", str(p)], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", message], cwd=repo_path, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("[smallFactory] Warning: Failed to commit changes to git.")


def git_push(repo_path: Path, remote: str = "origin", ref: str = "HEAD") -> bool:
    """Push the current ref to the given remote if it exists.

    Returns True if a push was attempted (and succeeded), False if remote missing.
    Prints a warning on failure and returns False.
    """
    try:
        remotes = subprocess.run(["git", "remote"], cwd=repo_path, capture_output=True, text=True)
        if remotes.returncode != 0:
            return False
        if remote not in remotes.stdout.split():
            return False
        r = subprocess.run(["git", "push", remote, ref], cwd=repo_path, capture_output=True, text=True)
        if r.returncode != 0:
            print("[smallFactory] Warning: git push failed:", (r.stderr or r.stdout or "").strip())
            return False
        return True
    except Exception:
        print("[smallFactory] Warning: Unexpected error during git push.")
        return False
