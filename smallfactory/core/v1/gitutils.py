import subprocess
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, message: str, *, cmd: list[str] | None = None, returncode: int | None = None, stdout: str | None = None, stderr: str | None = None):
        super().__init__(message)
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GitCommitError(GitError):
    pass


class GitPushError(GitError):
    pass

# Note: git_commit_and_push was removed. Use git_commit_paths (commit-only)
# and orchestrate push via higher-level transaction guard in web/CLI.
def git_commit_paths(repo_path: Path, paths: list[Path], message: str, delete: bool = False) -> None:
    """
    Stage multiple paths then commit with a message (commit-only; no push).

    - If delete is False (default), we run `git add <path>` for each existing path.
    - If delete is True, we run `git rm -f <path>` for each existing path to stage deletions.
      Missing paths are ignored.
    """
    if not paths:
        return
    try:
        for p in paths:
            if delete:
                # Stage removals and remove from working tree; ignore if path is untracked.
                r = subprocess.run(
                    ["git", "rm", "-fr", "--ignore-unmatch", "--", str(p)],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                )
                if r.returncode != 0:
                    raise GitCommitError(
                        "git rm failed",
                        cmd=r.args if isinstance(r.args, list) else None,
                        returncode=r.returncode,
                        stdout=r.stdout,
                        stderr=r.stderr,
                    )
            else:
                # Use -A so deletions under a directory are staged as well.
                r = subprocess.run(
                    ["git", "add", "-A", "--", str(p)],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                )
                if r.returncode != 0:
                    raise GitCommitError(
                        "git add failed",
                        cmd=r.args if isinstance(r.args, list) else None,
                        returncode=r.returncode,
                        stdout=r.stdout,
                        stderr=r.stderr,
                    )

        cm = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if cm.returncode != 0:
            out = (cm.stdout or "") + "\n" + (cm.stderr or "")
            low = out.lower()
            nothing_to_commit = (
                "nothing to commit" in low
                or "no changes added to commit" in low
                or "nothing added to commit" in low
            )
            if nothing_to_commit:
                return
            raise GitCommitError(
                "git commit failed",
                cmd=cm.args if isinstance(cm.args, list) else None,
                returncode=cm.returncode,
                stdout=cm.stdout,
                stderr=cm.stderr,
            )
    except GitError:
        raise
    except Exception as e:
        raise GitCommitError(str(e))


def git_push(repo_path: Path, remote: str = "origin", ref: str = "HEAD") -> bool:
    """Push the current ref to the given remote if it exists.

    Returns True if a push was attempted (and succeeded), False if remote missing.
    Prints a warning on failure and returns False.
    """
    remotes = subprocess.run(["git", "remote"], cwd=repo_path, capture_output=True, text=True)
    if remotes.returncode != 0:
        raise GitPushError(
            "git remote failed",
            cmd=remotes.args if isinstance(remotes.args, list) else None,
            returncode=remotes.returncode,
            stdout=remotes.stdout,
            stderr=remotes.stderr,
        )
    if remote not in (remotes.stdout or "").split():
        return False

    r = subprocess.run(["git", "push", remote, ref], cwd=repo_path, capture_output=True, text=True)
    if r.returncode != 0:
        raise GitPushError(
            "git push failed",
            cmd=r.args if isinstance(r.args, list) else None,
            returncode=r.returncode,
            stdout=r.stdout,
            stderr=r.stderr,
        )
    return True
