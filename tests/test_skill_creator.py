from backend.skill_creator import maybe_create_from_run, write_skill, delete_skill, list_home_skills

def test_write_and_list():
    r = write_skill("unit_test_skill", "desc", "body", tools_used=["read_file"])
    assert r["ok"]
    names = [s["name"] for s in list_home_skills()]
    assert "unit_test_skill" in names
    delete_skill("unit_test_skill", archive=False)

def test_maybe_create_needs_three_tools():
    class R:
        task_text = "implementar algo largo suficiente"
        transcript = [{"kind": "tool", "tool": "read_file", "ok": True}]
    assert maybe_create_from_run(R()) is None
