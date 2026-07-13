from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.user import User, UserApprovalStatus


def test_lowercase_approval_status_from_existing_database_loads() -> None:
    """Legacy migration values must be readable by the ORM during login."""
    engine = create_engine("sqlite://")
    User.__table__.create(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, username, hashed_password, password, full_name, email, timezone, role, approval_status) "
                "VALUES (1, 'keeper', '', '', 'Keeper', 'keeper@example.com', 'UTC', 'PLAYER', 'approved')"
            )
        )

    with Session(engine) as session:
        user = session.get(User, 1)

    assert user is not None
    assert user.approval_status is UserApprovalStatus.APPROVED
