import os
import shutil
import subprocess
from pathlib import Path


def create_or_get_workspace(folder_name: str = "workspace") -> str:
    """
    AI-AGENT USAGE:
    --------------
    Ensures a stable directory for cloning GitHub repos.
    The workspace location is determined automatically by using
    the parent directory of this script file.

    Why required?
    -------------
    - Agents need a deterministic and persistent location.
    - Prevents cloning into temp folders that are lost.
    - Avoids accidental mixing with other system folders.

    Behavior:
    ---------
    - If workspace exists → return it.
    - If not → create it and return path.

    Returns:
        Absolute path of the workspace directory.
    """

    possible_targets = [
        "multi_agent_refactorer",
        "src",
        "app",
        "core",     # add more if needed
    ]

    current_path = os.path.abspath(__file__)
    parts = current_path.split(os.sep)

    target_index = None
    # Auto-detect which target folder exists in the path
    for folder in possible_targets:
        if folder in parts:
            target_index = parts.index(folder)
            break

    if target_index is None:
        raise RuntimeError(
            f"No target folder from {possible_targets} found in path: {current_path}"
        )

    # Parent folder of the detected target
    parent_dir = os.path.dirname(os.sep.join(parts[:target_index]))

    # Workspace in parallel to target
    workspace_path = os.path.join(parent_dir, folder_name)

    # Create only if not exists
    if not os.path.exists(workspace_path):
        os.makedirs(workspace_path)

    return workspace_path


def commit_changes(repo_path: str, files: str, commit_message: str, branch_name:str) -> dict:
    """
    Commit only the specific files modified by the Fixer Agent.

    Inputs:
        repo_path (str): absolute path to the cloned repo
        files (str): comma separated file paths relative to repo root
        commit_message (str): commit message

    Output:
        {
            "git-commit-summary": "<status>"
        }
    """
    try:
        files = files.split(",")
        if not os.path.isdir(repo_path):
            return {
                "git-commit-summary": f"Invalid repo path: {repo_path}"
            }

        if not files or len(files) == 0:
            return {
                "git-commit-summary": "No files provided to commit."
            }

        # Stage only specific files
        for f in files:
            subprocess.run(
                ["git", "add", f],
                cwd=repo_path,
                check=True
            )

        # Commit
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_path,
            check=True,
            timeout=60
        )

        subprocess.run(
            ["git", "push", "--set-upstream", "origin", branch_name],
            cwd=repo_path,
            check=True,
            timeout=60
        )


        return {
            "git-commit-summary": f"Committed {len(files)} files successfully."
        }

    except subprocess.CalledProcessError as e:
        return {
            "git-commit-summary": f"Commit failed: {str(e)}"
        }

def create_feature_branch(repo_path: str, branch_name: str) -> dict:
    """
    Create & switch to a new feature branch inside the cloned repository.

    Inputs:
        repo_path (str): absolute path of the cloned repo
        branch_name (str): new branch name

    Output:
        {
            "git-branch-summary": "<status>"
        }
    """
    try:
        if not os.path.isdir(repo_path):
            return {
                "git-branch-summary": f"Invalid repo path: {repo_path}"
            }

        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_path,
            check=True
        )

        return {
            "git-branch-summary": f"Created and switched to branch: {branch_name}"
        }

    except subprocess.CalledProcessError as e:
        return {
            "git-branch-summary": f"Branch creation failed: {str(e)}"
        }


def git_clone_repo(repo_url: str) -> dict:
    """
    Clone a GitHub repository into the workspace folder and return clone info.

    AI-Agent Friendly Contract:
    ---------------------------
    This function NEVER raises exceptions; it ALWAYS returns:

        {
            "success": True/False,
            "path": "/absolute/path/to/cloned/project" or None,
            "error": "error message if failed"
        }

    Expected Input:
    ---------------
    - `repo_url`: GitHub repository HTTPS or SSH URL.
      Example: https://github.com/user/repo.git

    Behavior:
    ---------
    1. Validate repo URL (must contain github.com)
    2. Determine workspace path using `create_or_get_workspace()`
    3. Create project directory inside workspace → `<workspace>/<repo>`
    4. If project folder exists → delete and replace (fresh clone)
    5. Clone with `git clone`
    6. Return structured result for agent use

    Returns:
        dict → { success, path, error }
    """

    # Validate URL
    if not repo_url or "github.com" not in repo_url:
        return {
            "success": False,
            "path": None,
            "error": "Invalid GitHub repo URL. Must contain 'github.com'."
        }

    # Resolve workspace directory
    try:
        workspace = create_or_get_workspace()
    except Exception as e:
        return {
            "success": False,
            "path": None,
            "error": f"Unable to prepare workspace folder: {e}"
        }

    # Extract repo name
    try:
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    except Exception:
        repo_name = "cloned_repo"

    target_path = Path(workspace) / repo_name

    # Remove if already exists
    if target_path.exists():
        try:
            shutil.rmtree(target_path)
        except Exception as e:
            return {
                "success": False,
                "path": None,
                "error": f"Failed to remove existing directory '{target_path}': {e}"
            }

    # Perform git clone
    try:
        result = subprocess.run(
            ["git", "clone", repo_url, str(target_path)],
            capture_output=True,
            text=True,
            timeout=60
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "path": None,
            "error": "Git clone timed out. Check internet/GitHub access."
        }
    except Exception as e:
        return {
            "success": False,
            "path": None,
            "error": f"Git clone execution failure: {e}"
        }

    # Git command returned an error
    if result.returncode != 0:
        return {
            "success": False,
            "path": None,
            "error": result.stderr.strip() or "Unknown Git error."
        }

    # Success!
    return {
        "success": True,
        "path": str(target_path),
        "error": None
    }


git_tools =[
    create_or_get_workspace,
    git_clone_repo,
    create_feature_branch,
    commit_changes
]