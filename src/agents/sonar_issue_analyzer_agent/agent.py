
import os
from typing import final

import requests
from urllib.parse import urlparse
import os
import shutil
import subprocess
from pathlib import Path
from google.genai import types
from google.adk.models.google_llm import Gemini


retry_config=types.HttpRetryOptions(
    attempts=5,  # Maximum retry attempts
    exp_base=7,  # Delay multiplier
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504], # Retry on these HTTP errors
)



def sonar_init(token: str) -> None:
    """
    Store the Sonar authentication token in the environment.

    This sets the `SONAR_TOKEN` environment variable so other parts of the
    application can read it for authenticating requests to SonarCloud or a
    SonarQube server. Note: this will overwrite any existing value and does
    not perform token validation.
    """
    os.environ["SONAR_TOKEN"] = token


def fetch_sonar_issues(project_key: str, page_size: int = 500) -> dict:
    """
    Fetch issues from SonarCloud with full error reporting.

    Why structured response?
    ------------------------
    Agents need actionable data:
      - Did the API fail?
      - Did the token fail?
      - Did the project have 0 issues?
      - Is the issue list complete?

    This function NEVER raises exceptions.
    It always returns:
        {
          "success": True/False,
          "issues": [...],
          "error": "text message (if failed)"
        }

    This allows an AI agent to decide whether to retry, ask for a new token,
    or continue with analysis.
    """
    token = os.getenv("SONAR_TOKEN")
    if not token:
        return {
            "success": False,
            "issues": [],
            "error": "SONAR_TOKEN missing — call sonar_init(token) first."
        }

    url = "https://sonarcloud.io/api/issues/search"
    issues = []
    page = 1

    try:
        while True:
            params = {
                "projectKeys": project_key,
                "types": "BUG,VULNERABILITY,CODE_SMELL",
                "ps": page_size,
                "p": page
            }

            resp = requests.get(url, params=params, auth=(token, ""), timeout=10)

            # Failure → return structured error, never raise
            if not resp.ok:
                return {
                    "success": False,
                    "issues": [],
                    "error": f"Sonar API returned HTTP {resp.status_code}: {resp.text}"
                }

            data = resp.json()

            # Normalize issue objects for agent consumption
            for issue in data.get("issues", []):
                file_path = issue.get("component") or ""
                if ":" in file_path:
                    file_path = file_path.split(":", 1)[1]

                issues.append({
                    "message": issue.get("message"),
                    "severity": issue.get("severity"),
                    "file": file_path,
                    "line": issue.get("textRange", {}).get("startLine")
                })

            # Stop when all pages processed
            total = data.get("total", 0)
            if len(issues) >= total or not data.get("issues"):
                break

            page += 1

        return {
            "success": True,
            "issues": issues,
            "error": None
        }

    except requests.RequestException as e:
        return {
            "success": False,
            "issues": [],
            "error": f"Network/API error: {e}"
        }



def github_to_sonar_project_key(repo_link: str) -> str:
    """
    Convert a GitHub repo reference to Sonar project key format: owner_repo.
    Accepts:
      - https://github.com/owner/repo.git
      - git@github.com:owner/repo.git
      - owner/repo
      - https://github.com/owner/repo/...
    """
    if not repo_link:
        raise ValueError("Empty repository link")

    repo_link = repo_link.strip()

    # SSH form: git@github.com:owner/repo.git
    if repo_link.startswith("git@"):
        try:
            path = repo_link.split(":", 1)[1]
        except IndexError:
            raise ValueError("Unrecognized SSH GitHub format")
    else:
        parsed = urlparse(repo_link)
        if parsed.scheme and parsed.netloc:
            # URL form: https://github.com/owner/repo(.git)/...
            path = parsed.path
        else:
            # assume short form: owner/repo
            path = repo_link

    path = path.strip("/")

    parts = path.split("/")
    if len(parts) < 2:
        raise ValueError("Could not extract owner and repo from input")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    return f"{owner}_{repo}"



