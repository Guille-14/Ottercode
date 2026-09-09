from backend.environments import get_backend

def test_all_backends_construct():
    for name in ("local", "docker", "ssh", "singularity", "modal", "daytona", "vercel"):
        b = get_backend(name)
        assert hasattr(b, "exec")
