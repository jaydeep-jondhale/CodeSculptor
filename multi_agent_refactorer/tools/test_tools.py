import os
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# detect_build_system
# ---------------------------------------------------------------------------

def detect_build_system(repo_path: str) -> dict:
    """
    Inspect the repository root to identify the build system and test framework.

    AI-Agent Contract:
    ------------------
    This function NEVER raises. It always returns:
        {
            "success":          True / False,
            "build_system":     "maven" | "gradle" | "npm" | "python" | "unknown",
            "test_framework":   "junit" | "testng" | "jest" | "mocha" | "pytest" | "unknown",
            "test_command":     "<command to run tests>" or None,
            "src_root":         "<relative source root>" or None,
            "test_root":        "<relative test root>" or None,
            "error":            None or "<reason>"
        }

    Detection order (first match wins):
        pom.xml          → Maven  + JUnit  (src/main/java / src/test/java)
        build.gradle     → Gradle + JUnit  (src/main/java / src/test/java)
        package.json     → npm    + Jest   (src / __tests__ or *.test.js)
        requirements.txt → Python + pytest (. / tests)
        setup.py         → Python + pytest (. / tests)
        pyproject.toml   → Python + pytest (. / tests)

    Why this matters:
        Agent 3 needs to know where source files live, where test files
        live, and which command to execute to run the suite. Without this,
        test file discovery and test execution are impossible.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {
            "success": False,
            "build_system": "unknown",
            "test_framework": "unknown",
            "test_command": None,
            "src_root": None,
            "test_root": None,
            "error": f"repo_path does not exist or is not a directory: {repo_path}",
        }

    root = Path(repo_path)

    # --- Maven ---
    if (root / "pom.xml").exists():
        return {
            "success": True,
            "build_system": "maven",
            "test_framework": "junit",
            "test_command": "mvn test -q",
            "src_root": "src/main/java",
            "test_root": "src/test/java",
            "error": None,
        }

    # --- Gradle ---
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return {
            "success": True,
            "build_system": "gradle",
            "test_framework": "junit",
            "test_command": "gradle test --quiet",
            "src_root": "src/main/java",
            "test_root": "src/test/java",
            "error": None,
        }

    # --- npm / Node.js ---
    if (root / "package.json").exists():
        # Determine test runner from package.json scripts if possible
        test_framework = "jest"
        try:
            import json
            with open(root / "package.json", "r", encoding="utf-8") as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            test_script = scripts.get("test", "")
            if "mocha" in test_script.lower():
                test_framework = "mocha"
        except Exception:
            pass  # default to jest if parse fails

        return {
            "success": True,
            "build_system": "npm",
            "test_framework": test_framework,
            "test_command": "npm test",
            "src_root": "src",
            "test_root": "__tests__",
            "error": None,
        }

    # --- Python ---
    python_markers = ["requirements.txt", "setup.py", "pyproject.toml", "setup.cfg"]
    if any((root / m).exists() for m in python_markers):
        return {
            "success": True,
            "build_system": "python",
            "test_framework": "pytest",
            "test_command": "pytest --tb=short -q",
            "src_root": ".",
            "test_root": "tests",
            "error": None,
        }

    # --- Unknown ---
    return {
        "success": True,
        "build_system": "unknown",
        "test_framework": "unknown",
        "test_command": None,
        "src_root": None,
        "test_root": None,
        "error": None,
    }


# ---------------------------------------------------------------------------
# find_existing_test_file
# ---------------------------------------------------------------------------

def find_existing_test_file(
    repo_path: str,
    source_file: str,
    build_system: str,
    test_root: str,
) -> dict:
    """
    Locate the test file that corresponds to a given source file.

    AI-Agent Contract:
    ------------------
    Always returns:
        {
            "success":        True / False,
            "exists":         True / False,
            "test_file_path": "<absolute path>" or None,
            "expected_path":  "<absolute path where test file should be created>",
            "error":          None or "<reason>"
        }

    Convention mappings by build system:

    Maven / Gradle (Java):
        src/main/java/com/app/UserService.java
        → src/test/java/com/app/UserServiceTest.java

    npm (JavaScript / TypeScript):
        src/components/Button.jsx
        → __tests__/Button.test.jsx   (checked first)
        → src/components/Button.test.jsx  (fallback)

    Python:
        src/services/user_service.py
        → tests/test_user_service.py

    The agent uses:
    - test_file_path when exists=True   (to read and extend existing tests)
    - expected_path  when exists=False  (to know where to create a new test file)
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {
            "success": False,
            "exists": False,
            "test_file_path": None,
            "expected_path": None,
            "error": f"repo_path does not exist: {repo_path}",
        }

    if not source_file:
        return {
            "success": False,
            "exists": False,
            "test_file_path": None,
            "expected_path": None,
            "error": "source_file is required.",
        }

    root = Path(repo_path)
    src = Path(source_file)          # relative path like src/main/java/com/app/UserService.java
    stem = src.stem                  # e.g. UserService
    suffix = src.suffix              # e.g. .java

    try:
        if build_system in ("maven", "gradle"):
            # Mirror the package path from src/main/java → src/test/java
            # e.g. com/app/UserService.java → com/app/UserServiceTest.java
            try:
                # Strip the src_root prefix to get the package-relative path
                relative = src.relative_to("src/main/java")
                package_dir = relative.parent
            except ValueError:
                # source_file may already be relative to repo root without src/main prefix
                package_dir = src.parent

            expected_rel = Path(test_root) / package_dir / f"{stem}Test{suffix}"
            expected_abs = root / expected_rel

            if expected_abs.exists():
                return {
                    "success": True,
                    "exists": True,
                    "test_file_path": str(expected_abs),
                    "expected_path": str(expected_abs),
                    "error": None,
                }
            return {
                "success": True,
                "exists": False,
                "test_file_path": None,
                "expected_path": str(expected_abs),
                "error": None,
            }

        elif build_system == "npm":
            # Check two conventional locations for JS/TS tests
            candidates = [
                root / "__tests__" / f"{stem}.test{suffix}",
                root / "__tests__" / f"{stem}.spec{suffix}",
                src.parent / f"{stem}.test{suffix}",     # co-located
                src.parent / f"{stem}.spec{suffix}",
            ]
            for candidate in candidates:
                full = root / candidate if not candidate.is_absolute() else candidate
                if full.exists():
                    return {
                        "success": True,
                        "exists": True,
                        "test_file_path": str(full),
                        "expected_path": str(full),
                        "error": None,
                    }
            # Default creation location
            expected_abs = root / "__tests__" / f"{stem}.test{suffix}"
            return {
                "success": True,
                "exists": False,
                "test_file_path": None,
                "expected_path": str(expected_abs),
                "error": None,
            }

        elif build_system == "python":
            # tests/test_<module>.py convention
            module_name = stem  # e.g. user_service
            expected_abs = root / test_root / f"test_{module_name}.py"
            if expected_abs.exists():
                return {
                    "success": True,
                    "exists": True,
                    "test_file_path": str(expected_abs),
                    "expected_path": str(expected_abs),
                    "error": None,
                }
            return {
                "success": True,
                "exists": False,
                "test_file_path": None,
                "expected_path": str(expected_abs),
                "error": None,
            }

        else:
            return {
                "success": False,
                "exists": False,
                "test_file_path": None,
                "expected_path": None,
                "error": f"Unsupported build_system: '{build_system}'. Cannot determine test file convention.",
            }

    except Exception as e:
        return {
            "success": False,
            "exists": False,
            "test_file_path": None,
            "expected_path": None,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# read_file  (lightweight wrapper — same contract as file_tools.read_file
#             but owned by the test agent to keep toolsets independent)
# ---------------------------------------------------------------------------

def read_file_for_tests(file_path: str) -> dict:
    """
    Read a file and return its full content.

    AI-Agent Contract:
    ------------------
    Always returns:
        { "success": True/False, "content": "<text>" or None, "error": None or "<reason>" }

    Used by Agent 3 to:
    - Read the modified source file to understand what to test.
    - Read the existing test file to understand current coverage.

    This is a separate function from file_tools.read_file so that Agent 3's
    tool list remains self-contained and does not inherit Agent 2's full
    refactoring toolset.
    """
    if not file_path or not os.path.exists(file_path):
        return {
            "success": False,
            "content": None,
            "error": f"File not found: {file_path}",
        }
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"success": True, "content": content, "error": None}
    except Exception as e:
        return {"success": False, "content": None, "error": str(e)}