def sonar_project_exists(repo_link: str, api_url: str = "https://sonarcloud.io/api/project_branches/list") -> dict:
    """
    Check if a SonarCloud project exists for a GitHub repository,
    and return detailed structured information instead of booleans.

    Why structured response?
    ------------------------
    AI agents need *explicit signals* about what happened:
      - Was the input invalid?
      - Is the token missing?
      - Did the API reject the token?
      - Did the project actually exist?
      - Was there a network failure?

    This method NEVER throws exceptions.
    Instead, it returns:
        {
          "success": True/False,   # whether API call was successful
          "exists": True/False,    # whether project exists on SonarCloud
          "project_key": "...",    # normalized project key (owner_repo)
          "error": "text message"  # reason if something failed
        }

    Agents can use this to branch logic cleanly.
    """
    # Convert GitHub repo link to Sonar project key
    try:
        project_key = github_to_sonar_project_key(repo_link)
    except ValueError as e:
        return {
            "success": False,
            "exists": False,
            "error": f"Invalid GitHub repo link — {e}"
        }

    # Ensure token exists — agents must call sonar_init first
    token = os.getenv("SONAR_TOKEN")
    if not token:
        return {
            "success": False,
            "exists": False,
            "project_key": project_key,
            "error": "SONAR_TOKEN missing — call sonar_init(token) first."
        }

    # Call Sonar API
    try:
        resp = requests.get(api_url, params={"project": project_key}, auth=(token, ""), timeout=10)

        # Project not found (404)
        if resp.status_code == 404:
            return {
                "success": True,
                "exists": False,
                "project_key": project_key,
                "error": "Project not found on SonarCloud."
            }

        # Non-success API response (e.g. invalid token)
        if not resp.ok:
            return {
                "success": False,
                "exists": False,
                "project_key": project_key,
                "error": f"Sonar API returned HTTP {resp.status_code}: {resp.text}"
            }

        # Success: project exists
        return {
            "success": True,
            "exists": True,
            "project_key": project_key,
            "error": None
        }

    except requests.RequestException as e:
        # Network or unexpected failure
        return {
            "success": False,
            "exists": False,
            "project_key": project_key,
            "error": f"Network/API error: {e}"
        }



# ============================
# AI AGENT DEFINITION SECTION
# ============================

from google.adk.agents import Agent, SequentialAgent, LoopAgent

# --- Register tools directly ---
tools = [
    sonar_init,
    sonar_project_exists,
    fetch_sonar_issues
]


# --- SYSTEM INSTRUCTIONS FOR THE AGENT ---
agent_instructions = '''
agent_instructions = """
You are a sonar-agent responsible for fetching sonar issues.

Your responsibilities:
1. Validate that required inputs are present.
2. Initialize SonarCloud authentication using sonar_init(token).
3. Check if the Sonar project exists using sonar_project_exists(repo_url).
4. If project exists, fetch issues using fetch_sonar_issues(project_key).
5. Return all results or errors in structured JSON ONLY.

Input format:
{
    "github_url": "<url>",
    "sonar_token": "<token>"
}

Processing logic:
------------------

STEP 1 — Validate inputs (INTERNAL ONLY)
If any required input is missing:
{
    "error": "missing_input",
    "missing": ["github_url"] OR ["sonar_token"]
}
Return immediately. Do NOT ask the user for anything.

STEP 2 — Initialize Sonar
Call sonar_init(token).  
If the tool returns an error:
{
    "error": "sonar_init_failed",
    "details": "<tool error>"
}
Return immediately.

STEP 3 — Check project existence
Call sonar_project_exists(repo_url).

If tool call fails (no response, API error, network failure):
{
    "error": "project_check_failed",
    "details": "<tool error>"
}
Return immediately.

If tool reports project does NOT exist:
{
    "error": "project_not_found",
    "project_key": "<derived key>"
}
Return immediately.

STEP 4 — Fetch issues
Call fetch_sonar_issues(project_key).

If the fetch function fails:
{
    "error": "fetch_failed",
    "details": "<tool error>"
}
Return immediately.

STEP 5 — Produce final machine-readable output
If issues list is empty:
{
    "issues": [],
    "summary": {
        "total": 0
    }
}

If issues exist:
{
    "issues": [...],
    "summary": {
        "total": <count>,
        "severity_breakdown": {
            "BLOCKER": n,
            "CRITICAL": n,
            "MAJOR": n,
            "MINOR": n,
            "INFO": n
        }
    }
}

Behavior rules:
---------------
• ALWAYS return structured JSON ONLY.
• Treat all tool outputs as authoritative.
• Never attempt to interpret or modify tool data.
• Never suppress or hide tool errors.
• All error cases must be explicit and machine readable.
• This agent does NOT perform analysis or recommendations.
'''


