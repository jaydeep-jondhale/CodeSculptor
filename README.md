# CodeSculptor - Multi-Agent Refactoring Tool

Built with Google ADK · Gemini 2.5 Pro · SonarCloud · GitHub API

---

## Problem Statement

Every software project accumulates technical debt — bugs, security vulnerabilities, and code smells that slow teams down. Tools like SonarCloud make these problems visible, but visibility alone does not fix anything.

The real bottleneck is the developer time required to read each issue, navigate to the flagged file, understand the surrounding context, apply a safe fix, write a regression test, and open a Pull Request for review. For a project with dozens of open issues, this can consume an entire developer day — for work that is mechanical, repetitive, and well-understood.

CodeSculptor automates this entire workflow using a multi-agent pipeline powered by Gemini 2.5 Pro.

---

## Why Agents?

A static script can fetch SonarCloud issues. It cannot:

- Reason about code context to understand *why* a null pointer occurs
- Generate safe, minimal patches that preserve surrounding logic
- Adapt fix strategies across different languages and frameworks
- Write meaningful tests that reflect the intent of the fixed code
- Verify the build still compiles and all tests pass after changes

CodeSculptor uses a 6-agent sequential pipeline where each agent has a focused responsibility, and the structured output of each stage becomes the context for the next. This is fundamentally an agentic task — multi-step, context-aware, and requiring language model reasoning at every stage.

---

## Architecture

### Pipeline

```
User Input (github_url · sonar_token · github_token)
      |
      v
Agent 0: Validator           -- Security guardrails, input sanitization
      |  output_key: "validation-result"
      v
Agent 1: Sonar Analyzer      -- Fetch all bugs, vulnerabilities, code smells
      |  output_key: "sonar-issue-summary"
      v
Agent 2: Refactorer          -- Clone, fix, commit, open Pull Request
      |  output_key: "issue-fix-summary"
      v
Agent 3: Test Agent          -- Detect framework, write tests, run and verify
      |  output_key: "test-summary"
      v
Agent 4: Build + Re-query    -- Compile project, run full suite, re-query Sonar
      |  output_key: "build-and-recheck-summary"
      v
Agent 5: Reporter            -- Synthesize all outputs into a Markdown report
         output_key: "final-report"
```

### Why SequentialAgent?

The pipeline has a strict data dependency chain. Agent 1 cannot run until Agent 0 validates inputs; Agent 2 cannot fix code without Agent 1's issue list; Agent 3 cannot write tests without knowing which files Agent 2 modified, and so on. A `SequentialAgent` enforces this ordering and passes session state between sub-agents automatically via ADK's built-in state mechanism. Each agent writes its result to an `output_key`; every downstream agent reads all prior keys from session state.

### Technology Stack

| Component | Technology |
|---|---|
| Agent Framework | Google ADK (`google-adk`) |
| Language Model | Gemini 2.5 Pro |
| Orchestration | ADK `SequentialAgent` |
| Code Analysis | SonarCloud REST API |
| Version Control | Git (subprocess) + GitHub REST API |
| Language | Python 3.10+ |
| Interface | ADK Web UI (`adk web`) |

---

## Agents

### Agent 0 — Input Validation Agent

The pipeline's security gate. Validates and sanitizes all user-provided inputs using an allowlist approach before any API call, git operation, or file access is triggered.

