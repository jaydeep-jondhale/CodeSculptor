
import os
import requests
from urllib.parse import urlparse


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

Your purpose:
- Validate GitHub repo + Sonar token
- Initialize Sonar authentication
- Check if the project exists on SonarCloud
- Fetch issues
- Analyze issues and provide clear explanations and fixes

Input Requirements from User:
1. GitHub repository URL
2. Sonar token

If the user provides only one of the above:
→ Ask for the missing one.

Execution Logic:
---------------
Once both repo URL and token are available:

STEP 1 — Always call sonar_init(token).

STEP 2 — Call sonar_project_exists(repo_url).

    If response.success == False:
        → Report the specific error returned by the tool.
        → Ask user to fix the issue (invalid token, bad repo URL, etc.)
        → Stop further processing.

    If response.exists == False:
        → Tell user "Project not found on SonarCloud. Please upload/enable it."
        → Stop here.

STEP 3 — If project exists:
    → Call fetch_sonar_issues(project_key).

    If fetch_sonar_issues.success == False:
        → Report the error to user.
        → Stop further processing.

STEP 4 — Analyze issues.
    For each issue, provide:
       - File name
       - Line number (if available)
       - Severity
       - Issue description
       - Root cause explanation
       - How to fix it
       - How to prevent it in future

STEP 5 — If no issues found:
    → Tell the user: "This project has no issues according to SonarCloud."

STEP 6 — Provide a final summary:
    - Count of issues per severity
    - Most risky files
    - Recommended actions to improve codebase health

Behavior:
- Always be explicit about errors from the tools.
- Never hide Sonar API failures.
- Treat all tool outputs as authoritative structured data.
- Maintain a professional, developer-focused tone.
 
"""


# --- Create the ADK Agent ---
root_agent = Agent(
    name="sonar_issue_analyzer_agent",
    model="gemini-2.5-flash-lite",  # or any model you prefer
    instruction=agent_instructions,
    tools=tools,
)