# --- Create the ADK Agent ---
sonar_agent = Agent(
    name="sonar_issue_analyzer_agent",
    model=Gemini(
        model="gemini-2.5-flash-lite",
        retry_options=retry_config
    ),
    instruction=agent_instructions,
    tools=tools,
    output_key="sonar-issue-summary",
)



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

git_instructions = '''
You are a git-agent responsible for performing git operations.
you will also ask for sonar token, as its required for next agent
Purpose:
- Clone a GitHub repository into the workspace directory.
- ONLY IF the Fixer Agent has produced fixes:
       - Create a feature branch(always create feature branch before commiting).
       - Commit only the modified files.
- Report cloning errors strictly as machine-readable JSON.

Input format:
{
    "github_url": "<url>",
    "sonar_token":<sonar_token>
}

Processing Logic:
------------------

STEP 1 — Validate input (INTERNAL ONLY)
If github_url is missing or empty:
{
    "error": "missing_input",
    "missing": ["github_url"]
}
Return immediately.

STEP 2 — Clone repository
Always call:
    git_clone_repo(github_url)

The tool MUST be treated as authoritative.

STEP 3 — Handle tool response
If the tool returns:
{
    "success": false,
    "error": "<reason>"
}
Then respond with:
{
    "error": "clone_failed",
    "details": "<reason>"
}

If the tool returns:
{
    "success": true,
    "path": "<absolute path>",
    "sonar_token":<sonar_token>
}
Then respond with:
{
    "repo_path": "<absolute path>",
    "sonar_token":<sonar_token>
}

Behavior Rules:
---------------
• NEVER make assumptions about workspace paths.
• NEVER perform direct filesystem operations.
• ALWAYS use the tool-provided workspace path and clone path.
• ALWAYS return structured JSON ONLY.
• NEVER suppress or modify git errors..
'''


git_tools =[
    create_or_get_workspace,
    git_clone_repo,
    create_feature_branch,
    commit_changes
]

git_agent = Agent(
    name="git_agent",
    model=Gemini(
        model="gemini-2.5-flash-lite",
        retry_options=retry_config
    ),
    instruction=git_instructions,
    tools=git_tools,
    output_key="git-status-summary"
)



def read_file(file_path: str) -> dict:
    """
    Read a file and return its content.

    AI-Agent Contract:
    ------------------
    Always return structured:
        { success, content, error }

    Used for:
    - Reading code that contains the Sonar issue
    """
    if not os.path.exists(file_path):
        return {
            "success": False,
            "content": None,
            "error": f"File not found: {file_path}"
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"success": True, "content": content, "error": None}

    except Exception as e:
        return {"success": False, "content": None, "error": str(e)}



def write_file(file_path: str, content: str) -> dict:
    """
    Writes content back to a file.

    AI-Agent Contract:
    ------------------
    Always return structured response.

    Used for:
    - Writing AI-fixed code back into repository
    """
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return {"success": True, "error": None}

    except Exception as e:
        return {"success": False, "error": str(e)}



