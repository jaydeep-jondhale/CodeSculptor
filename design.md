# Multi-Agent Codebase Refactorer – design.md

## 1. Overview

**Goal:**  
Build a multi-agent system that takes a code repository as input and **continuously improves** it by:

- Scanning and understanding the codebase
- Detecting bugs and code smells
- Refactoring code safely
- Writing / updating tests
- Suggesting and (optionally) applying performance optimisations
- Managing git workflow (pull, branch, commit, PR)

The system should be **modular** so new agents can be plugged in later (e.g., Security Agent, Documentation Agent, Infra Agent).

---

## 2. Primary User Flows

### 2.1 One-shot Improvement Run

1. User selects repo (local path or remote URL).
2. Git Agent:
    - Clones repo (if remote).
    - Creates a new branch (e.g., `refactor/<timestamp>`).
3. Code Scanner Agent:
    - Builds a lightweight **codebase map** (modules, files, dependencies).
    - Detects language(s) and frameworks.
4. Bug Detection Agent:
    - Runs static analysis + LLM-based review.
    - Produces a **Bug Report** (issues with severity, location, suggestion).
5. Refactor Agent:
    - Picks a subset of issues (or goals like “clean architecture”, “reduce duplication”).
    - Produces refactored patches (diffs).
6. Test Writer Agent:
    - Writes/updates unit tests for changed files and critical modules.
    - Ensures tests compile and can run.
7. Performance Agent:
    - Looks for obvious performance hotspots & anti-patterns.
    - Suggests or creates optimised versions (still readable & safe).
8. Git Agent:
    - Runs tests (if configured).
    - Stages changes, commits with meaningful messages, optionally opens PR.

Output to user:
- Summary of changes
- Bug report + refactor summary
- Test coverage changes (basic)
- Performance suggestions applied/not applied

---

## 3. Agents (Current Scope)

We include **6 core agents now**:

1. Code Scanner Agent
2. Bug Detection Agent
3. Refactor Agent
4. Test Writer Agent
5. Performance Agent
6. Git Agent

Future agents will be mentioned later as **Future Scope**.

### 3.1 Code Scanner Agent

**Role:** Build an internal model of the repo.

**Responsibilities:**
- Detect languages & frameworks (e.g., Java/Spring, Node/Express).
- Parse project structure:
    - Source folders, test folders
    - Build files (Maven/Gradle/package.json/etc.)
- Build a **code map**:
    - Per-file: path, LOC, main classes/functions
    - Per-module: dependencies between modules
- Provide **APIs for other agents**:
    - `list_files(filter)`, `get_file_contents(path)`
    - `find_references(symbol)`, `get_module_dependencies(module)`

**Inputs:**
- Repo path
- Optional: config file (e.g. `.agent-config.yml`) for ignore patterns

**Outputs:**
- Codebase metadata JSON (used by all other agents)
- Index / embeddings store (if using vector DB)

---

### 3.2 Bug Detection Agent

**Role:** Find bugs, code smells, risky patterns.

**Responsibilities:**
- Run static analyzers (if available): e.g., ESLint, PMD, Checkstyle, etc.
- LLM-based analysis:
    - Scan high-risk files (complex, long, high fan-in/fan-out).
    - Detect:
        - Null-pointer risks
        - Wrong error handling
        - Race conditions (basic patterns)
        - Misuse of APIs
        - Security-ish smells (optional/minimal in first version)
- Produce a **Bug Report**:
    - Each issue: `severity`, `file`, `lineRange`, `description`, `proposedFix`.

**Inputs:**
- Codebase map from Scanner
- Raw file contents

**Outputs:**
- `bug_report.json`:
  ```json
  {
    "issues": [
      {
        "id": "BUG-001",
        "severity": "HIGH",
        "file": "src/main/java/com/app/UserService.java",
        "line_start": 42,
        "line_end": 58,
        "description": "Possible NPE when user is null.",
        "proposed_fix_summary": "Check for null before accessing user.getId()."
      }
    ]
  }
  ```

### 3.3 Refactor Agent

**Role:** Apply safe and meaningful refactors.

**Responsibilities:**
- Prioritise issues from Bug Detection Agent:
  - Higher severity first.
  - Optionally limit number of files per run.
- Refactoring types (MVP):
  - Extract method for long functions
  - Remove duplicated code
  - Improve naming (methods, variables)
  - Add null checks / guards for risky code
  - Simplify complex conditions
