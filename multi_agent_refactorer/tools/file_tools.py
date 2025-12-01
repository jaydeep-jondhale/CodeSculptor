import os


def read_file(file_path: str) -> dict:
    """
    Read a file and return its content.

    AI-Agent Contract:
    ------------------
    Always return structured:
        { success, content, error }

    Used for:
    - Reading code that contains the Sonar issue
    """
    if not os.path.exists(file_path):
        return {
            "success": False,
            "content": None,
            "error": f"File not found: {file_path}"
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"success": True, "content": content, "error": None}

    except Exception as e:
        return {"success": False, "content": None, "error": str(e)}



def write_file(file_path: str, content: str) -> dict:
    """
    Writes content back to a file.

    AI-Agent Contract:
    ------------------
    Always return structured response.

    Used for:
    - Writing AI-fixed code back into repository
    """
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return {"success": True, "error": None}

    except Exception as e:
        return {"success": False, "error": str(e)}



def extract_code_context(file_path: str, line_number: int, window: int = 5) -> dict:
    """
    Extract a small window of lines around the issue.

    Why?
    ----
    LLM needs surrounding context to fix the issue safely.

    Returns:
        {
            success: True/False,
            context: "string block",
            start_line: int,
            error: str or None
        }
    """

    if not os.path.exists(file_path):
        return {
            "success": False,
            "context": None,
            "error": f"File not found: {file_path}",
            "start_line": None
        }

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = line_number - 1
        start = max(idx - window, 0)
        end = min(idx + window + 1, len(lines))

        snippet = "".join(lines[start:end])

        return {
            "success": True,
            "context": snippet,
            "start_line": start + 1,
            "error": None
        }

    except Exception as e:
        return {
            "success": False,
            "context": None,
            "error": str(e),
            "start_line": None
        }


refactoring_tools = [
    read_file,
    write_file,
    extract_code_context
]