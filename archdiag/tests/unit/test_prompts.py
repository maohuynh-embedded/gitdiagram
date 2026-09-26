from archdiag.context.select import Evidence, Excerpt
from archdiag.llm.prompts import build_messages, retry_message


def evidence(truncated: bool = False) -> Evidence:
    return Evidence(
        tree_summary="src/ (1 file: 1 c)\n  main.c",
        excerpts=(Excerpt("src/main.c", "int main(void) { return 0; }"),),
        budget_used=100,
        budget_limit=20000,
        truncated=truncated,
        candidates_total=4,
    )


def test_messages_contain_tree_excerpts_and_language() -> None:
    system, user = build_messages("mini", evidence(), "Vietnamese")
    assert system["role"] == "system"
    assert "Vietnamese prose" in system["content"]
    assert "{language}" not in system["content"]
    assert user["role"] == "user"
    assert "Repository: mini" in user["content"]
    assert "src/ (1 file: 1 c)" in user["content"]
    assert "<<<FILE src/main.c>>>\nint main(void) { return 0; }\n<<<END FILE>>>" in user["content"]
    assert "Excerpts cover 1 of 4 candidate files." in user["content"]
    assert "truncated" not in user["content"]


def test_truncation_is_announced() -> None:
    _, user = build_messages("mini", evidence(truncated=True), "English")
    assert "The context was truncated" in user["content"]


def test_retry_message_lists_errors() -> None:
    message = retry_message(("bad id 'X'", "edge to ghost"))
    assert message["role"] == "user"
    assert "- bad id 'X'\n- edge to ghost" in message["content"]
    assert "complete corrected JSON" in message["content"]
