"""Registration, login, session and avatar endpoints."""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.deps import get_db, require_user
from core.errors import ValidationError
from core.security import (
    clear_auth_cookies,
    create_access_token,
    hash_password,
    password_error,
    set_auth_cookies,
    verify_password,
)
from models.user import User
from schemas.auth import CaptchaOut, LoginIn, UserOut
from services import storage_service
from services.captcha_service import generate_captcha, verify_captcha

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_USERNAME_LENGTH = 50


async def _read_registration(request: Request) -> tuple[dict, UploadFile | None]:
    """Accept the registration payload as multipart, form-encoded or JSON."""
    ctype = (request.headers.get("content-type") or "").lower()

    if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        form = await request.form()
        avatar = form.get("avatar")
        body = {k: form.get(k) for k in ("username", "email", "password", "captcha", "captcha_token")}
        return body, (avatar if getattr(avatar, "filename", None) else None)

    # Parse JSON regardless of the declared Content-Type: cached older frontends
    # sent JSON bodies labelled text/plain.
    try:
        return await request.json(), None
    except Exception:
        pass
    try:
        import json
        return json.loads(await request.body()), None
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 或表单格式")


@router.get("/captcha", response_model=CaptchaOut)
def get_captcha():
    image_data, token = generate_captcha()
    return CaptchaOut(image=image_data, token=token)


@router.post("/register", status_code=201, response_model=UserOut)
async def register(request: Request, db: Session = Depends(get_db)):
    body, avatar = await _read_registration(request)

    username = (body.get("username") or "").strip()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    captcha = (body.get("captcha") or "").strip()
    captcha_token = body.get("captcha_token") or ""

    if not all([username, email, password, captcha, captcha_token]):
        raise ValidationError("所有字段均为必填项")
    if len(username) > MAX_USERNAME_LENGTH:
        raise ValidationError(f"用户名不能超过 {MAX_USERNAME_LENGTH} 个字符")

    # The frontend's minlength=8 is trivially bypassed, so re-check here.
    pwd_err = password_error(password)
    if pwd_err:
        raise ValidationError(pwd_err)

    if not verify_captcha(captcha, captcha_token):
        raise ValidationError("验证码错误或已过期")

    if db.query(User).filter(User.username == username).first():
        raise ValidationError("用户名已存在")
    if db.query(User).filter(User.email == email).first():
        raise ValidationError("邮箱已被注册")

    # Read and validate the avatar BEFORE inserting the user: doing it afterwards
    # left an orphan account behind on every rejected avatar, and the client
    # could then never register that username again.
    avatar_bytes = None
    if avatar is not None:
        avatar_bytes = await storage_service.read_upload(
            avatar,
            allowed_types=storage_service.IMAGE_TYPES,
            max_mb=storage_service.settings.max_image_size_mb,
            label="头像",
        )

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role="admin" if db.query(User).count() == 0 else "user",   # first user is admin
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"注册失败: {e}")

    if avatar_bytes is not None:
        stored = storage_service.write_bytes(
            avatar_bytes,
            prefix=f"avatar_{user.id}_",
            original_name=avatar.filename or "",
            content_type=avatar.content_type or "",
            directory=storage_service.settings.avatar_dir,
            url_prefix="/api/uploads/avatars",
            default_ext=".jpg",
        )
        user.avatar_url = stored.url
        db.commit()
        db.refresh(user)

    return user


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.is_disabled:
        raise HTTPException(status_code=403, detail="账号已被管理员禁用")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    response = JSONResponse({"user": UserOut.model_validate(user).model_dump(mode="json")})
    set_auth_cookies(response, create_access_token(user.id, user.role))
    return response


@router.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    clear_auth_cookies(response)
    return response


@router.get("/me", response_model=UserOut)
def get_me(user: User = Depends(require_user)):
    return user


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    old_filename = os.path.basename(user.avatar_url or "")
    stored = await storage_service.save_avatar(file, user.id)
    user.avatar_url = stored.url
    db.commit()
    # Replacing an avatar used to leave the previous file behind forever.
    storage_service.delete_file(storage_service.settings.avatar_dir, old_filename)
    return {"avatar_url": user.avatar_url}
