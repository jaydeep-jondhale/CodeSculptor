
from google.genai import types
from google.adk.models.google_llm import Gemini

from google.adk.agents import Agent, SequentialAgent, LoopAgent

from Agent.instructions import git_instructions
from Agent.instructions import sonar_instructions
from Agent.instructions import fixer_instructions

from Agent.fixer_tools import fixer_tools
from Agent.git_tools import git_tools
from Agent.sonar_issue_analyzer_tools import sonar_issue_analyzer_tools

retry_config=types.HttpRetryOptions(
    attempts=5,  # Maximum retry attempts
    exp_base=7,  # Delay multiplier
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504], # Retry on these HTTP errors
)

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

sonar_agent = Agent(
    name="sonar_issue_analyzer_agent",
    model=Gemini(
        model="gemini-2.5-flash-lite",
        retry_options=retry_config
    ),
    instruction=sonar_instructions,
    tools=sonar_issue_analyzer_tools,
    output_key="sonar-issue-summary",
)

fixer_agent = Agent(
    name="sonar_issue_fixer_agent",
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