# ---------------------------------------------------------------------------
# write_test_file
# ---------------------------------------------------------------------------

def write_test_file(file_path: str, content: str) -> dict:
    """
    Write generated or updated test content to a file.

    AI-Agent Contract:
    ------------------
    Always returns:
        { "success": True/False, "error": None or "<reason>" }

    Creates parent directories automatically if they do not exist.
    This is required because test directories (e.g. src/test/java/com/app/)
    may not exist if no tests existed before for a given package.

    Separate from file_tools.write_file to keep Agent 3's toolset
    self-contained and clearly scoped to test file operations.
    """
    if not file_path:
        return {"success": False, "error": "file_path is required."}
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------

def run_tests(repo_path: str, test_command: str, timeout_seconds: int = 120) -> dict:
    """
    Execute the project's test suite and capture the results.

    AI-Agent Contract:
    ------------------
    Always returns:
        {
            "success":       True / False,   # True = exit code 0 (all tests passed)
            "exit_code":     <int>,
            "output":        "<stdout — last 3000 chars>",
            "error_output":  "<stderr — last 2000 chars>",
            "timed_out":     True / False,
            "error":         None or "<reason if tool itself failed>"
        }

    Why truncate output?
        Test runners can produce thousands of lines. The tail of the output
        contains the summary line (e.g. "Tests run: 47, Failures: 2, Errors: 0")
        which is what the agent needs to reason about. Truncating to the last
        N characters keeps token usage manageable without losing the signal.

    Why is this a tool (not done internally by the LLM)?
        Running a subprocess is a deterministic, side-effectful operation.
        Delegating it to a tool gives the agent a reliable, structured result
        rather than asking it to simulate or guess test outcomes.

    Behaviour on timeout:
        Returns success=False, timed_out=True. The agent should record
        the timeout and continue rather than failing the entire pipeline.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {
            "success": False,
            "exit_code": -1,
            "output": "",
            "error_output": "",
            "timed_out": False,
            "error": f"repo_path does not exist or is not a directory: {repo_path}",
        }

    if not test_command:
        return {
            "success": False,
            "exit_code": -1,
            "output": "",
            "error_output": "",
            "timed_out": False,
            "error": "test_command is required.",
        }

    try:
        result = subprocess.run(
            test_command,
            cwd=repo_path,
            shell=True,                  # allow composite commands like "npm test"
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        # Truncate to tail — the summary line is always at the end
        stdout_tail = result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout
        stderr_tail = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr

        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "output": stdout_tail,
            "error_output": stderr_tail,
            "timed_out": False,
            "error": None,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "output": "",
            "error_output": "",
            "timed_out": True,
            "error": f"Test command timed out after {timeout_seconds} seconds.",
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "output": "",
            "error_output": "",
            "timed_out": False,
            "error": f"Unexpected error running tests: {e}",
        }


# ---------------------------------------------------------------------------
# commit_test_files
# ---------------------------------------------------------------------------

def commit_test_files(repo_path: str, test_files: str, branch_name: str) -> dict:
    """
    Stage and commit test files to the existing fix branch.

    AI-Agent Contract:
    ------------------
    Inputs:
        repo_path  (str): absolute path to the cloned repo (from issue-fix-summary)
        test_files (str): comma-separated paths of test files, relative to repo root
        branch_name(str): the fix branch created by Agent 2 (from issue-fix-summary)

    Always returns:
        {
            "success":      True / False,
            "committed":    <int>,   # number of files successfully staged and committed
            "error":        None or "<reason>"
        }

    Design decisions:
    - Commits to the SAME branch created by Agent 2 so that fix code and
      its tests live in a single coherent commit history and PR.
    - Stages only the listed test files (not git add -A) to prevent
      accidentally committing build artifacts or temp files.
    - Does NOT push — Agent 2 already pushed and set the upstream. A plain
      git push on the same branch will work from here.
    - Uses a fixed commit message format for traceability.
    """
    if not repo_path or not os.path.isdir(repo_path):
        return {
            "success": False,
            "committed": 0,
            "error": f"repo_path does not exist or is not a directory: {repo_path}",
        }

    if not test_files or not test_files.strip():
        return {
            "success": False,
            "committed": 0,
            "error": "test_files is required and must be a non-empty comma-separated string.",
        }

    if not branch_name or not branch_name.strip():
        return {
            "success": False,
            "committed": 0,
            "error": "branch_name is required.",
        }

    files = [f.strip() for f in test_files.split(",") if f.strip()]
    if not files:
        return {
            "success": False,
            "committed": 0,
            "error": "No valid file paths found after parsing test_files.",
        }

    try:
        # Stage each test file individually
        for f in files:
            subprocess.run(
                ["git", "add", f],
                cwd=repo_path,
                check=True,
                timeout=30,
            )

        # Commit
        commit_message = "test: Add/update unit tests for automated Sonar fixes"
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_path,
            check=True,
            timeout=60,
        )

        # Push to the already-tracked upstream branch
        subprocess.run(
            ["git", "push"],
            cwd=repo_path,
            check=True,
            timeout=60,
        )

        return {
            "success": True,
            "committed": len(files),
            "error": None,
        }

    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "committed": 0,
            "error": f"Git operation failed: {str(e)}",
        }
    except Exception as e:
        return {
            "success": False,
            "committed": 0,
            "error": f"Unexpected error during commit: {str(e)}",
        }


# ---------------------------------------------------------------------------
# Tool list exported for use in agent.py
# ---------------------------------------------------------------------------
test_agent_tools = [
    detect_build_system,
    find_existing_test_file,
    read_file_for_tests,
    write_test_file,
    run_tests,
    commit_test_files,
]
