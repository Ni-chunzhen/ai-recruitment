from pydantic import BaseModel


class ApiResponse[T](BaseModel):
    code: int
    message: str
    data: T


class HealthData(BaseModel):
    status: str
    service: str


class LiveData(BaseModel):
    status: str


class ReadyChecks(BaseModel):
    postgresql: str
    redis: str


class ReadyData(BaseModel):
    status: str
    checks: ReadyChecks
