import inspect

from app.api.v1.endpoints import jobs


def test_jobs_router_prefix() -> None:
    assert jobs.router.prefix == "/jobs"


def test_jobs_endpoints_require_recruitment_manage() -> None:
    source = inspect.getsource(jobs)
    assert source.count('require_permission("recruitment.manage")') >= 9


def test_jobs_routes_cover_lifecycle_actions() -> None:
    route_paths = sorted({route.path for route in jobs.router.routes})
    joined = " ".join(route_paths)
    assert "/{job_id}/publish" in joined
    assert "/{job_id}/pause" in joined
    assert "/{job_id}/resume" in joined
    assert "/{job_id}/close" in joined
    assert "/{job_id}/copy" in joined
    assert "/{job_id}/draft" in joined
