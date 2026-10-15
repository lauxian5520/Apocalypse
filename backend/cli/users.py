"""Create an invited account: cd backend && python -m cli.users USERNAME EMAIL [--admin]."""
import argparse
import getpass
from sqlalchemy import or_
from core.database import SessionLocal
from core.security import hash_password, password_error
from core.config import get_settings
from core.runtime import acquire_runtime_lock
from models.migrations import init_database
from models.user import User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('username')
    parser.add_argument('email')
    parser.add_argument('--admin', action='store_true')
    args = parser.parse_args()
    if not args.username.strip() or len(args.username) > 50 or '@' not in args.email:
        parser.error('用户名或邮箱无效')
    password = getpass.getpass('密码（不会回显）: ')
    error = password_error(password)
    if error:
        parser.error(error)
    if getpass.getpass('再次输入密码: ') != password:
        parser.error('两次密码不一致')
    maintenance_lock = acquire_runtime_lock(get_settings().var_dir)
    init_database()
    with SessionLocal() as db:
        if db.query(User).filter(or_(User.username == args.username, User.email == args.email)).first():
            parser.error('用户名或邮箱已存在')
        db.add(User(username=args.username, email=args.email, password_hash=hash_password(password),
                    role='admin' if args.admin else 'user', can_use_harness=args.admin))
        db.commit()
    maintenance_lock.close()
    print('账号已创建')


if __name__ == '__main__':
    main()
