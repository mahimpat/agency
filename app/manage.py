"""Admin commands.

  python -m app.manage run-due              write and post the letters that are due (run daily)
  python -m app.manage make-admin EMAIL     give an existing account access to /admin
"""

import argparse
import logging
import sys

from sqlalchemy import select

from . import jobs
from .config import Settings
from .models import User, make_sessionmaker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.manage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-due", help="Write and post the letters that are due")
    p = sub.add_parser("make-admin", help="Give an account access to /admin")
    p.add_argument("email")
    args = parser.parse_args()

    settings = Settings()
    db = make_sessionmaker(settings.database_url)()
    try:
        if args.command == "run-due":
            summary = jobs.run_due(db, settings)
            print(summary)
            sys.exit(1 if summary.errors else 0)
        if args.command == "make-admin":
            user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
            if not user:
                sys.exit(f"No account with email {args.email}. Sign up on the site first.")
            user.is_admin = True
            db.commit()
            print(f"{user.email} is now an admin.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
