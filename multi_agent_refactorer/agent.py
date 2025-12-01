
from google.genai import types
from google.adk.models.google_llm import Gemini
from google.adk.agents import Agent, SequentialAgent

from multi_agent_refactorer.instructions.analyzer_instructions import analyzer_instructions
from multi_agent_refactorer.tools.file_tools import refactoring_tools
from multi_agent_refactorer.tools.git_tools import git_tools
from multi_agent_refactorer.instructions.refactorer_instructions import refactoring_instructions
from multi_agent_refactorer.tools.sonar_tools import sonar_issue_analyzer_tools

retry_config=types.HttpRetryOptions(
    attempts=5,  # Maximum retry attempts
    exp_base=7,  # Delay multiplier
    initial_delay=1,
    http_status_codes=[429, 500, 503, 504], # Retry on these HTTP errors
)

model = "gemini-2.5-pro"

sonar_issue_analyzer_agent = Agent(
    name="sonar_issue_analyzer_agent",
    model=Gemini(
        model=model,
        retry_options=retry_config
    ),
    instruction=analyzer_instructions,
    tools=sonar_issue_analyzer_tools,
    output_key="sonar-issue-summary",
)

refactoring_agent = Agent(
    name="sonar_issue_fixer_agent",
    model=Gemini(
        model=model,
        retry_options=retry_config
    ),
    instruction=refactoring_instructions,
    tools=refactoring_tools + git_tools,
    output_key="issue-fix-summary"
)

# Workflow -- analyzer agent then refactoring agent!!
root_agent = SequentialAgent(
    name="multi_agent_refactorer",
    sub_agents=[sonar_issue_analyzer_agent,refactoring_agent],
)




