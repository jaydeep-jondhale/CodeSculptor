# CodeSculptor — Multi-Agent Codebase Refactorer

## 1. Overview

**Goal:**
Build a multi-agent system that takes a GitHub repository as input and automatically improves its code quality by:

- Validating all user inputs before any operation is triggered
- Detecting bugs, vulnerabilities, and code smells via SonarCloud
- Applying LLM-generated, context-aware code fixes
- Writing and verifying unit tests for every changed file
- Verifying the project builds and all tests pass
- Producing a structured report with a GitHub Pull Request

The system is **modular**: each agent has a single responsibility, a defined input schema (session state keys it reads), and a defined output schema (its `output_key`). New agents can be added to the `SequentialAgent` pipeline without changing existing agents.

---

## 2. Pipeline Overview

```
User Input (github_url · sonar_token · github_token)
      |
      v
Agent 0: Input Validation Agent       output_key: "validation-result"
      |
      v
Agent 1: Sonar Issue Analyzer Agent   output_key: "sonar-issue-summary"
      |
      v
Agent 2: Refactoring Agent            output_key: "issue-fix-summary"
      |
      v
Agent 3: Test Agent                   output_key: "test-summary"
      |
      v
Agent 4: Build + Sonar Re-Query Agent output_key: "build-and-recheck-summary"
      |
      v
Agent 5: Reporter Agent               output_key: "final-report"
      |
      v
    Markdown Report + GitHub Pull Request
```

**Orchestration:** `google.adk.agents.SequentialAgent` runs sub-agents in declaration order. ADK passes session state between them automatically. Each agent writes its result to its `output_key`; every downstream agent can read all prior keys.

---

## 3. Agents

### 3.0 Agent 0 — Input Validation Agent

**Role:** Security gate. Runs before any external API call, git operation, or file access.

