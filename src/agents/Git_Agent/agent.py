from google.adk.agents.llm_agent import Agent

import os
import subprocess
import time
from pathlib import Path
import tempfile

from google import genai

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

repo_input = None
workdir=r"C:\Users\Vaishnavi\Desktop\VAISHNAVI\Projects\AI Agents\Code Sculptor\CodeSculptor\workspace"
repo_path = None
branch_name = None

def init_git_agent(repo_link_or_path: str, workdir_path: str):
    """
    Initialize the Git Agent with the repository source and workspace directory.

    Purpose:
        Sets up global state required by all Git operations. Must be called
        before clone, checkout, commit, or push tools.

    Args:
        repo_link_or_path (str):
            Remote Git URL (HTTPS/SSH) or a local repository path.
        workdir_path (str | Path):
            Directory where the agent will create/use a workspace. A "repo"
            subfolder inside this directory becomes the active repo path.

    Behavior:
        - Stores repo_input, workdir, and repo_path for downstream tools.
        - Resets branch_name to None. Actual Git actions happen later.

    Returns:
        None. This function only prepares internal state.

    Notes for AI agent/tool use:
        - Must be executed first in any Git workflow.
        - Does not validate URLs or paths; clone_or_open_repo() will handle that.
        - If called with an invalid path, later tools may raise errors.

    Summary:
        Prepares the Git Agent environment so other tools can safely perform
        cloning and repository operations.
    """

    global repo_input, workdir, repo_path, branch_name
    # If user passes /tmp/... or \tmp/... → replace with Windows temp
    if workdir_path.startswith("/") or workdir_path.startswith("\\"):
        workdir_path = Path(tempfile.gettempdir()) / "gpt-agent-workdir"

    workdir = Path(workdir_path)
    workdir.mkdir(parents=True, exist_ok=True)
    repo_input = repo_link_or_path
    repo_path = workdir / "repo"
    branch_name = None

