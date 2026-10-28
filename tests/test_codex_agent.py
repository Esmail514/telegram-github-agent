from app.agents.codex import build_codex_cli_command


def test_codex_cli_command_uses_selected_model_and_reasoning_effort():
    command = build_codex_cli_command("codex", "gpt-5.6-terra", "high")

    assert command == [
        "codex",
        "exec",
        "--json",
        "--full-auto",
        "--model",
        "gpt-5.6-terra",
        "-c",
        'model_reasoning_effort="high"',
        "-",
    ]
