"""
multi_agent_refactorer/agent.py
================================
Orchestration layer for the CodeSculptor multi-agent pipeline.

Architecture Decision — Why SequentialAgent?
---------------------------------------------
The pipeline has a strict data dependency chain:
  Agent 0 (Validator)  → validates all inputs        → output_key="validation-result"
  Agent 1 (Analyzer)   → fetches Sonar issues        → output_key="sonar-issue-summary"
  Agent 2 (Refactorer) → clones repo, applies fixes  → output_key="issue-fix-summary"
  Agent 3 (Test Agent) → writes & runs tests         → output_key="test-summary"

A SequentialAgent enforces this ordering and passes session state between
sub-agents automatically via ADK's built-in state mechanism. Each agent
writes its result to an output_key; every downstream agent can read all
prior output_keys from session state.

A ParallelAgent would be inappropriate here because each stage depends on
the structured output of the stage before it.

Retry Strategy — Why exp_base=7?
---------------------------------
Gemini 2.5 Pro is subject to rate limits (HTTP 429) under sustained load.
Exponential base 7 with initial_delay=1 produces wait times of:
  Attempt 1: 1s, Attempt 2: 7s, Attempt 3: 49s ...
This is intentionally spaced to avoid rapid retry storms that would further
exhaust API quota. HTTP 503/504 cover transient backend outages; 500 covers
unexpected server errors that may resolve on retry.
"""

from google.genai import types
from google.adk.models.google_llm import Gemini
from google.adk.agents import Agent, SequentialAgent

from multi_agent_refactorer.instructions.validator_instructions import validator_instructions
from multi_agent_refactorer.instructions.analyzer_instructions import analyzer_instructions
from multi_agent_refactorer.instructions.refactorer_instructions import refactoring_instructions
from multi_agent_refactorer.instructions.test_agent_instructions import test_agent_instructions

from multi_agent_refactorer.tools.validation_tools import validation_tools
from multi_agent_refactorer.tools.sonar_tools import sonar_issue_analyzer_tools
from multi_agent_refactorer.tools.file_tools import refactoring_tools
from multi_agent_refactorer.tools.git_tools import git_tools
from multi_agent_refactorer.tools.test_tools import test_agent_tools


# ---------------------------------------------------------------------------
# Shared retry configuration applied to all agents.
# See module docstring above for rationale.
# ---------------------------------------------------------------------------
retry_config = types.HttpRetryOptions(
    attempts=5,
    exp_base=7,
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504],
)

model = "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# Agent 0 — Input Validation Agent
# Runs first. Validates github_url, sonar_token, and github_token before
# any external API call or git operation is triggered. If any input fails
# validation, the pipeline stops here with a clear error.
# ---------------------------------------------------------------------------
validation_agent = Agent(
    name="input_validation_agent",
    model=Gemini(
        model=model,
        retry_options=retry_config,
    ),
    instruction=validator_instructions,
    tools=validation_tools,
    output_key="validation-result",
)


# ---------------------------------------------------------------------------
# Agent 1 — Sonar Issue Analyzer Agent
# Connects to SonarCloud, verifies the project is registered, fetches all
# open issues, and emits a structured issue list. Never touches the repo.
# ---------------------------------------------------------------------------
sonar_issue_analyzer_agent = Agent(
    name="sonar_issue_analyzer_agent",
    model=Gemini(
        model=model,
        retry_options=retry_config,
    ),
    instruction=analyzer_instructions,
    tools=sonar_issue_analyzer_tools,
    output_key="sonar-issue-summary",
)


# ---------------------------------------------------------------------------
# Agent 2 — Sonar Issue Fixer Agent
# Reads the issue list from session state, clones the repo, creates a
# feature branch, applies LLM-generated minimal fixes file by file, and
# commits the modified files back to the branch.
# ---------------------------------------------------------------------------
refactoring_agent = Agent(
    name="sonar_issue_fixer_agent",
    model=Gemini(
        model=model,
        retry_options=retry_config,
    ),
    instruction=refactoring_instructions,
    tools=refactoring_tools + git_tools,
    output_key="issue-fix-summary",
)


# ---------------------------------------------------------------------------
# Agent 3 — Test Agent
# Reads the list of modified files from issue-fix-summary, detects the
# build system and test framework, generates unit tests for each changed
# file, runs the suite, self-corrects once on failure, and commits only
# the verified-passing test files to the same branch Agent 2 created.
# ---------------------------------------------------------------------------
test_agent = Agent(
    name="test_agent",
    model=Gemini(
        model=model,
        retry_options=retry_config,
    ),
    instruction=test_agent_instructions,
    tools=test_agent_tools,
    output_key="test-summary",
)


# ---------------------------------------------------------------------------
# Root Orchestrator — SequentialAgent
# Runs sub_agents in declaration order. ADK passes session state between
# them automatically. The pipeline stops early if Agent 0 or Agent 1
# signals a failure via their output_key (controlled by each agent's
# instruction contract).
# ---------------------------------------------------------------------------
root_agent = SequentialAgent(
    name="multi_agent_refactorer",
    sub_agents=[
        validation_agent,
        sonar_issue_analyzer_agent,
        refactoring_agent,
        test_agent,
    ],
)