def run_cmd(cmd, cwd=None):
    """Run shell commands safely"""
    result = subprocess.run(
        cmd, 
        cwd=cwd, 
        # shell=True, 
        stdout=subprocess.PIPE, # !!introduces injection risk if cmd contains untrusted input.!!
        stderr=subprocess.PIPE, 
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout

def clone_or_open_repo() -> str:
    """
    Prepare a working repository by either cloning it from a remote URL or opening an existing local repository path.

    Returns:
      A string representing the absolute path to the repository directory that will be used for all subsequent Git operations performed by the agent.

    Notes for AI agent/tool use:
      - Repository input:
          * If `repo_input` is an HTTP/HTTPS URL, the function treats it as a remote Git repository and attempts to clone it into `workdir/repo`(only if the target path does not already exist).
          * If `repo_input` is a filesystem path, it must already exist. The function resolves it into an absolute directory path and uses it directly without cloning.

      - Cloning behaviour:
          * Cloning is performed using the system `git` CLI via `run_cmd()`.

      - Directory preparation:
          * `workdir` is created if missing, ensuring a clean working area for remote clone operations.

      - Error handling:
          * If `repo_input` is treated as a local path but does not exist, the function raises `FileNotFoundError`.
          * Git cloning failures (e.g., network issues, invalid repository, missing git binary) will propagate as exceptions from `run_cmd()`.

      - Returned path:
          * `repo_path` is set internally to the resolved directory of the repository (either cloned or local).
          * The return value is always the string form of this path.
    """
    global repo_input, workdir, repo_path, branch_name
    workdir.mkdir(parents=True, exist_ok=True)
    repo_input_str = str(repo_input).strip()

    # Detect remote URLs: HTTP(S) or SSH-style (git@...)
    is_http = repo_input_str.startswith(("http://", "https://"))
    is_ssh = repo_input_str.startswith("ssh://") or ("@" in repo_input_str and ":" in repo_input_str and not Path(repo_input_str).exists())

    if is_http or is_ssh:
        # Remote repository cloning
        if not repo_path.exists():
            print("Cloning repository...")

            # For SSH or HTTP, clone into workdir/repo
            run_cmd(
                f"git clone {repo_input} repo",
                cwd=workdir
            )
    else:
        # Treat as local filesystem path
        local = Path(repo_input).resolve()

        if not local.exists():
            raise FileNotFoundError("Local repo path does not exist")

        repo_path = local

    print(f"Using repository at: {repo_path}")
    return str(repo_path)

def create_branch() -> str:
    """
    Create a new Git branch for the agent's refactoring operation.

    Returns:
      The name of the newly created Git branch as a string. The branch name follows the pattern: `agent-refactor/<timestamp>` where `<timestamp>` is a unique value in the format `YYYYMMDD-HHMMSS`.

    Notes for AI agent/tool use:
      - Branch naming:
          * A unique timestamp is appended to ensure branch name collisions do not occur across multiple agent runs.
          * The branch is always created under the prefix `agent-refactor/`.

      - Base branch selection:
          * The function attempts to switch the working directory to `main`.
          * If `main` does not exist, it falls back to `master`.
          * If neither branch exists, branch switching is skipped and a warning is printed, but execution continues.

      - Pulling latest changes:
          * The function checks if a remote named `origin` exists before attempting `git pull`.
          * If `origin` is available, it pulls the latest changes to ensure the new branch is created from the most up-to-date commit.
          * Pull failures (e.g., network issues, authentication, diverged history) emit warnings but do not stop execution.

      - Branch creation:
          * The function executes `git checkout -b <branch_name>` from the repository root.
          * If the branch already exists or Git errors occur, exceptions from `run_cmd()` will propagate upward.

      - Error handling:
          * `run_cmd()` may raise a `RuntimeError` for Git failures such as invalid branch names, repository corruption, or missing Git CLI.
          * Agents calling this tool should handle these exceptions if a non-crashing flow is desired.

      - Repository context:
          * All Git commands are executed inside `repo_path`, which must be a valid Git repository directory prepared by `clone_or_open_repo()`.
    """
    global repo_input, workdir, repo_path, branch_name
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    branch_name = f"agent-refactor/{timestamp}"

    # Switch to main/master. chkout - switching between branches or commits and restoring files to a previous state
    try:
        run_cmd("git checkout main", cwd=repo_path)
    except RuntimeError:
        try:
            run_cmd("git checkout master", cwd=repo_path)
        except RuntimeError:
            print("Warning: Could not checkout main or master")

    # Only pull if remote 'origin' exists
    remotes = run_cmd("git remote", cwd=repo_path).strip()
    if "origin" in remotes:
        print("Pulling latest changes from remote...")
        try:
            run_cmd("git pull", cwd=repo_path)
        except RuntimeError:
            print("Warning: Could not pull from remote")
    else:
        print("⚠️ No remote configured → skipping git pull")

    # Create branch
    run_cmd(f"git checkout -b {branch_name}", cwd=repo_path)
    print(f"Created branch: {branch_name}")
    return branch_name

def apply_patch(file_path: str, content: str) -> str:
    """
    Write updated or newly generated code/content into a file inside the repository, creating any missing parent directories as needed.

    Returns:
      The repository-relative file path (string) of the file that was modified or created. This value can be used by calling agents to track what files were patched or to stage later Git operations (e.g., `git add`).

    Notes for AI agent/tool use:
      - Purpose:
          * This function allows the agent to "apply a patch" by overwriting the target file with the provided content.
          * It is designed for automated code modification workflows where an agent analyzes code, generates a fix, and writes the updated file back into the repository.

      - File path handling:
          * `file_path` must be relative to the repository root (`repo_path`).
          * Parent directories are automatically created using `mkdir(parents=True)` if they do not exist, enabling patching of deeply nested paths.
          * The function writes the final content using UTF-8 encoding.

      - Content handling:
          * The entire file is replaced with the given `content`. It does not perform diff-based patching or partial edits — the agent must supply the full rewritten file content.
          * Line endings and formatting are preserved exactly as provided.

      - Repository context:
          * The file is written inside `repo_path`, which must already be set by `clone_or_open_repo()` and must point to a valid local Git working directory.

      - Side effects:
          * This function does not stage or commit changes — it only writes them. Agents must run follow-up Git commands (e.g., `git add`, `git commit`) if they want persistent repository changes.
          * A console log is printed for traceability: `[PATCH APPLIED] <file>`.

      - Error handling:
          * If the repository path is invalid, if the file cannot be written, or if the filesystem is read-only, Python I/O exceptions will be raised (e.g., `FileNotFoundError`, `PermissionError`).
          * Agents should catch these exceptions if they require a controlled failure mode or retry logic.
    """
    global repo_input, workdir, repo_path, branch_name
    full_path = repo_path / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)

    with open(full_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[PATCH APPLIED] {file_path}")
    return file_path

def run_tests() -> bool:
    """
    Detect and execute test suites in the repository, returning whether the tests completed successfully.

    Returns:
      A boolean value:
        - True  → Tests were run and passed, or no tests were configured.
        - False → Tests were detected but failed during execution.

    Notes for AI agent/tool use:
      - Test detection:
          * The function automatically checks for Python test configurations by inspecting:
                - a `pytest.ini` file, or
                - a `tests/` directory at the repository root.
          * If neither is present, the function assumes the repository does not have a test suite and returns `True` (i.e., safe to continue).

      - Test execution:
          * When tests are detected, the function runs `pytest` through the underlying `run_cmd()` method.
          * Standard output and errors from pytest are surfaced in the agent's console/logs.
          * If pytest exits with a non-zero status, `run_cmd()` raises a `RuntimeError`, which the function treats as a test failure.

      - Return semantics:
          * This function is intentionally conservative: failure to run or failing tests returns `False`, allowing agents to decide whether to halt or try alternative fixes.
          * Successful test execution returns `True`.

      - Side effects:
          * The function does not create, modify, or delete any files. It only executes pytest.
          * No virtual environment activation is performed—agents must ensure dependencies are installed and the repo is in a runnable state.

      - Error handling:
          * `RuntimeError` from `run_cmd()` is caught and translated into `False` for a clean signal to the agent.
          * If pytest is not installed or cannot be executed, this also results in a `False` return, allowing agents to gracefully fall back or skip test enforcement.

      - Repository context:
          * Tests are executed with `cwd=repo_path`, which must be a valid local repository directory prepared earlier (e.g., via `clone_or_open_repo()`).
    """
    global repo_input, workdir, repo_path, branch_name
    # Detecting Python tests
    if (repo_path / "pytest.ini").exists() or (repo_path / "tests").exists():
        print("Running pytest...")
        try:
            run_cmd("pytest", cwd=repo_path)
            return True
        except RuntimeError:
            return False

    print("No tests configured.")
    return True

def commit(message: str) -> str:
    """
    Stage all modified files in the repository and create a Git commit using the provided commit message, returning the resulting commit hash.

    Returns:
      - The commit hash (string) of the newly created commit, if changes were successfully staged and committed.
      - None if the repository contained no changes to commit.

    Notes for AI agent/tool use:
      - Purpose:
          * This function is used to persist the agent’s applied patches or code modifications as a Git commit.
          * It stages **all** tracked and untracked changes via `git add .` before attempting the commit.

      - Change detection:
          * After staging files, the function uses `git status --porcelain` to determine whether any changes remain to be committed.
          * If `status` is empty:
                - No commit is created.
                - The function returns `None`.
                - Agents should interpret this as: “nothing changed since the last commit or patch application.”

      - Commit creation:
          * If changes are detected, the function executes:
                git commit -m "<message>"
          * The function then retrieves the commit hash using:
                git rev-parse HEAD
          * This hash is returned so that agents can reference the commit, attach metadata, or trigger downstream workflows.

      - Repository context:
          * All commands run inside `repo_path`, which must be a valid Git repository previously prepared via `clone_or_open_repo()`.

      - Side effects:
          * This function permanently records modifications inside the Git history.
          * It does **not** push the commit to a remote—agents must call the push tool separately.

      - Error handling:
          * Git failures (e.g., invalid repository, missing permissions, merge conflicts, or Git not installed) propagate via `run_cmd()` as `RuntimeError`.
          * Agents relying on robust flows should catch these exceptions.
          * Malformed commit messages or restricted characters will cause Git to error out and should be validated by the agent beforehand.

      - Safety considerations for agents:
          * Because `git add .` stages *all* changes (including unintended ones), agents may want to call `git status --porcelain` beforehand if they need to inspect or reason about the exact modifications.
    """
    global repo_input, workdir, repo_path, branch_name
    run_cmd("git add .", cwd=repo_path)
    
    # Check if there are changes to commit
    status = run_cmd("git status --porcelain", cwd=repo_path).strip()
    if not status:
        print("No changes to commit")
        return None
        
    run_cmd(["git", "commit", "-m", message], cwd=repo_path)
    commit_hash = run_cmd("git rev-parse HEAD", cwd=repo_path).strip()
    print(f"Changes committed → {commit_hash}")
    return commit_hash

def commit_and_push(message: str) -> str | None:
    """
    Create a Git commit with the provided commit message and push it to the remote branch.

    Returns:
        str | None:
            The commit hash of the newly created commit, or None if the commit failed (e.g., no staged changes were present).

    Role & Detailed Description for AI Agent/Tool Use:
        - This function performs two operations:
            1. **Commit**: Calls `commit(message)` to stage and commit all current changes in the repository. The returned commit hash uniquely identifies the commit.
            2. **Push**: If a commit was successfully created *and* a branch name is configured (`branch_name`), the function runs:
                   `git push --set-upstream origin <branch_name>`
               ensuring that:
                 - The local branch is linked to the remote tracking branch.
                 - Future pushes can be done without specifying `--set-upstream`.

        - **When to use this tool:**
            Agents should use this tool when they need to:
              - Finalize local code modifications by creating a commit.
              - Propagate changes to the remote Git repository.
              - Ensure new branches are pushed with a proper upstream link.

        - **Behavior & Expectations:**
            - If no changes are staged, `commit(message)` may return None; in this case, no push is attempted.
            - Successful pushes print a confirmation message containing the commit hash.
            - The command is executed in `repo_path`, which must point to a valid Git repo.
            - This function assumes that authentication for pushing (SSH keys or HTTPS credentials) is already configured in the environment.

        - **Failures & Error Flow:**
            - Any failure inside `run_cmd` (e.g., Git not installed, network issues, permission issues, or rejected pushes) may raise an exception.
            - Agents relying on non-exception flows should wrap calls in try/except.
    """
    global repo_input, workdir, repo_path, branch_name
    commit_hash = commit(message)
    if commit_hash and branch_name:
        run_cmd(
            f"git push --set-upstream origin {branch_name}", 
            cwd=repo_path
        )
        print(f"Changes pushed → commit {commit_hash}")
    return commit_hash
instruction = f"""
You are an Autonomous Git Operations AI Agent.

--- Responsibility ---
You perform all Git actions needed to prepare a repository for other agents.
Your workflow is automatic, deterministic, and safe.
You NEVER ask questions unless the repository URL is missing.

--- Required Input ---
1. A GitHub repository URL or local path
2. (Optional) A diff/patch generated by other agents

If the URL/path is missing → ask the user for it.
If only the URL is given → proceed with normal workflow.

--- Execution Flow ---

STEP 0 → Initialize
    Always call `init_git_agent()` first.

STEP 1 → Clone Repository
    - Call `clone_or_open_repo()`
    - Always clone into a clean workspace directory
    - Never reuse or overwrite old folders

STEP 2 → Create Feature Branch
    - Call `create_branch()`
    - Branch name format: agent/<timestamp>-workspace
    - Never modify main/master directly

STEP 3 → Apply Patches (if provided)
    - If a diff or file update is received:
        Call `apply_patch(file_path, content)`
    - If patch application fails:
        Return JSON with an error and stop

STEP 4 → Commit Changes
    - Call `commit(message)`
    - Commit message format:
        "chore(agent): automated update"

STEP 5 → Return Metadata
    Provide JSON with workspace path, branch, changed files, and commit hash.

--- Rules ---

1. Do NOT push to remote unless commit_and_push is explicitly used.
2. Never alter main/master or protected branches.
3. No destructive Git operations (no rebase/reset/amend/stash).
4. Do NOT execute repository scripts or arbitrary shell commands.
5. Git Agent does NOT analyze or interpret code.
6. Output MUST be clean JSON—no extra text or logs.
7. Always use a fresh workspace to keep operations idempotent.

--- Output Formats ---

If only cloning + branching:
{{
  "repo_path": "<local workspace path>",
  "branch": "<created feature branch>"
}}

If patches/tests/commits are also used:
{{
  "repo_path": "<local workspace path>",
  "branch": "<created feature branch>",
  "changed_files": [...],
  "commit_hash": "<hash>",
  "summary": "Repository prepared for the next agent"
}}

--- Safety Notes ---
• Do not interpret code or validate patch logic.  
• Only verify that patches can be applied.  
• Provide clear metadata so downstream agents can continue work safely.
"""

tools = [
    init_git_agent,
    clone_or_open_repo,
    create_branch,
    apply_patch,
    run_tests,
    commit,
    commit_and_push
]

root_agent = Agent(
    name="git_agent",
    model="gemini-2.5-flash-lite",     # "gemini-2.5-flash-lite" , gemini-2.0-flash-exp , gemini-2.5-pro
    description="Git automation using manual Python functions.",
    instruction=instruction,
    tools=tools 
)
