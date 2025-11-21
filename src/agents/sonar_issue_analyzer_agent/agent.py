
import os
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import requests
from urllib.parse import urlparse

from sqlalchemy import Boolean


def sonar_init(token: str) -> None:
    """
    Store the Sonar authentication token in the environment.

    This sets the `SONAR_TOKEN` environment variable so other parts of the
    application can read it for authenticating requests to SonarCloud or a
    SonarQube server. Note: this will overwrite any existing value and does
    not perform token validation.
    """
    os.environ["SONAR_TOKEN"] = token

def fetch_sonar_issues(project_key: str, page_size: int = 500) -> list:
    """
    Retrieve all issues from SonarCloud for the given `project_key`.

    Returns:
      A list of dicts, each with keys:
        - "message": issue message text
        - "severity": issue severity (e.g., BLOCKER, CRITICAL, MAJOR, ...)
        - "file": repository-relative file path where the issue was detected

    Notes for AI agent/tool use:
      - Authentication: this function reads the Sonar token from the environment
        variable `SONAR_TOKEN` and uses it as the basic-auth username with an
        empty password.
      - Pagination: the Sonar API is paginated; this function iterates pages
        until it has collected the reported total or no more issues are returned.
      - Errors: HTTP errors will raise `requests.HTTPError` via
        `response.raise_for_status()`. Agents should catch network/auth errors
        if they want a non-exception flow (see `sonar_project_exists` for a
        boolean-check approach).
      - Returned file paths: Sonar's `component` field may include a prefix
        like `module:path/to/file`; the code strips a leading prefix before
        returning the path.
    """
    url = "https://sonarcloud.io/api/issues/search"
    issues = []
    page = 1

    # Read token from environment once per call.
    token = os.getenv("SONAR_TOKEN")

    while True:
        # Query parameters for this page.
        params = {
            "projectKeys": project_key,
            "types": "BUG,VULNERABILITY,CODE_SMELL",
            "ps": page_size,
            "p": page
        }

        # Authenticate using token as username and empty password (Sonar convention).
        # This call will raise on non-2xx responses; callers should handle exceptions.
        response = requests.get(url, params=params, auth=(token, ""))
        response.raise_for_status()
        data = response.json()

        # Normalize each issue into the compact structure expected by downstream tools.
        for issue in data.get("issues", []):
            file_path = issue.get("component") or ""
            # Sonar component values can be like "module:src/main.py"; keep only the path.
            if ":" in file_path:
                file_path = file_path.split(":", 1)[1]

            issues.append({
                "message": issue.get("message"),
                "severity": issue.get("severity"),
                "file": file_path
            })

        # Stop when we've collected at least the reported total, or no issues were returned.
        total = data.get("total", 0)
        if len(issues) >= total or not data.get("issues"):
            break
        page += 1

    return issues

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



def sonar_project_exists(repo_link: str, api_url: str = "https://sonarcloud.io/api/project_branches/list") -> bool:
    """
    Check whether a Sonar project exists for a given GitHub repository.

    Details:
    - Converts common GitHub repository references (SSH, HTTPS, or `owner/repo`) into the Sonar project key
      using `github_to_sonar_project_key`.
    - Calls the Sonar `project_branches/list` API to determine existence. A successful HTTP response (`2xx`)
      is treated as evidence the project exists.
    - Authentication is performed via the `SONAR_TOKEN` environment variable (token passed as basic-auth
      username with an empty password).
    - The function is resilient for use in AI agent flows: it returns a boolean and does not raise on network/
      HTTP errors, making it safe for pre-checks before attempting heavier Sonar operations.

    Returns:
      True if Sonar responds successfully for the given project key, False for invalid input, missing token,
      non-2xx responses, or network/auth errors.
    """
    try:
        project = github_to_sonar_project_key(repo_link)
    except ValueError:
        return False

    params = {"project": project}
    token = os.getenv("SONAR_TOKEN")

    try:
        response = requests.get(api_url, params=params, auth=(token, ""), timeout=10)
        return response.ok
    except requests.RequestException:
        return False


# ============================
# AI AGENT DEFINITION SECTION
# ============================

from google.adk.agents import Agent

# --- Register tools directly ---
tools = [
    sonar_init,
    sonar_project_exists,
    fetch_sonar_issues
]


# --- SYSTEM INSTRUCTIONS FOR THE AGENT ---
agent_instructions = """
You are a Sonar Issue Analyzer AI Agent.

--- Your Responsibility ---
You analyze GitHub repositories by fetching issues from SonarCloud.

--- Input Expectations ---
The user must provide:
1. GitHub repository URL
2. SonarCloud token

If ANY of these is missing, ask the user to provide the missing value.

--- Execution Flow ---
When both inputs are available:

STEP 1 → Call `sonar_init(token)`  
STEP 2 → Call `sonar_project_exists(repo_url)`  

    - If the project does NOT exist:
        Tell user: "Project not found on SonarCloud. Please upload/enable it."

    - If it DOES exist:
        Convert GitHub URL to projectKey using the helper function.
        Then call: `fetch_sonar_issues(project_key)`

STEP 3 → Analyze results:
    For each issue:
      - Show file name
      - Show severity
      - Show issue message
      - If Sonar returned line numbers, include them
      - Provide a human-readable explanation
      - Provide a fix & prevention strategy

Finally, produce:
• A summary of all issues  
• A remediation plan to improve the project code quality  
"""


# --- Create the ADK Agent ---
root_agent = Agent(
    name="sonar_issue_analyzer_agent",
    model="gemini-2.5-flash-lite",  # or any model you prefer
    instruction=agent_instructions,
    tools=tools,
)



