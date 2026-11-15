"""Card evidence, reviews and budget reservations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cards", sa.Column("journal", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("cards", sa.Column("job_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_cards_job", "cards", "jobs", ["job_id"], ["id"])
    op.add_column("card_fields", sa.Column("reason", sa.Text()))
    op.add_column("card_fields", sa.Column("source", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("card_fields", sa.Column("not_found", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("card_fields", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("llm_calls", sa.Column("state", sa.String(24), nullable=False, server_default="unknown"))
    op.add_column("llm_calls", sa.Column("reserved_usd", sa.Numeric(20, 10), nullable=False, server_default="0"))
    op.add_column("llm_calls", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"])
    op.create_index("ix_llm_calls_job_id", "llm_calls", ["job_id"])
    op.execute("UPDATE llm_calls SET error = NULL WHERE error = 'null'::jsonb")
    op.create_table('rate_limit_events', sa.Column('id', sa.UUID(), primary_key=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                    sa.Column('key', sa.String(64), nullable=False))
    op.create_index('ix_rate_limit_events_key', 'rate_limit_events', ['key'])


def downgrade():
    op.drop_table('rate_limit_events')
    op.drop_index("ix_llm_calls_job_id", table_name="llm_calls")
    op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
    for column in ("attempt", "reserved_usd", "state"):
        op.drop_column("llm_calls", column)
    for column in ("reviewed_at", "not_found", "source", "reason"):
        op.drop_column("card_fields", column)
    op.drop_constraint("fk_cards_job", "cards", type_="foreignkey")
    op.drop_column("cards", "job_id")
    op.drop_column("cards", "journal")
