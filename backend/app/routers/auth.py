import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.db_models import AuditLog, Organisation, Role, User, UserRole, UserSession
from app.security import generate_session_token, hash_token, verify_password

router = APIRouter()

SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))


class LoginRequest(BaseModel):
    email: str
    password: str


def _user_role_name(db: Session, user: User) -> str | None:
    return db.scalar(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id, UserRole.org_id == user.org_id)
    )


def _user_response(db: Session, user: User, org: Organisation, token: str | None = None) -> dict:
    body = {
        "user_id": str(user.id),
        "name": f"{user.first_name} {user.last_name}",
        "email": str(user.email),
        "role": _user_role_name(db, user),
        "org_id": str(user.org_id),
        "org_name": org.name,
    }
    if token is not None:
        body["token"] = token
    return body


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    now = datetime.now()

    user = db.scalar(select(User).where(User.email == email))

    if user is None or not user.is_active:
        db.add(AuditLog(
            org_id=user.org_id if user else None,
            actor_id=user.id if user else None,
            event_type="user.login_failed",
            ip_address=ip_address,
            user_agent=user_agent,
            event_metadata={"email": email},
        ))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.locked_until and user.locked_until > now:
        raise HTTPException(status_code=423, detail="Account is locked. Try again later.")

    if not verify_password(user.password_hash, body.password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= LOGIN_MAX_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
        db.add(AuditLog(
            org_id=user.org_id,
            actor_id=user.id,
            event_type="user.login_failed",
            ip_address=ip_address,
            user_agent=user_agent,
        ))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now

    token = generate_session_token()
    db.add(UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        device_info=user_agent,
        ip_address=ip_address,
        expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
    ))
    db.add(AuditLog(
        org_id=user.org_id,
        actor_id=user.id,
        event_type="user.login",
        ip_address=ip_address,
        user_agent=user_agent,
    ))

    org = db.get(Organisation, user.org_id)
    response = _user_response(db, user, org, token=token)
    db.commit()
    return response


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> tuple[User, UserSession]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.split(" ", 1)[1].strip()
    now = datetime.now()

    session = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user, session


@router.get("/me")
def get_me(current: tuple[User, UserSession] = Depends(get_current_user), db: Session = Depends(get_db)):
    user, _session = current
    org = db.get(Organisation, user.org_id)
    return _user_response(db, user, org)


@router.post("/logout")
def logout(
    request: Request,
    current: tuple[User, UserSession] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user, session = current
    session.revoked_at = datetime.now()
    db.add(AuditLog(
        org_id=user.org_id,
        actor_id=user.id,
        event_type="user.logout",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ))
    db.commit()
    return {"status": "ok"}