- Work incrementally:
  - Use patches/diffs instead of rewriting whole files.
- Ensure compilability:
  - Keep imports, method signatures and public APIs stable unless explicitly allowed.

**Inputs:**
- bug_report.json
- Codebase map + file contents

**Outputs:**
- A set of patches (e.g., diff objects):
```json
{
  "file": "src/main/java/com/app/UserService.java",
  "patch": "@@ -40,6 +40,10 @@ ...",
  "related_issues": ["BUG-001"]
}
```

### 3.4 Test Writer Agent

**Role:** Improve automated test coverage.

**Responsibilities:**
- Detect test framework from project (JUnit, Jest, etc.).
- For changed files:
  - Check existing tests.
  - Generate new or update existing tests for main public methods.
  - Ensure tests compile (basic sanity call to test runner).
  - Label tests as generated by tool in comments.

**Inputs:**
- Code changes (patches)
- Codebase map (where tests live, how to run them)

**Outputs:**
- New/updated test files
- Optional test_plan.json:
```json
{
  "tests_added": [
    {
      "file": "src/test/java/com/app/UserServiceTest.java",
      "covers": ["UserService.createUser", "UserService.getUser"]
    }
  ]
}
```

### 3.5 Performance Agent

**Role:** Spot obvious performance issues and suggest fixes.

**Responsibilities (MVP):**
- Pattern-based detection:
  - N+1 loops (nested loops on large collections).
  - Inefficient string concatenation inside loops.
  - Unnecessary database calls in hot paths (very basic heuristics).
- Propose small, low-risk optimisations:
  - Use StringBuilder or equivalent.
  - Cache computed values when safe.
  - Replace O(n²) loops with more efficient approaches when clear.
- Annotate changes clearly in diff comments.

**Inputs:**
- Codebase map
- Hotspot hints (optional, future integration with profiling)

**Outputs:**
- Performance suggestions + patches
- performance_report.json with rationale

### 3.6 Git Agent

**Role:** Handle git operations safely and consistently.

**Responsibilities:**
- Clone or update repo:
  - git clone if path is remote.
  - git pull on default branch before changes.
- Branch management:
  - Creates branch, e.g. agent-refactor/<date>.
  - Optionally: one branch per run.
- Apply patches from other agents.
- Run tests (optional, based on project config):
  - If tests fail → mark run as partially failed, no commit unless user allows.
- Commit and push:
  - Commit messages summarising changes:
    - chore: refactor UserService & add tests
  - Push branch to remote.
  - Optionally open Pull Request (later, via GitHub/GitLab API).

**Inputs:**
- Repo URL or local path
- User git token/config
- Patches from Refactor, Test Writer, Performance agents

**Outputs:**
- Git branch name
- Commit hash(es)
- Optional: PR URL

## 4. System Architecture

### 4.1 High-Level Components

**Orchestrator Service**

Central brain.

Manages workflow steps, agent calls, and data passing.

Implements “pipelines”:

scan → detect_bugs → refactor → tests → perf → git

**Agents Layer**

Each agent is a separate module/service with a clear interface:

Input schema (JSON)

Output schema (JSON)

Agents can be local services, containers, or functions.

**Storage Layer**

Repo working directory

Metadata (code maps, reports, run history) in a DB or local files (MVP).

Optional: vector DB for semantic code search.

**Interface Layer**

CLI initially (easiest).

Later: simple web UI or IDE plugin.

### 4.2 Orchestration Options (MVP)

Simple approach: Orchestrator is a single process that:

Calls each agent sequentially.

Passes JSON via function calls / local modules.

Later: Convert to message-based (e.g., queue) for concurrency.

## 5. Example Execution Flow (Happy Path)

User runs:

agent-refactor --repo https://github.com/user/project.git


Orchestrator:

Asks Git Agent to clone and create branch.

Code Scanner Agent:

Produces code_map.json.

Bug Detection Agent:

Uses code_map.json to scan and emits bug_report.json.

Refactor Agent:

Reads bug_report.json and code, produces patch set A.

Test Writer Agent:

Reads patch set A + code map, produces patch set B (tests).

Performance Agent:

Reads code and produces patch set C (perf).

Git Agent:

Applies patches A + B + C.

Runs mvn test / npm test / etc.

If tests pass:

Commits and pushes.

If tests fail:

Keeps changes locally and reports failure reason.

Orchestrator:

Generates final run report for the user.
