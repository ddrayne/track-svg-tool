from __future__ import annotations

import os
import subprocess
import tempfile


def _select_llm_cmd() -> str | None:
    return (
        os.environ.get("TRACKFACTORY_LLM_CMD")
        or os.environ.get("TRACKFACTORY_LLM_CMD_CLAUDE")
        or os.environ.get("TRACKFACTORY_LLM_CMD_CODEX")
    )


def run_llm(prompt: str) -> str | None:
    cmd = _select_llm_cmd()
    if not cmd:
        return None
    if "{prompt_file}" in cmd:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write(prompt)
            prompt_path = handle.name
        try:
            command = cmd.replace("{prompt_file}", prompt_path)
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
        finally:
            os.unlink(prompt_path)
    else:
        result = subprocess.run(cmd, shell=True, input=prompt, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None
