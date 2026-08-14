from app.services.error_sanitizer import sanitize_error_message


def test_sanitize_error_message_redacts_sensitive_values() -> None:
    raw = (
        "Authorization: Bearer super-secret-token "
        "api_key=sk-dify-live-secret "
        "password=hunter2 "
        "Traceback (most recent call last):\n"
        '  File "E:\\\\AI-Recruitment\\\\backend\\\\app\\\\'
        'workers\\\\ai_tasks.py", line 12\n'
        "resume_text=COMPLETE_RESUME_BODY_SHOULD_NOT_LEAK "
        + ("x" * 5000)
    )
    summary = sanitize_error_message(raw)
    assert summary is not None
    assert "super-secret-token" not in summary
    assert "sk-dify-live-secret" not in summary
    assert "hunter2" not in summary
    assert "COMPLETE_RESUME_BODY_SHOULD_NOT_LEAK" not in summary
    assert "Traceback (most recent call last)" not in summary
    assert "E:\\AI-Recruitment" not in summary
    assert "e:\\AI-Recruitment" not in summary.lower()
    assert len(summary) <= 280
