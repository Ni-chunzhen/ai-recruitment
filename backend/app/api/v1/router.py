from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin_ai_tasks,
    ai_tasks,
    audit_logs,
    auth,
    candidates,
    health,
    interview_transcripts,
    interviews,
    invitations,
    jobs,
    resumes,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(audit_logs.router)
api_router.include_router(jobs.router)
api_router.include_router(candidates.router)
api_router.include_router(ai_tasks.router)
api_router.include_router(admin_ai_tasks.router)
api_router.include_router(resumes.router)
api_router.include_router(interviews.router)
api_router.include_router(invitations.router)
api_router.include_router(interview_transcripts.router)
