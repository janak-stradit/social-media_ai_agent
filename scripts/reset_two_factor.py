"""Turn off two-step sign-in for one account, e.g. an admin locked out
because SECRET_KEY changed (its authenticator secret can no longer be
decrypted) or a lost phone. The password still applies; the user can set up
two-step sign-in again in Settings -> Security.

    python scripts/reset_two_factor.py admin@example.com

On the server (inside the app container):

    docker compose -f /opt/socialmedia/deploy/docker-compose.prod.yml exec web \
        python scripts/reset_two_factor.py admin@example.com
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    from db import get_totp_state, get_user_by_email, log_audit, set_totp

    user = get_user_by_email(argv[1])
    if user is None:
        print(f"No account with the email {argv[1]}.")
        return 1
    if not get_totp_state(user.id)["enabled"] and not get_totp_state(user.id)["secret"]:
        print(f"Two-step sign-in is already off for {user.email}.")
        return 0
    set_totp(user.id, None, False)
    log_audit("2fa.reset_by_script", target_user_id=user.id, details={"email": user.email})
    print(f"Two-step sign-in is now off for {user.email}. Sign in with the password, then set it up again "
          "in Settings -> Security.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