def extract_code_context(file_path: str, line_number: int, window: int = 5) -> dict:
    """
    Extract a small window of lines around the issue.

    Why?
    ----
    LLM needs surrounding context to fix the issue safely.

    Returns:
        {
            success: True/False,
            context: "string block",
            start_line: int,
            error: str or None
        }
    """

    if not os.path.exists(file_path):
        return {
            "success": False,
            "context": None,
            "error": f"File not found: {file_path}",
            "start_line": None
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = line_number - 1
        start = max(idx - window, 0)
        end = min(idx + window + 1, len(lines))

        snippet = "".join(lines[start:end])

        return {
            "success": True,
            "context": snippet,
            "start_line": start + 1,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "context": None,
            "error": str(e),
            "start_line": None
        }


fixer_instructions = '''

You are Code Auto-Fixer for sonar issues.
Your inputs come from upstream agents:
- Git Sub-Agent → provides: {git-status-summary}
- Sonar Sub-Agent → provides: {sonar-issue-summary}

You must generate minimal, precise code fixes for each issue.
any custom exception needed as part of fix, do not assume its already there, create that as well

Expected Input:
{
    "repo_path": "<absolute-path>",
    "issues": [
        {
            "message": "<sonar message>",
            "severity": "<level>",
            "file": "src/example.py",
            "line": 42
        },
        ...
    ]
}

Processing Logic:
------------------

For each issue in issues:

STEP 1 — Resolve Full File Path - this step strictly need to be followed
full_file_path = repo_path + "/" + issue["file"], to avoid file not found error

If full_path does not exist:
Record:
{
    "file": issue["file"],
    "line": issue["line"],
    "issue": issue["message"],
    "status": "skipped",
    "error": "file_not_found"
}
Continue to next issue.

STEP 2 — Extract Context
Call:
    extract_code_context(full_path, line)

If tool fails:
Record:
{
    "file": issue["file"],
    "line": issue["line"],
    "issue": issue["message"],
    "status": "skipped",
    "error": "<tool error>"
}
Continue.

STEP 3 — Generate Fix
Use ONLY:
- the code context returned
- the issue["message"]
Produce a corrected version of ONLY the affected/related code.
Rules:
• Apply minimal required changes, stick to the issue no extra changes.
• Preserve formatting & style.
• Preserve logic unless a logic change is required for the fix.
• If the issue requires adding new constructs (examples: new class, helper function, interface, enum, constant, import, wrapper, utility method, type, etc.):
     → Then create the required construct inside the SAME FILE.
     → Place it in a logically correct location (top-level, before or after class, after imports, etc.).
     → Ensure it is minimal, safe, and fits the project's style.
   - Example: If Sonar flags "Generic exception is used", and a specific exception class is missing, create a new specific exception class in the same file.
   - This rule applies for ANY missing construct, not only exceptions.
• If unsure, apply only safe improvements.
• Produce BEFORE and AFTER code blocks.



If a safe fix cannot be produced:
Record:
{
    "file": issue["file"],
    "line": issue["line"],
    "issue": issue["message"],
    "status": "skipped",
    "error": "cannot_generate_fix"
}
Continue.

STEP 4 — Write Updated File
Use:
    write_file(full_path, updated_content)

If writing fails:
Record:
{
    "file": issue["file"],
    "line": issue["line"],
    "issue": issue["message"],
    "status": "failed",
    "error": "<tool error>"
}
Continue.

If writing succeeds:
Record:
{
    "file": issue["file"],
    "line": issue["line"],
    "issue": issue["message"],
    "status": "fixed",
    "fix_applied": "short description",
    "error": null
}

Final Output:
-------------
Always return:
listOfFiles are used by git-agent to code commit
{
    "fix_results": [
        {
            "file": "...",
            "line": <int>,
            "issue": "...",
            "status": "fixed" | "skipped" | "failed",
            "fix_applied": "...",
            "error": null | "<message>"
        },
        ...
    ],
    listOfFiles:[]
}

Behavior Rules:
---------------
• provide summary.
• ALWAYS treat tool outputs as authoritative.
• NEVER hide tool errors.
• NEVER modify unrelated parts of the file.
'''
fixer_tools = [
    read_file,
    write_file,
    extract_code_context
]

fixer_agent = Agent(
    name="sonar_issue_fixer",
    model=Gemini(
        model="gemini-2.5-flash-lite",
        retry_options=retry_config
    ),
    instruction=fixer_instructions,
    tools=fixer_tools,
    output_key="issue-fix-summary"
)


root_agent = SequentialAgent(
    name="SonarIssueFixPipline",
    sub_agents=[git_agent,sonar_agent,fixer_agent],
)


