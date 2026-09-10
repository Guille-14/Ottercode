"""T5: completed exige evidencia."""


def apply_status(status: str, evidence: str) -> str:
    status = status.lower().strip()
    if status in ("done", "complete", "completed", "completado", "x"):
        status = "completed"
    if status == "completed" and len((evidence or "").strip()) < 8:
        return "in_progress"
    return status


def test_completed_needs_evidence():
    assert apply_status("completed", "") == "in_progress"
    assert apply_status("completed", "pytest test_app.py OK") == "completed"
    assert apply_status("pending", "") == "pending"


if __name__ == "__main__":
    test_completed_needs_evidence()
    print("T5 OK")
