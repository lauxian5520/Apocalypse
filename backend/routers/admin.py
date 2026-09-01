"""User administration."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.deps import get_db, require_admin
from core.errors import NotFoundError, ValidationError
from core.security import hash_password, password_error
from models.user import User
from schemas.common import Page
from schemas.user import AdminUserOut, ResetPasswordIn, UserRoleIn, UserStatusIn

router = APIRouter(prefix="/admin", tags=["admin"])

ROLES = {"user", "admin"}


def _admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == "admin").count()


def _get_target(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundError("用户不存在")
    return user


@router.get("/users", response_model=Page[AdminUserOut])
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(""),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    kw = keyword.strip()
    if kw:
        like = f"%{kw}%"
        query = query.filter(User.username.ilike(like) | User.email.ilike(like))

    total = query.count()
    users = (
        query.order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return Page[AdminUserOut](total=total, page=page, page_size=page_size, items=users)


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    body: UserRoleIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if body.role not in ROLES:
        raise ValidationError("role 仅支持 user/admin")

    target = _get_target(db, user_id)
    if target.id == admin.id and body.role != "admin":
        raise ValidationError("不能移除自己的管理员身份")
    if target.role == "admin" and body.role == "user" and _admin_count(db) <= 1:
        raise ValidationError("至少保留一个管理员")

    target.role = body.role
    db.commit()
    return {"id": target.id, "role": target.role}


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    body: UserStatusIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = _get_target(db, user_id)
    if target.id == admin.id and body.is_disabled:
        raise ValidationError("不能禁用当前管理员账号")
    if target.role == "admin" and body.is_disabled and _admin_count(db) <= 1:
        raise ValidationError("至少保留一个可用管理员")

    target.is_disabled = body.is_disabled
    db.commit()
    return {"id": target.id, "is_disabled": target.is_disabled}


@router.patch("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    body: ResetPasswordIn,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    pwd_err = password_error(body.value)
    if pwd_err:
        raise ValidationError(f"新密码不合法：{pwd_err}")

    target = _get_target(db, user_id)
    target.password_hash = hash_password(body.value)
    db.commit()
    return {"id": target.id, "message": "密码已重置"}


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = _get_target(db, user_id)
    if target.id == admin.id:
        raise ValidationError("不能删除当前管理员账号")
    if target.role == "admin" and _admin_count(db) <= 1:
        raise ValidationError("至少保留一个管理员")

    db.delete(target)
    db.commit()
