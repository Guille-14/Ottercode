from backend.db import session_search

def test_session_search_empty_query():
    assert session_search("") == []
