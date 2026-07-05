import re
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Allowlist patterns for accepted GitHub URL formats.
#
# Security design: allowlist (not blocklist).
# Only two well-defined formats are permitted:
#   SSH:   git@github.com:owner/repo(.git)
#   HTTPS: https://github.com/owner/repo(.git)
#
# Everything else — including github.io, github.dev, bitbucket, or any
# other host — is rejected. This prevents the git clone tool from
# connecting to attacker-controlled servers via a crafted URL.
# ---------------------------------------------------------------------------

_SSH_PATTERN = re.compile(
    r"^git@github\.com:[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?$"
)

_HTTPS_PATTERN = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+(\.git)?$"
)

# Characters that must never appear in a URL passed to subprocess git clone.
# These could be used to escape the argument and inject arbitrary shell commands.
_INJECTION_CHARS = [";", "&&", "||", "`", "$", ">", "<", "|", "\n", "\r", "\x00"]


def validate_github_url(github_url: str) -> dict:
    """
    Validate that the GitHub URL is safe and matches an accepted format.

    AI-Agent Contract:
    ------------------
    Always returns structured response — NEVER raises.
        {
            "valid":          True / False,
            "sanitized_url":  "<clean url>" or None,
            "format":         "ssh" | "https" | None,
            "owner":          "<owner>" or None,
            "repo":           "<repo>" or None,
            "error":          None or "<reason>"
        }

    The agent MUST call this first, before any git or API operation.
    If valid=False, the pipeline must stop and return the error to the user.

    Security checks (in order):
    1. Type and presence check — must be a non-empty string.
    2. Length cap — rejects inputs over 300 characters (prevents buffer-stuffing).
    3. Injection character scan — blocks shell-special sequences.
    4. Allowlist match — only github.com SSH and HTTPS formats are accepted.

    On success, also extracts owner and repo so downstream agents
    never need to re-parse the URL.
    """
    # --- Check 1: type and presence ---
    if not github_url or not isinstance(github_url, str):
        return {
            "valid": False,
            "sanitized_url": None,
            "format": None,
            "owner": None,
            "repo": None,
            "error": "github_url is missing or not a string.",
        }

    url = github_url.strip()

    # --- Check 2: length cap ---
    if len(url) > 300:
        return {
            "valid": False,
            "sanitized_url": None,
            "format": None,
            "owner": None,
            "repo": None,
            "error": "github_url exceeds the maximum allowed length of 300 characters.",
        }

    # --- Check 3: shell injection characters ---
    for char in _INJECTION_CHARS:
        if char in url:
            return {
                "valid": False,
                "sanitized_url": None,
                "format": None,
                "owner": None,
                "repo": None,
                "error": (
                    f"github_url contains a disallowed character sequence: {repr(char)}. "
                    "Only plain GitHub repository URLs are accepted."
                ),
            }

    # --- Check 4: allowlist match and owner/repo extraction ---
    if _SSH_PATTERN.match(url):
        # git@github.com:owner/repo.git  →  path = "owner/repo.git"
        path = url.split(":", 1)[1].rstrip("/")
        parts = path.split("/")
        owner = parts[0]
        repo = parts[1].removesuffix(".git")
        return {
            "valid": True,
            "sanitized_url": url,
            "format": "ssh",
            "owner": owner,
            "repo": repo,
            "error": None,
        }

    if _HTTPS_PATTERN.match(url):
        # https://github.com/owner/repo.git  →  path = "/owner/repo.git"
        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")
        owner = parts[0]
        repo = parts[1].removesuffix(".git")
        return {
            "valid": True,
            "sanitized_url": url,
            "format": "https",
            "owner": owner,
            "repo": repo,
            "error": None,
        }

    # Rejected — does not match any accepted pattern
    return {
        "valid": False,
        "sanitized_url": None,
        "format": None,
        "owner": None,
        "repo": None,
        "error": (
            "github_url does not match an accepted GitHub repository format. "
            "Expected: 'https://github.com/owner/repo(.git)' "
            "or 'git@github.com:owner/repo(.git)'."
        ),
    }


