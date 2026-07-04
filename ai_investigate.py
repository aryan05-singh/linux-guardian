"""
Optional AI root-cause-analysis layer on top of guardian's deterministic
checks and fixes.

When a check escalates because it has no safe deterministic fix (or its
fix didn't hold), this module hands an LLM a bundle of diagnostic evidence
and lets it *reason* about why — forming a hypothesis, optionally
requesting more evidence through a small allowlisted set of read-only
commands (an agentic tool-use loop), and returning a structured
root-cause report instead of a raw log dump.

This is intentionally kept separate from checks/builtin.py: an LLM should
never sit in the *fix* critical path (see the README's design principle —
fixes must stay deterministic and auditable). But root-cause reasoning
over evidence that's already been gathered is exactly the kind of
judgment call that's unsafe to hardcode as a rule, and exactly what LLMs
are good at. Guardian tells you *that* something is broken; this tells
you *why*.

Requires the `anthropic` package (pip install -r requirements-ai.txt) and
an ANTHROPIC_API_KEY. Entirely optional — the core tool needs neither.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# A tightly scoped allowlist of read-only diagnostic commands the model may
# request during investigation. Nothing here can mutate state — the model
# can look, but the fix path stays entirely deterministic and under
# guardian's/the human's control, never the LLM's.
ALLOWED_TOOL_COMMANDS = {
    "disk_usage": ["df", "-h"],
    "memory_usage": ["free", "-h"],
    "uptime": ["uptime"],
    "recent_git_log": ["git", "log", "--oneline", "-10"],
}

SYSTEM_PROMPT = """You are an incident-response assistant investigating a \
failing health check. You will be given the check's name, its failure \
detail, and some diagnostic evidence. Your job:

1. Form a root-cause hypothesis, reasoning from the evidence you have.
2. If you need more evidence to be confident, call the `run_diagnostic` \
tool with one of the allowed command names before concluding.
3. Report back in exactly this format:

Root cause: <your hypothesis>
Confidence: <high|medium|low>
Evidence: <what specifically supports this>
Recommended action: <what a human should do next>

If you're genuinely unsure, say so plainly in the root cause line rather \
than guessing with false confidence — an honest "inconclusive, here's \
what I ruled out" is more useful than a confident wrong answer."""


def _run_allowed_command(command_name: str) -> str:
    cmd = ALLOWED_TOOL_COMMANDS.get(command_name)
    if cmd is None:
        return f"<unknown diagnostic command: {command_name!r}, allowed: {list(ALLOWED_TOOL_COMMANDS)}>"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (result.stdout + result.stderr).strip() or "<no output>"
    except Exception as e:
        return f"<error running {command_name}: {e}>"


def gather_base_evidence(log_file: Path, window_lines: int = 100) -> dict:
    """Generic evidence any check might benefit from: the tail of guardian's
    own log around the time of failure. Check-specific evidence can be
    merged in by the caller before passing to investigate()."""
    if log_file.exists():
        with open(log_file, "r", errors="ignore") as f:
            recent_log_tail = "".join(f.readlines()[-window_lines:])
    else:
        recent_log_tail = "<log file not found>"
    return {"recent_log_tail": recent_log_tail}


def investigate(
    check_name: str,
    check_detail: str,
    evidence: dict,
    api_key: str,
    model: str = "claude-sonnet-5",
    max_tool_calls: int = 3,
) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    tools = [
        {
            "name": "run_diagnostic",
            "description": "Run one of a small set of read-only diagnostic commands to gather more evidence.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command_name": {"type": "string", "enum": list(ALLOWED_TOOL_COMMANDS.keys())},
                },
                "required": ["command_name"],
            },
        }
    ]

    messages: list = [
        {
            "role": "user",
            "content": (
                f"Check: {check_name}\nFailure detail: {check_detail}\n\n"
                f"Evidence gathered so far:\n{json.dumps(evidence, indent=2)}"
            ),
        }
    ]

    for _ in range(max_tool_calls + 1):
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if not tool_uses:
            text_blocks = [block.text for block in response.content if block.type == "text"]
            return "\n".join(text_blocks)

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tool_use in tool_uses:
            command_name = tool_use.input.get("command_name", "")
            output = _run_allowed_command(command_name)
            tool_results.append({"type": "tool_result", "tool_use_id": tool_use.id, "content": output})
        messages.append({"role": "user", "content": tool_results})

    return "Investigation inconclusive: exceeded max diagnostic tool calls without a final answer."
