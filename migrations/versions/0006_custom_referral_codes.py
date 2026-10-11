"""custom referral codes

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-11 23:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('referral_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=24), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('percent_off', sa.Integer(), nullable=False),
    sa.Column('months', sa.Integer(), nullable=False),
    sa.Column('max_uses', sa.Integer(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('owner_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('referral_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_referral_codes_code'), ['code'], unique=True)

    with op.batch_alter_table('referrals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_referrals_code_id'), ['code_id'], unique=False)
        batch_op.alter_column('referrer_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('referrals', schema=None) as batch_op:
        batch_op.alter_column('referrer_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_index(batch_op.f('ix_referrals_code_id'))
        batch_op.drop_column('code_id')

    with op.batch_alter_table('referral_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_referral_codes_code'))

    op.drop_table('referral_codes')
