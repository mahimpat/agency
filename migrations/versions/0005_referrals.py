"""referrals

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11 22:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('referrals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('referrer_id', sa.Integer(), nullable=False),
    sa.Column('friend_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('friend_sub_id', sa.Integer(), nullable=True),
    sa.Column('reward_pence', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('paid_at', sa.DateTime(), nullable=True),
    sa.Column('rewarded_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('friend_id')
    )
    with op.batch_alter_table('referrals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_referrals_referrer_id'), ['referrer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_referrals_status'), ['status'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('referral_code', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('referred_by_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_users_referred_by_id'), ['referred_by_id'], unique=False)
        batch_op.create_unique_constraint('uq_users_referral_code', ['referral_code'])


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_referral_code', type_='unique')
        batch_op.drop_index(batch_op.f('ix_users_referred_by_id'))
        batch_op.drop_column('referred_by_id')
        batch_op.drop_column('referral_code')

    with op.batch_alter_table('referrals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_referrals_status'))
        batch_op.drop_index(batch_op.f('ix_referrals_referrer_id'))

    op.drop_table('referrals')
