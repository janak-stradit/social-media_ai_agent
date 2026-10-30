"""Make an existing account an admin (Admin Control Center, AI calls), or
take admin away with --remove. The account signs up as usual first.

    python scripts/make_admin.py you@example.com
    python scripts/make_admin.py you@example.com --remove

On the server (inside the app container):

    docker compose -f /opt/socialmedia/deploy/docker-compose.prod.yml exec web \
        python scripts/make_admin.py you@example.com

With ADMIN_2FA_REQUIRED on, an admin turns on two-step sign-in
(Settings -> Security) before the admin tools open.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--remove"]
    remove = "--remove" in argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    from sqlalchemy.orm import Session

    from db import User, engine, log_audit

    email = args[0].strip().lower()
    with Session(engine) as session:
        user = session.query(User).filter(User.email.ilike(email)).first()
        if user is None:
            print(f"No account with the email {email}. Sign up first, then run this again.")
            return 1
        if bool(user.is_admin) != remove:
            print(f"{user.email} is {'not ' if remove else ''}an admin already.")
            return 0
        if remove and session.query(User).filter(User.is_admin.is_(True)).count() <= 1:
            print("This is the only admin: make someone else an admin first.")
            return 1
        user.is_admin = not remove
        session.commit()
        user_id, user_email = user.id, user.email
    log_audit("admin.granted_by_script" if not remove else "admin.removed_by_script", target_user_id=user_id,
              details={"email": user_email})
    print(f"{user_email} is {'no longer' if remove else 'now'} an admin. Sign out and in again to see the admin tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
