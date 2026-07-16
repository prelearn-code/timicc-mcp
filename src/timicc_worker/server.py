from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .core import DEFAULT_MODEL, generate_patch as generate_patch_core
from .jobs import get_job_manager


INSTRUCTIONS = """This server sends selected code context to TIMI CC and returns an untrusted candidate patch. It never edits files. Inspect the repository first, provide minimum necessary context and exact allowed_paths, and never send secrets, credentials, customer data, .env files, or unrelated proprietary content. Review the entire patch and run local validation before accepting it."""

mcp = FastMCP("TIMI CC GPT Patch Worker", instructions=INSTRUCTIONS, json_response=True)


@mcp.tool()
def generate_patch(
    task: str,
    file_context: str,
    allowed_paths: list[str],
    constraints: str = "",
    test_failures: str = "",
    model: str = DEFAULT_MODEL,
) -> dict:
    """Generate a validated candidate unified diff without editing the repository."""
    return generate_patch_core(
        task=task,
        file_context=file_context,
        allowed_paths=allowed_paths,
        constraints=constraints,
        test_failures=test_failures,
        model=model,
    )


@mcp.tool()
def review_patch(
    task: str,
    file_context: str,
    candidate_patch: str,
    allowed_paths: list[str],
    constraints: str = "",
    test_results: str = "",
    model: str = DEFAULT_MODEL,
) -> dict:
    """Review an untrusted candidate patch and return a corrected replacement diff, or an empty patch when no correction is justified."""
    review_task = f"""Act as a strict code reviewer. Review the candidate patch against the task and current files.
Identify correctness, security, compatibility, and test problems. Return a complete corrected replacement patch only when changes are needed; otherwise return an empty patch. State the verdict and findings in summary, assumptions, and risks.

ORIGINAL TASK
{task}

CANDIDATE PATCH
{candidate_patch}
"""
    result = generate_patch_core(
        task=review_task,
        file_context=file_context,
        allowed_paths=allowed_paths,
        constraints=constraints,
        test_failures=test_results,
        model=model,
    )
    result["mode"] = "review"
    return result


@mcp.tool()
def submit_patch_job(
    task: str,
    file_context: str,
    allowed_paths: list[str],
    task_name: str = "",
    constraints: str = "",
    test_failures: str = "",
    model: str = DEFAULT_MODEL,
) -> dict:
    """Submit a long-running patch generation job and return immediately."""
    return get_job_manager().submit(
        task=task,
        file_context=file_context,
        allowed_paths=allowed_paths,
        task_name=task_name,
        constraints=constraints,
        test_failures=test_failures,
        model=model,
    )


@mcp.tool()
def get_patch_job(job_id: str) -> dict:
    """Get the current status of a background patch job."""
    return get_job_manager().status(job_id)


@mcp.tool()
def get_patch_result(job_id: str) -> dict:
    """Return a completed validated patch job result, or its current status."""
    return get_job_manager().result(job_id)


@mcp.tool()
def cancel_patch_job(job_id: str) -> dict:
    """Request cancellation of a background patch job."""
    return get_job_manager().cancel(job_id)


@mcp.tool()
def get_capabilities() -> dict:
    """Return local worker capabilities without calling the external model."""
    return {
        "worker": "timicc",
        "version": "0.2.0",
        "tools": [
            "generate_patch",
            "review_patch",
            "submit_patch_job",
            "get_patch_job",
            "get_patch_result",
            "cancel_patch_job",
            "get_capabilities",
        ],
        "models": ["gpt-5.6-sol", "gpt-5.5", "gpt-5.4"],
        "writes_files": False,
        "returns": ["patch", "changed_files", "patch_sha256", "tests", "risks", "usage"],
        "display_contract": "Worker returns an untrusted diff; Codex reviews and applies it for native file-change display.",
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
