import json
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

REFRESH_PREFIX = "auth:refresh:"
SESSION_PREFIX = "auth:session:"
USER_SESSIONS_PREFIX = "auth:user_sessions:"
STATUS_ACTIVE = "active"
STATUS_USED = "used"
STATUS_REVOKED = "revoked"


class SessionError(Exception):
    pass


class SessionUnavailableError(SessionError):
    pass


@dataclass(frozen=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    session_id: UUID
    family_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class SessionRecord:
    session_id: UUID
    family_id: UUID
    user_id: UUID
    token_version: int
    status: str


def _refresh_key(digest: str) -> str:
    return f"{REFRESH_PREFIX}{digest}"


def _session_key(session_id: UUID) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def _user_sessions_key(user_id: UUID) -> str:
    return f"{USER_SESSIONS_PREFIX}{user_id}"


def _ttl_seconds() -> int:
    return get_settings().REFRESH_TOKEN_DAYS * 24 * 60 * 60


def _serialize_record(record: SessionRecord) -> str:
    return json.dumps(
        {
            "session_id": str(record.session_id),
            "family_id": str(record.family_id),
            "user_id": str(record.user_id),
            "token_version": record.token_version,
            "status": record.status,
        }
    )


def _deserialize_record(payload: str) -> SessionRecord:
    data = json.loads(payload)
    return SessionRecord(
        session_id=UUID(data["session_id"]),
        family_id=UUID(data["family_id"]),
        user_id=UUID(data["user_id"]),
        token_version=int(data["token_version"]),
        status=data["status"],
    )


ROTATE_REFRESH_SCRIPT = """
local old_key = KEYS[1]
local new_key = KEYS[2]
local session_key = KEYS[3]
local user_sessions_key = KEYS[4]
local ttl = tonumber(ARGV[1])
local new_payload = ARGV[2]
local session_payload = ARGV[3]
local new_session_id = ARGV[4]

local old_payload = redis.call('GET', old_key)
if not old_payload then
  return 'MISSING'
end

local old_data = cjson.decode(old_payload)
if old_data.status == 'revoked' then
  return 'REVOKED'
end
if old_data.status == 'used' then
  local family_id = old_data.family_id
  local members = redis.call('SMEMBERS', user_sessions_key)
  for _, sid in ipairs(members) do
    local skey = 'auth:session:' .. sid
    local spayload = redis.call('GET', skey)
    if spayload then
      local sdata = cjson.decode(spayload)
      if sdata.family_id == family_id then
        sdata.status = 'revoked'
        redis.call('SET', skey, cjson.encode(sdata), 'EX', ttl)
      end
    end
  end
  old_data.status = 'revoked'
  redis.call('SET', old_key, cjson.encode(old_data), 'EX', ttl)
  return 'REUSED'
end

old_data.status = 'used'
redis.call('SET', old_key, cjson.encode(old_data), 'EX', ttl)
redis.call('SET', new_key, new_payload, 'EX', ttl)
redis.call('SET', session_key, session_payload, 'EX', ttl)
redis.call('SADD', user_sessions_key, new_session_id)
redis.call('EXPIRE', user_sessions_key, ttl)
return 'OK'
"""


class SessionService:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._rotate_script = self.redis.register_script(ROTATE_REFRESH_SCRIPT)

    async def create_session(
        self,
        user_id: UUID,
        token_version: int,
        access_token: str,
        refresh_token: str,
        refresh_digest: str,
        session_id: UUID | None = None,
    ) -> SessionTokens:
        try:
            session_id = session_id or uuid4()
            family_id = session_id
            record = SessionRecord(
                session_id=session_id,
                family_id=family_id,
                user_id=user_id,
                token_version=token_version,
                status=STATUS_ACTIVE,
            )
            ttl = _ttl_seconds()
            payload = _serialize_record(record)
            await self.redis.set(_refresh_key(refresh_digest), payload, ex=ttl)
            await self.redis.set(_session_key(session_id), payload, ex=ttl)
            await self.redis.sadd(_user_sessions_key(user_id), str(session_id))
            await self.redis.expire(_user_sessions_key(user_id), ttl)
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc

        return SessionTokens(
            access_token=access_token,
            refresh_token=refresh_token,
            session_id=session_id,
            family_id=family_id,
            user_id=user_id,
        )

    async def get_session_by_digest(self, refresh_digest: str) -> SessionRecord | None:
        try:
            payload = await self.redis.get(_refresh_key(refresh_digest))
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc
        if payload is None:
            return None
        return _deserialize_record(payload)

    async def get_session(self, session_id: UUID) -> SessionRecord | None:
        try:
            payload = await self.redis.get(_session_key(session_id))
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc
        if payload is None:
            return None
        return _deserialize_record(payload)

    async def rotate_refresh_token(
        self,
        old_digest: str,
        new_refresh_token: str,
        new_digest: str,
        access_token: str,
    ) -> SessionTokens:
        old_record = await self.get_session_by_digest(old_digest)
        if old_record is None or old_record.status == STATUS_REVOKED:
            raise SessionError("invalid refresh token")

        new_session_id = uuid4()
        new_record = SessionRecord(
            session_id=new_session_id,
            family_id=old_record.family_id,
            user_id=old_record.user_id,
            token_version=old_record.token_version,
            status=STATUS_ACTIVE,
        )
        ttl = _ttl_seconds()
        try:
            result = await self._rotate_script(
                keys=[
                    _refresh_key(old_digest),
                    _refresh_key(new_digest),
                    _session_key(new_session_id),
                    _user_sessions_key(old_record.user_id),
                ],
                args=[
                    ttl,
                    _serialize_record(new_record),
                    _serialize_record(new_record),
                    str(new_session_id),
                ],
            )
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc

        if result in {b"MISSING", b"REVOKED"}:
            raise SessionError("invalid refresh token")
        if result == b"REUSED":
            raise SessionError("refresh token reused")

        return SessionTokens(
            access_token=access_token,
            refresh_token=new_refresh_token,
            session_id=new_session_id,
            family_id=old_record.family_id,
            user_id=old_record.user_id,
        )

    async def revoke_session(self, session_id: UUID) -> None:
        record = await self.get_session(session_id)
        if record is None:
            return
        record = SessionRecord(
            session_id=record.session_id,
            family_id=record.family_id,
            user_id=record.user_id,
            token_version=record.token_version,
            status=STATUS_REVOKED,
        )
        ttl = _ttl_seconds()
        payload = _serialize_record(record)
        try:
            await self.redis.set(_session_key(session_id), payload, ex=ttl)
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc

    async def revoke_family(self, family_id: UUID, user_id: UUID) -> None:
        try:
            session_ids = await self.redis.smembers(_user_sessions_key(user_id))
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc

        ttl = _ttl_seconds()
        for raw_session_id in session_ids:
            session_id = UUID(raw_session_id)
            record = await self.get_session(session_id)
            if record is None or record.family_id != family_id:
                continue
            revoked = SessionRecord(
                session_id=record.session_id,
                family_id=record.family_id,
                user_id=record.user_id,
                token_version=record.token_version,
                status=STATUS_REVOKED,
            )
            await self.redis.set(
                _session_key(session_id), _serialize_record(revoked), ex=ttl
            )

    async def revoke_user_sessions(
        self,
        user_id: UUID,
        except_session_id: UUID | None = None,
    ) -> None:
        try:
            session_ids = await self.redis.smembers(_user_sessions_key(user_id))
        except RedisError as exc:
            raise SessionUnavailableError("session store unavailable") from exc

        ttl = _ttl_seconds()
        for raw_session_id in session_ids:
            session_id = UUID(raw_session_id)
            if except_session_id is not None and session_id == except_session_id:
                continue
            record = await self.get_session(session_id)
            if record is None:
                continue
            revoked = SessionRecord(
                session_id=record.session_id,
                family_id=record.family_id,
                user_id=record.user_id,
                token_version=record.token_version,
                status=STATUS_REVOKED,
            )
            await self.redis.set(
                _session_key(session_id), _serialize_record(revoked), ex=ttl
            )


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    classes = 0
    if re.search(r"[a-z]", password):
        classes += 1
    if re.search(r"[A-Z]", password):
        classes += 1
    if re.search(r"\d", password):
        classes += 1
    if re.search(r"[^A-Za-z0-9]", password):
        classes += 1
    if classes < 3:
        raise ValueError("password must include at least three character classes")
