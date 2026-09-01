"""Public user profiles."""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.deps import get_db, require_user
from core.errors import NotFoundError
from models.comment import Comment
from models.memo import Memo
from models.user import User
from schemas.user import UserProfileOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}/profile", response_model=UserProfileOut)
def get_user_profile(
    user_id: int,
    _: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise NotFoundError("用户不存在")

    memo_count = db.query(func.count(Memo.id)).filter(Memo.user_id == user_id).scalar() or 0
    comment_count = db.query(func.count(Comment.id)).filter(Comment.user_id == user_id).scalar() or 0

    return UserProfileOut(
        id=user.id,
        username=user.username,
        avatar_url=user.avatar_url,
        role=user.role,
        created_at=user.created_at,
        memo_count=int(memo_count),
        comment_count=int(comment_count),
    )
