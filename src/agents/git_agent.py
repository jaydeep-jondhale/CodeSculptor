import os
import subprocess
import time
from pathlib import Path

class GitAgent:
    def __init__(self, repo_link_or_path: str, workdir: str | Path):
        self.repo_input = repo_link_or_path
        self.workdir = Path(workdir)
        self.repo_path = self.workdir / "repo"

    # Utility function to run shell commands safely
    def run_cmd(self, cmd, cwd=None):
        result = subprocess.run(cmd, cwd=cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
        return result.stdout

    # Clone or open a repo
    def clone_or_open_repo(self):
        self.workdir.mkdir(exist_ok=True)
        if str(self.repo_input).startswith("http"):
            # Remote URL to clone
            if not self.repo_path.exists():
                print("Cloning repository...")
                self.run_cmd(f"git clone {self.repo_input} repo", cwd=self.workdir)
        else:
            # Local path to copy reference
            local = Path(self.repo_input).resolve()
            if not local.exists():
                raise FileNotFoundError("Local repo path does not exist")
            self.repo_path = local

        print(f"Using repository at: {self.repo_path}")

    # Create a branch 
    def create_branch(self):
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.branch_name = f"agent-refactor/{timestamp}"

        # Switch to main/master
        try:
            self.run_cmd("git checkout main", cwd=self.repo_path)
        except RuntimeError:
            self.run_cmd("git checkout master", cwd=self.repo_path)

        # Only pull if remote 'origin' exists
        remotes = self.run_cmd("git remote", cwd=self.repo_path).strip()
        if "origin" in remotes:
            print("Pulling latest changes from remote...")
            self.run_cmd("git pull", cwd=self.repo_path)
        else:
            print("⚠️ No remote configured → skipping git pull")

        # Create branch
        self.run_cmd(f"git checkout -b {self.branch_name}", cwd=self.repo_path)
        print(f"Created branch: {self.branch_name}")
        return self.branch_name

    # Apply patches from other Agents
    def apply_patch(self, file_path: str, content: str):
        full_path = self.repo_path / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"[PATCH APPLIED] {file_path}")

    # (optional) Run tests
    def run_tests(self) -> bool:
        # Detecting Python tests
        if (self.repo_path / "pytest.ini").exists() or (self.repo_path / "tests").exists():
            print("Running pytest...")
            try:
                self.run_cmd("pytest", cwd=self.repo_path)
                return True
            except RuntimeError:
                return False

        # Adding more test runners later
        print("No tests configured.")
        return True

    #Commit + push
    def commit_and_push(self, message: str):
        self.run_cmd("git add .", cwd=self.repo_path)
        self.run_cmd(f'git commit -m "{message}"', cwd=self.repo_path)
        self.run_cmd(f"git push --set-upstream origin {self.branch_name}", cwd=self.repo_path)

        commit_hash = self.run_cmd("git rev-parse HEAD", cwd=self.repo_path).strip()
        print(f"Changes pushed → commit {commit_hash}")
        return commit_hash


# How Orchestrator Will Use This
# git_agent = GitAgent(repo_url_or_path)
# git_agent.clone_or_open_repo()

# branch = git_agent.create_branch()

# # patches come from refactor/performance/test writer agents
# git_agent.apply_patch("src/main.py", new_code)

# tests_ok = git_agent.run_tests()

# if tests_ok:
#     commit = git_agent.commit_and_push("chore: automated refactor")
# else:
#     print("Tests failed → changes not committed")

