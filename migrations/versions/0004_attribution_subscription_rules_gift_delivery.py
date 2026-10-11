"""sign-up sources, subscription rules (cancel at, reminders), scheduled gift delivery

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=100), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('campaign', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('landing', sa.String(length=200), nullable=False, server_default=''))
        batch_op.create_index(batch_op.f('ix_users_source'), ['source'], unique=False)

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('cancel_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_reminder_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('gifts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('recipient_email', sa.String(length=320), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('send_on', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('sent_to_recipient_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=100), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('campaign', sa.String(length=200), nullable=False, server_default=''))
        batch_op.create_index(batch_op.f('ix_gifts_source'), ['source'], unique=False)

    # Existing paying subscriptions: count the reminder period from when they were created.
    op.execute("UPDATE subscriptions SET started_at = created_at "
               "WHERE stripe_subscription_id IS NOT NULL AND started_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('gifts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_gifts_source'))
        for col in ('campaign', 'source', 'sent_to_recipient_at', 'send_on', 'recipient_email'):
            batch_op.drop_column(col)

    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        for col in ('last_reminder_at', 'cancel_at', 'started_at'):
            batch_op.drop_column(col)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_source'))
        for col in ('landing', 'campaign', 'source'):
            batch_op.drop_column(col)
