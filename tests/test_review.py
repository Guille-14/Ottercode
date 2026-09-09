from backend.review import enqueue_review, gpu_idle, queue_size

def test_enqueue_does_not_crash():
    class R:
        task_id = "t"
        task_text = "hola"
        transcript = []
    n = queue_size()
    enqueue_review(R())
    assert queue_size() >= n
    assert gpu_idle() in (True, False)