Security threats blocked:
- Connecting to attacker-controlled servers via crafted repository URLs
- Shell injection via special characters in URLs (`;`, `&&`, `|`, `` ` ``, `$`)
- Malformed tokens being forwarded to external APIs

| Tool | Purpose |
|---|---|
| `validate_github_url` | Regex allowlist: SSH and HTTPS GitHub.com patterns only |
| `validate_sonar_token` | Format check: alphanumeric, 10–100 characters |
| `validate_github_token` | Optional: validates GitHub PAT format for PR creation |
| `sanitize_commit_message` | Strips injection characters, enforces 72-character limit |

---

### Agent 1 — Sonar Issue Analyzer Agent

Connects to SonarCloud, verifies the project is registered on SonarQube, and fetches all open issues across all severity levels and types. Produces a structured JSON summary consumed by Agent 2. Never touches the repository.

| Tool | Purpose |
|---|---|
| `sonar_init` | Register SonarCloud token in environment |
| `sonar_project_exists` | Verify project exists on SonarCloud, derive project key |
| `github_to_sonar_project_key` | Convert GitHub URL to `owner_repo` key format |
| `fetch_sonar_issues` | Paginated fetch of all Bugs, Vulnerabilities, Code Smells |

---

### Agent 2 — Refactoring Agent

Reads the issue list from session state, clones the repository, creates a timestamped feature branch, and iterates through each issue. For each, it extracts focused context around the flagged line, generates a minimal LLM-powered patch, writes it back, and tracks modified files. After all fixes, commits, pushes the branch, and opens a Pull Request via the GitHub REST API.

| Tool | Purpose |
|---|---|
| `git_clone_repo` | Clone repository into a stable workspace directory |
| `create_feature_branch` | Create isolated `feature/sonar_fixes-<timestamp>` branch |
| `extract_code_context` | Extract lines around flagged line for focused LLM context |
| `read_file` | Read full file content when required |
| `write_file` | Write fixed content back to the file |
| `commit_changes` | Stage only modified files, commit, push to remote |
| `github_init` | Register GitHub PAT for API calls |
| `create_pull_request` | Open a PR via GitHub REST API with a generated description |

Fix strategy: `extract_code_context` is used instead of full file reads to minimize token usage. Patches are minimal — only flagged lines are changed, surrounding logic is preserved.

---

### Agent 3 — Test Agent

Ensures test coverage exists for every file changed by Agent 2. Auto-detects the build system and test framework, locates or creates the corresponding test file, generates new test methods using the LLM, runs the tests, and self-corrects once on failure. Only verified-passing tests are committed to the branch.

| Tool | Purpose |
|---|---|
| `detect_build_system` | Inspect repo root for `pom.xml`, `package.json`, `requirements.txt`, etc. |
| `find_existing_test_file` | Locate test file using framework conventions |
| `list_files_in_directory` | Scan test directories to understand existing coverage |
| `read_file` | Read source and existing test files for LLM context |
| `write_file` | Write generated or updated test files |
| `run_tests` | Execute test suite, capture pass/fail counts |
| `commit_test_files` | Commit passing test files to the fix branch |

Supported build systems: Maven, Gradle, npm/Node.js, Python (pytest)

---

### Agent 4 — Build + Sonar Re-Query Agent

The verification agent confirms that the project still compiles and all tests pass after fixes and new tests are committed. Uses compile-only mode (e.g., `mvn compile -q`) for speed — a full package build is not required at this stage. Re-queries SonarCloud for a before/after issue count to include in the final report.

| Tool | Purpose |
|---|---|
| `detect_build_system` | Identify build system (shared with Agent 3) |
| `build_project` | Compile-only build to verify no broken code was introduced |
| `run_full_test_suite` | Final full test run post-commit |
| `get_git_diff_stats` | `git diff --stat` between base and fix branch |
| `sonar_init` | Re-register token for re-query |
| `fetch_sonar_issue_count` | Lightweight count-only fetch for before/after comparison |

Note on Sonar re-query: SonarCloud analyses the main branch by default. The fix branch count will update after the CI pipeline runs on the Pull Request. This is reported transparently in the final output.

---

### Agent 5 — Reporter Agent

Reads all four prior `output_key` values from session state and produces a single Markdown report covering: pipeline status, before/after SonarCloud metrics, per-fix before/after code diffs, test coverage summary, build results, diff statistics, PR link, and next steps.

| Tool | Purpose |
|---|---|
| `get_github_pr_details` | Fetch live PR status from GitHub |
| `generate_markdown_report` | Write the final `.md` report to the workspace |

---

## Tools Reference

| File | Tools | Used By |
|---|---|---|
| `validation_tools.py` | `validate_github_url`, `validate_sonar_token`, `validate_github_token`, `sanitize_commit_message` | Agent 0 |
| `sonar_tools.py` | `sonar_init`, `sonar_project_exists`, `github_to_sonar_project_key`, `fetch_sonar_issues`, `fetch_sonar_issue_count` | Agent 1, 4 |
| `file_tools.py` | `read_file`, `write_file`, `extract_code_context` | Agent 2, 3 |
| `git_tools.py` | `create_or_get_workspace`, `git_clone_repo`, `create_feature_branch`, `commit_changes` | Agent 2 |
| `github_tools.py` | `github_init`, `create_pull_request`, `get_github_pr_details` | Agent 2, 5 |
| `test_tools.py` | `detect_build_system`, `find_existing_test_file`, `list_files_in_directory`, `run_tests`, `commit_test_files` | Agent 3 |
| `build_tools.py` | `build_project`, `run_full_test_suite`, `get_git_diff_stats` | Agent 4 |
| `report_tools.py` | `generate_markdown_report` | Agent 5 |

---

## Getting Started

### Prerequisites

| Requirement | Details |
|---|---|
| Python | 3.10 or higher |
| Google ADK | `pip install google-adk` |
| Google API Key | [AI Studio](https://aistudio.google.com) |
| SonarCloud Account | Project must be registered and scanned at least once |
| SonarCloud Token | User token with `Execute Analysis` permission |
| GitHub SSH Key | Configured locally for `git push` to work |
| GitHub PAT (optional) | `repo` scope — required only for automatic PR creation |

### Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/jaydeep-jondhale/CodeSculptor.git
   cd CodeSculptor
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv

   # Windows
   .venv\Scripts\activate

   # macOS / Linux
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a `.env` file inside the `multi_agent_refactorer/` directory:
   ```env
   GOOGLE_GENAI_USE_VERTEXAI=0
   GOOGLE_API_KEY=<your-google-api-key>
   ```

   All other tokens (SonarCloud, GitHub) are passed at runtime via the prompt — do not add them to `.env`.

### Environment Variables

| Variable | Required | Set By | Description |
|---|---|---|---|
| `GOOGLE_API_KEY` | Yes | `.env` file | Google AI API key for Gemini |
| `GOOGLE_GENAI_USE_VERTEXAI` | Yes | `.env` file | `0` for AI Studio, `1` for Vertex AI |
| `SONAR_TOKEN` | Runtime | Agent 1 via `sonar_init` | SonarCloud API token — passed via prompt |
| `GITHUB_TOKEN` | Optional | Agent 2 via `github_init` | GitHub PAT for PR creation — passed via prompt |

---

## Usage

Launch the ADK web interface from the project root:

```bash
adk web
```

Open `http://localhost:8000`, select **`multi_agent_refactorer`**, and provide a prompt:

```
github_url = git@github.com:your-username/your-repo.git
sonar_token = <your-sonarcloud-token>
github_token = <your-github-pat>
```

The pipeline runs fully autonomously: validates inputs, fetches issues, applies fixes, writes tests, builds the project, and generates a report with a PR link.

---

## Project Structure

```
CodeSculptor/
├── README.md
├── design.md                             ← System design document
├── requirements.txt
│
└── multi_agent_refactorer/
    ├── agent.py                          ← SequentialAgent + all 6 agents
    ├── __init__.py
    │
    ├── instructions/                     ← LLM system prompts per agent
    │   ├── validator_instructions.py
    │   ├── analyzer_instructions.py
    │   ├── refactorer_instructions.py
    │   ├── test_agent_instructions.py
    │   ├── build_recheck_instructions.py
    │   └── reporter_instructions.py
    │
    └── tools/                            ← Tool functions per agent
        ├── validation_tools.py
        ├── sonar_tools.py
        ├── file_tools.py
        ├── git_tools.py
        ├── github_tools.py
        ├── test_tools.py
        ├── build_tools.py
        └── report_tools.py
```

---

## Security Notes

- All tokens are passed at runtime via the ADK interface and stored in environment variables, never in source files
- GitHub URLs are validated against a regex allowlist — only `github.com` SSH and HTTPS formats are accepted
- Shell-special characters are blocked before any subprocess call
- The git commit tool stages only the specific files modified by the agent, not `git add -A`

---

## Troubleshooting

| Problem | Likely Cause | Solution |
|---|---|---|
| `Project not found on SonarCloud` | Project key mismatch | Ensure the repo is imported in SonarCloud and scanned at least once |
| `Git clone timed out` | SSH key not configured | Run `ssh -T git@github.com` to verify SSH access |
| `SONAR_TOKEN missing` | Missing from prompt | Add `sonar_token = <token>` to the prompt |
| `Build system: unknown` | Unsupported project type | Agents 3 and 4 skip build/test steps; fixes are still applied |
| `PR creation rejected (422)` | PR already exists for branch | Close the existing PR or let the agent use a fresh run |
| `Commit failed` | Git identity not configured | Run `git config --global user.email` and `git config --global user.name` |

---

## Design Document

See [design.md](design.md) for the full system design including data flow schemas, orchestration options, and future scope.