**Responsibilities:**
- Parse `github_url`, `sonar_token`, and `github_token` from the user prompt
- Validate the GitHub URL against a strict regex allowlist (SSH and HTTPS GitHub.com formats only)
- Block shell injection characters in the URL (`;`, `&&`, `|`, `` ` ``, `$`, `>`, `<`)
- Validate SonarCloud token format (alphanumeric, 10–200 chars)
- Validate GitHub PAT format if provided (`ghp_*` or `github_pat_*` prefix patterns)
- Stop the pipeline immediately on the first validation failure

**Tools:**

| Tool | Purpose |
|---|---|
| `validate_github_url` | Allowlist match, length cap, injection character scan, owner/repo extraction |
| `validate_sonar_token` | Format and length check |
| `validate_github_token` | Optional PAT format check; absence is not an error |

**Session State Input:** User prompt (raw text)

**Session State Output — `validation-result`:**
```json
{
    "validation_passed": true,
    "github_url": "<sanitized url>",
    "github_url_format": "ssh | https",
    "owner": "<owner>",
    "repo": "<repo>",
    "sonar_token": "<token>",
    "github_token": "<token or empty string>",
    "github_token_provided": true
}
```

---

### 3.1 Agent 1 — Sonar Issue Analyzer Agent

**Role:** Fetch all open code quality issues from SonarCloud for the target repository.

**Responsibilities:**
- Initialize the SonarCloud connection using the provided token
- Verify the repository is registered as a SonarCloud project
- Fetch all open issues (Bugs, Vulnerabilities, Code Smells) with pagination
- Produce a structured, normalized issue list for Agent 2
- Stop the pipeline if no issues are found (nothing to fix)

**Tools:**

| Tool | Purpose |
|---|---|
| `sonar_init` | Store SonarCloud token in `os.environ["SONAR_TOKEN"]` |
| `sonar_project_exists` | Verify project exists on SonarCloud; derive `owner_repo` project key |
| `fetch_sonar_issues` | Paginated fetch of all issues with severity, type, file, and line |

**Session State Input:** `validation-result` (github_url, sonar_token)

**Session State Output — `sonar-issue-summary`:**
```json
{
    "project_key": "owner_repo",
    "issues": [
        {
            "message": "<issue description>",
            "severity": "BLOCKER | CRITICAL | MAJOR | MINOR | INFO",
            "file": "src/main/java/com/app/UserService.java",
            "line": 42
        }
    ],
    "summary": {
        "total": 47,
        "severity_breakdown": {
            "BLOCKER": 2, "CRITICAL": 5, "MAJOR": 18, "MINOR": 20, "INFO": 2
        }
    }
}
```

---

### 3.2 Agent 2 — Refactoring Agent

**Role:** Clone the repository, apply LLM-generated minimal fixes to each flagged file, and open a Pull Request.

**Responsibilities:**
- Clone the repository into a stable workspace directory
- Create a timestamped feature branch (`feature/sonar_fixes-<timestamp>`)
- For each Sonar issue: extract focused context around the flagged line, generate a minimal safe patch using the LLM, write the fix back to the file
- Commit only the modified files to the feature branch and push
- Open a Pull Request via the GitHub REST API if a GitHub token is available

**Tools:**

| Tool | Purpose |
|---|---|
| `git_clone_repo` | Clone into `workspace/<repo>`, delete and re-clone if already exists |
| `create_feature_branch` | Create and checkout `feature/sonar_fixes-<timestamp>` |
| `extract_code_context` | Extract N lines around the flagged line for focused LLM context |
| `read_file` | Read full file content when required |
| `write_file` | Write LLM-fixed content back to the file |
| `commit_changes` | Stage only the listed files, commit, and push with upstream tracking |
| `github_init` | Store GitHub PAT in `os.environ["GITHUB_TOKEN"]` |
| `create_pull_request` | Open a PR via GitHub REST API with a generated title and description |

**Fix strategy:** `extract_code_context` is used instead of full file reads to minimize token usage while giving the LLM sufficient context. Only flagged lines are changed; surrounding logic is preserved.

**Session State Input:** `sonar-issue-summary` (issues, summary), `validation-result` (github_url, github_token)

**Session State Output — `issue-fix-summary`:**
```json
{
    "repo_path": "/absolute/path/to/cloned/repo",
    "branch": "feature/sonar_fixes-20240705_113045",
    "pr_url": "https://github.com/owner/repo/pull/42",
    "fix_results": [
        {
            "file": "src/main/java/com/app/UserService.java",
            "line": 42,
            "status": "fixed | skipped | failed",
            "before": "<original code>",
            "after": "<fixed code>"
        }
    ],
    "files_modified": ["src/main/java/com/app/UserService.java"],
    "total_fixed": 38,
    "total_skipped": 9
}
```

---

### 3.3 Agent 3 — Test Agent

**Role:** Write and verify unit tests for every file changed by Agent 2.

**Responsibilities:**
- Detect the build system and test framework from the repository root
- For each modified source file, locate or derive the path of its corresponding test file
- Read the source file and (if it exists) the existing test file
- Generate new test methods that cover the specific code paths that were fixed
- Run the full test suite to verify the generated tests pass
- Attempt one round of self-correction if any generated test fails
- Commit only the verified-passing test files to the same branch Agent 2 created

**Tools:**

| Tool | Purpose |
|---|---|
| `detect_build_system` | Inspect repo root for `pom.xml`, `build.gradle`, `package.json`, `requirements.txt`, etc. |
| `find_existing_test_file` | Map source file to test file using framework conventions (JUnit, Jest, pytest) |
| `read_file_for_tests` | Read source and existing test files for LLM context |
| `write_test_file` | Write generated test content; auto-creates parent directories |
| `run_tests` | Execute test suite via subprocess; capture exit code, stdout/stderr (tail) |
| `commit_test_files` | Stage only listed test files, commit to fix branch, push |

**Build system support:** Maven · Gradle · npm/Node.js · Python (pytest)

**Test file conventions:**

| Build System | Source | Test |
|---|---|---|
| Maven / Gradle | `src/main/java/com/app/UserService.java` | `src/test/java/com/app/UserServiceTest.java` |
| npm | `src/components/Button.jsx` | `__tests__/Button.test.jsx` |
| Python | `src/user_service.py` | `tests/test_user_service.py` |

**Self-correction loop:** If the generated test file causes the suite to fail, the agent reads the error output, regenerates only the failing test method, re-runs. If it still fails after one correction, that test file is excluded from the commit.

**Session State Input:** `issue-fix-summary` (repo_path, branch, files_modified, fix_results)

**Session State Output — `test-summary`:**
```json
{
    "status": "completed | skipped | partial",
    "build_system": "maven",
    "test_framework": "junit",
    "test_results": [
        {
            "source_file": "src/main/java/com/app/UserService.java",
            "test_file": "src/test/java/com/app/UserServiceTest.java",
            "status": "written | extended | skipped | failed",
            "methods_added": ["testGetUser_nullSafe", "testCreateUser_validation"]
        }
    ],
    "test_run": {
        "success": true,
        "exit_code": 0,
        "output_tail": "<last lines of test runner output>",
        "timed_out": false
    },
    "test_files_committed": ["src/test/java/com/app/UserServiceTest.java"],
    "commit_success": true
}
```

---

### 3.4 Agent 4 — Build + Sonar Re-Query Agent

**Role:** Verify the project compiles and all tests pass after fixes are committed. Re-query SonarCloud for a before/after issue count.

**Responsibilities:**
- Detect the build system (reuses the same detection logic as Agent 3)
- Run a compile-only build to verify no syntax or type errors were introduced
- Run the full test suite as a final quality gate
- Compute git diff statistics between the base branch and the fix branch
- Re-query SonarCloud for the current issue count (before/after comparison for the report)

**Tools:**

| Tool | Purpose |
|---|---|
| `detect_build_system` | Identify build system and compile command |
| `build_project` | Compile-only build (e.g. `mvn compile -q`) — fast, catches broken code |
| `run_full_test_suite` | Full test run post-commit; separate from Agent 3's mid-pipeline run |
| `get_git_diff_stats` | `git diff --stat <base>..<fix_branch>` for report metrics |
| `sonar_init` | Re-register token before re-query |
| `fetch_sonar_issue_count` | Lightweight count-only fetch (ps=1) for before/after comparison |

**Why compile-only (not full build)?**
A full Maven `package` or Gradle `build` downloads dependencies, runs tests, and produces artifacts — it can take minutes. A compile-only step (`mvn compile -q`) takes seconds and is sufficient to detect the one failure mode we care about: did the LLM-generated fix break the code's syntax or type correctness?

**SonarCloud re-query note:**
SonarCloud analyses the main/master branch by default. The fix branch count will update only after the CI pipeline runs on the Pull Request. The re-query captures the state at query time and documents this transparently in the report.

**Session State Input:** `issue-fix-summary` (repo_path, branch), `sonar-issue-summary` (project_key, summary.total), `validation-result` (sonar_token)

**Session State Output — `build-and-recheck-summary`:**
```json
{
    "build_system": "maven",
    "build_success": true,
    "compilation_errors": [],
    "test_run": {
        "success": true,
        "tests_run": 52,
        "tests_failed": 0,
        "output_tail": "<summary line>"
    },
    "diff_stats": {
        "files_changed": 7,
        "insertions": 143,
        "deletions": 89
    },
    "sonar_recheck": {
        "issues_before": 47,
        "issues_after": 47,
        "note": "Fix branch will be scanned by SonarCloud after CI runs on the PR."
    }
}
```

---

### 3.5 Agent 5 — Reporter Agent

**Role:** Synthesize all upstream agent outputs into a single human-readable Markdown report.

**Responsibilities:**
- Read all four prior `output_key` values from ADK session state
- Fetch live Pull Request details from the GitHub API
- Produce a Markdown report covering: pipeline status, before/after SonarCloud metrics, per-fix BEFORE/AFTER code blocks, test coverage summary, build results, diff statistics, PR link, and next steps
- Write the report to the workspace as a `.md` file

**Tools:**

| Tool | Purpose |
|---|---|
| `get_github_pr_details` | Fetch live PR state (open/merged/closed) and URL from GitHub API |
| `generate_markdown_report` | Write the formatted report to `workspace/reports/codesculptor_report_<timestamp>.md` |

**Session State Input:** `validation-result`, `sonar-issue-summary`, `issue-fix-summary`, `test-summary`, `build-and-recheck-summary`

**Session State Output — `final-report`:**
```json
{
    "report_path": "/workspace/reports/codesculptor_report_20240705_113045.md",
    "pr_url": "https://github.com/owner/repo/pull/42",
    "pipeline_success": true,
    "summary": {
        "issues_found": 47,
        "issues_fixed": 38,
        "issues_skipped": 9,
        "tests_written": 5,
        "build_success": true,
        "all_tests_passing": true
    }
}
```

---

## 4. System Architecture

### 4.1 Orchestration

**Framework:** Google Agent Development Kit (`google-adk`)

**Orchestrator type:** `SequentialAgent`

The `SequentialAgent` wraps all six agents and runs them in declaration order. Session state is passed between agents via ADK's built-in state mechanism — each agent's `output_key` is available to all subsequent agents as a session state variable.

**Why SequentialAgent and not ParallelAgent?**
The pipeline has a strict data dependency chain. Agent 1 cannot run without Agent 0's validated URL. Agent 2 cannot fix code without Agent 1's issue list. Agent 3 cannot write tests without the list of files Agent 2 modified. Each step depends on the previous step's output, so parallelism is not applicable here.

### 4.2 Session State Flow

```
validation-result
    └── consumed by: Agent 1, Agent 2, Agent 4

sonar-issue-summary
    └── consumed by: Agent 2, Agent 4, Agent 5

issue-fix-summary
    └── consumed by: Agent 3, Agent 4, Agent 5

test-summary
    └── consumed by: Agent 5

build-and-recheck-summary
    └── consumed by: Agent 5
```

### 4.3 Tool Contract

All tool functions across all agents follow the same contract:
- **Never raise exceptions** — always return a dict
- **Always include a `success` field** (True/False) or equivalent boolean signal
- **Always include an `error` field** (None if success, string if failure)
- **Truncate large outputs** (e.g. test runner stdout) to prevent token overflow

### 4.4 Component Layers

**Agents Layer**
Each agent is a Python module with:
- An instruction file (`instructions/<agent>_instructions.py`) — the LLM system prompt
- A tools file (`tools/<agent>_tools.py`) — deterministic Python functions the LLM can call

**Storage Layer**
- Cloned repository: `workspace/<repo_name>/`
- Generated reports: `workspace/reports/`
- Session state: managed in-memory by ADK for the duration of a run

**Interface Layer**
- ADK Web UI (`adk web`) — browser-based chat interface
- Future: CLI wrapper, IDE plugin

### 4.5 File Structure

```
multi_agent_refactorer/
├── agent.py                          ← SequentialAgent + all 6 agent definitions
│
├── instructions/
│   ├── validator_instructions.py     ← Agent 0
│   ├── analyzer_instructions.py      ← Agent 1
│   ├── refactorer_instructions.py    ← Agent 2
│   ├── test_agent_instructions.py    ← Agent 3
│   ├── build_recheck_instructions.py ← Agent 4
│   └── reporter_instructions.py      ← Agent 5
│
└── tools/
    ├── validation_tools.py           ← Agent 0: URL and token validation
    ├── sonar_tools.py                ← Agent 1 + 4: SonarCloud API
    ├── file_tools.py                 ← Agent 2: file read/write/context
    ├── git_tools.py                  ← Agent 2: clone, branch, commit, push
    ├── github_tools.py               ← Agent 2 + 5: GitHub REST API
    ├── test_tools.py                 ← Agent 3: build detection, test run, commit
    ├── build_tools.py                ← Agent 4: compile, test suite, diff stats
    └── report_tools.py               ← Agent 5: PR details, report generation
```

---

## 5. Example Execution Flow (Happy Path)

User provides:
```
github_url   = git@github.com:owner/Project.git
sonar_token  = <sonarcloud-token>
github_token = <github-pat>
```

**Agent 0** validates all three inputs. URL matches SSH pattern. Tokens pass format checks.

**Agent 1** calls SonarCloud. Finds project `owner_Project`. Fetches 47 open issues across 12 files.

**Agent 2** clones the repo into `workspace/Project/`. Creates branch `feature/sonar_fixes-20240705_113045`. Iterates through 47 issues: reads context, generates fix, writes file. 38 fixed, 9 skipped (file not found or fix not safe). Commits 7 modified files. Pushes branch. Opens PR #42.

**Agent 3** detects Maven + JUnit. For each of the 7 modified files, locates or derives the test file path. Reads source and existing tests. Generates new test methods for the fixed code paths. Runs `mvn test -q`. All 52 tests pass. Commits 5 updated test files to the same branch.

**Agent 4** runs `mvn compile -q`. Compiles cleanly. Runs `mvn test -q` again as final gate. 52/52 pass. Gets diff stats: 7 files, +143/-89 lines. Re-queries SonarCloud: 47 issues (reflects main branch — will update after CI on PR).

**Agent 5** reads all session state keys. Fetches PR #42 status from GitHub. Writes `workspace/reports/codesculptor_report_20240705_113045.md`. Output includes pipeline status table, per-fix BEFORE/AFTER diffs, test coverage summary, build results, PR link, and next steps.

---

## 6. Future Scope

- **Security Agent** — dedicated SAST pass using Semgrep or similar; focuses on OWASP Top 10 patterns beyond what SonarCloud flags
- **Documentation Agent** — generates or updates Javadoc/JSDoc/docstrings for all modified public APIs
- **Performance Agent** — detects N+1 loops, inefficient string concatenation, unnecessary DB calls in hot paths
- **Multi-repository mode** — accept a list of repos and run the pipeline in sequence or parallel
- **IDE Plugin** — trigger the pipeline directly from VS Code or IntelliJ without the ADK web interface
- **Scheduled runs** — periodic SonarCloud polling with automatic fix PRs on a configurable cadence