def validate_sonar_token(sonar_token: str) -> dict:
    """
    Validate that the SonarCloud token has the expected structure.

    AI-Agent Contract:
    ------------------
    Always returns:
        { "valid": True/False, "error": None or "<reason>" }

    SonarCloud user tokens are alphanumeric strings in a known length range.
    Rejecting tokens that don't match this format prevents:
    - Accidentally forwarding GitHub tokens or passwords to the Sonar API.
    - Injection of crafted strings into the HTTP Authorization header.

    Note: this does NOT verify the token against SonarCloud — that is done
    by sonar_init + sonar_project_exists in Agent 1. This check is purely
    structural, ensuring the string is safe to forward.
    """
    if not sonar_token or not isinstance(sonar_token, str):
        return {
            "valid": False,
            "error": "sonar_token is missing or not a string.",
        }

    token = sonar_token.strip()

    if len(token) < 10 or len(token) > 200:
        return {
            "valid": False,
            "error": (
                f"sonar_token has an unexpected length ({len(token)} chars). "
                "Expected between 10 and 200 characters."
            ),
        }

    # SonarCloud tokens are alphanumeric with optional hyphens and underscores.
    # Reject anything containing whitespace or special characters that could
    # be injected into an HTTP header.
    if not re.match(r"^[A-Za-z0-9_\-]+$", token):
        return {
            "valid": False,
            "error": (
                "sonar_token contains invalid characters. "
                "Only alphanumeric characters, hyphens, and underscores are accepted."
            ),
        }

    return {"valid": True, "error": None}


def validate_github_token(github_token: str) -> dict:
    """
    Validate the GitHub Personal Access Token (PAT) format.

    AI-Agent Contract:
    ------------------
    Always returns:
        {
            "valid":            True / False,
            "provided":         True / False,
            "error":            None or "<reason>"
        }

    This token is OPTIONAL — it is only required for automatic PR creation.
    If not provided (empty string or None), returns valid=True, provided=False.
    The pipeline continues without PR creation in that case.

    GitHub PAT formats accepted:
    - Classic PAT:     ghp_<36 alphanumeric chars>
    - Fine-grained:    github_pat_<alphanumeric + underscores, 82+ chars>

    Rationale: validating the prefix prevents accidentally passing a
    SonarCloud token or other secret as the GitHub token, which would
    result in a confusing 401 from the GitHub API deep in Agent 2.
    """
    # Optional field — absence is not an error
    if not github_token or not isinstance(github_token, str) or not github_token.strip():
        return {
            "valid": True,
            "provided": False,
            "error": None,
        }

    token = github_token.strip()

    # Reject injection characters in token (extra safety for HTTP headers)
    for char in ["\n", "\r", " ", "\t"]:
        if char in token:
            return {
                "valid": False,
                "provided": True,
                "error": "github_token contains whitespace or newline characters, which are not allowed.",
            }

    # Check known GitHub PAT prefixes
    classic_pattern = re.compile(r"^ghp_[A-Za-z0-9]{36}$")
    fine_grained_pattern = re.compile(r"^github_pat_[A-Za-z0-9_]{82,}$")

    if classic_pattern.match(token) or fine_grained_pattern.match(token):
        return {
            "valid": True,
            "provided": True,
            "error": None,
        }

    return {
        "valid": False,
        "provided": True,
        "error": (
            "github_token does not match a recognised GitHub PAT format. "
            "Expected a classic token starting with 'ghp_' or a fine-grained "
            "token starting with 'github_pat_'. "
            "If you do not have a GitHub token, leave the field empty to skip PR creation."
        ),
    }


# Tool list exported for use in agent.py
validation_tools = [
    validate_github_url,
    validate_sonar_token,
    validate_github_token,
]
