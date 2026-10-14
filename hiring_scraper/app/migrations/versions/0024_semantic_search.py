"""Add local multilingual semantic search vectors and refresh triggers."""
import hashlib
import json

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0024_semantic_search"
down_revision = "0023_search_snapshot_refresh_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.add_column("job_search_snapshots", sa.Column(
        "criteria_hash", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("job_search_snapshots", sa.Column(
        "is_prewarmed", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("job_search_snapshot_refreshes", sa.Column(
        "prewarm", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index("ix_job_search_snapshots_criteria_created", "job_search_snapshots",
                    ["criteria_hash", "created_at"])
    snapshot_rows = bind.execute(
        sa.text("SELECT key, payload FROM job_search_snapshots")
    ).mappings().all()
    for row in snapshot_rows:
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        criteria = payload.get("criteria") if isinstance(payload, dict) else None
        if not isinstance(criteria, dict):
            continue
        stable = {name: value for name, value in criteria.items() if name != "freshness_bucket"}
        signature = hashlib.sha256(json.dumps(
            stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        bind.execute(sa.text(
            "UPDATE job_search_snapshots SET criteria_hash = :signature WHERE key = :key"
        ), {"signature": signature, "key": row["key"]})

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "semantic_search_documents",
        sa.Column("entity_type", sa.String(length=16), primary_key=True),
        sa.Column("entity_id", sa.Integer(), primary_key=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("model_revision", sa.String(length=128), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_semantic_search_documents_entity_revision", "semantic_search_documents",
                    ["entity_type", "model_revision"])
    op.create_table(
        "semantic_search_refreshes",
        sa.Column("entity_type", sa.String(length=16), primary_key=True),
        sa.Column("entity_id", sa.Integer(), primary_key=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_semantic_search_refresh_claim", "semantic_search_refreshes",
                    ["queued_at", "lease_until"])
    op.create_table(
        "semantic_search_index_state",
        sa.Column("entity_type", sa.String(length=16), primary_key=True),
        sa.Column("model_revision", sa.String(length=128), primary_key=True),
        sa.Column("backfill_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
        CREATE INDEX ix_semantic_company_embedding_hnsw
        ON semantic_search_documents USING hnsw (embedding vector_cosine_ops)
        WHERE entity_type = 'company'
    """)
    op.execute("""
        CREATE INDEX ix_semantic_job_embedding_hnsw
        ON semantic_search_documents USING hnsw (embedding vector_cosine_ops)
        WHERE entity_type = 'job'
    """)
    op.execute("""
        CREATE FUNCTION queue_company_semantic_refresh() RETURNS trigger AS $$
        BEGIN
            DELETE FROM semantic_search_documents
             WHERE entity_type = 'company' AND entity_id = NEW.id;
            INSERT INTO semantic_search_refreshes(entity_type, entity_id, queued_at)
            VALUES ('company', NEW.id, now())
            ON CONFLICT(entity_type, entity_id) DO UPDATE
                SET queued_at = EXCLUDED.queued_at, lease_token = NULL, lease_until = NULL;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER companies_semantic_refresh_insert
        AFTER INSERT ON companies
        FOR EACH ROW EXECUTE FUNCTION queue_company_semantic_refresh()
    """)
    op.execute("""
        CREATE TRIGGER companies_semantic_refresh_update
        AFTER UPDATE OF name, domain, category, state, location_label ON companies
        FOR EACH ROW EXECUTE FUNCTION queue_company_semantic_refresh()
    """)
    op.execute("""
        CREATE FUNCTION queue_job_semantic_refresh() RETURNS trigger AS $$
        BEGIN
            DELETE FROM semantic_search_documents
             WHERE entity_type = 'job' AND entity_id = NEW.id;
            INSERT INTO semantic_search_refreshes(entity_type, entity_id, queued_at)
            VALUES ('job', NEW.id, now())
            ON CONFLICT(entity_type, entity_id) DO UPDATE
                SET queued_at = EXCLUDED.queued_at, lease_token = NULL, lease_until = NULL;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER jobs_semantic_refresh_insert
        AFTER INSERT ON jobs
        FOR EACH ROW EXECUTE FUNCTION queue_job_semantic_refresh()
    """)
    op.execute("""
        CREATE TRIGGER jobs_semantic_refresh_update
        AFTER UPDATE OF title, description, location_text, is_remote, work_arrangement,
            employment_type, schedule, department, seniority ON jobs
        FOR EACH ROW EXECUTE FUNCTION queue_job_semantic_refresh()
    """)
    op.execute("""
        CREATE FUNCTION purge_semantic_document() RETURNS trigger AS $$
        BEGIN
            DELETE FROM semantic_search_documents
             WHERE entity_type = TG_ARGV[0] AND entity_id = OLD.id;
            DELETE FROM semantic_search_refreshes
             WHERE entity_type = TG_ARGV[0] AND entity_id = OLD.id;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER companies_semantic_purge AFTER DELETE ON companies
        FOR EACH ROW EXECUTE FUNCTION purge_semantic_document('company')
    """)
    op.execute("""
        CREATE TRIGGER jobs_semantic_purge AFTER DELETE ON jobs
        FOR EACH ROW EXECUTE FUNCTION purge_semantic_document('job')
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS jobs_semantic_purge ON jobs")
    op.execute("DROP TRIGGER IF EXISTS companies_semantic_purge ON companies")
    op.execute("DROP FUNCTION IF EXISTS purge_semantic_document()")
    op.execute("DROP TRIGGER IF EXISTS jobs_semantic_refresh_update ON jobs")
    op.execute("DROP TRIGGER IF EXISTS jobs_semantic_refresh_insert ON jobs")
    op.execute("DROP FUNCTION IF EXISTS queue_job_semantic_refresh()")
    op.execute("DROP TRIGGER IF EXISTS companies_semantic_refresh_update ON companies")
    op.execute("DROP TRIGGER IF EXISTS companies_semantic_refresh_insert ON companies")
    op.execute("DROP FUNCTION IF EXISTS queue_company_semantic_refresh()")
    op.drop_index("ix_semantic_job_embedding_hnsw", table_name="semantic_search_documents")
    op.drop_index("ix_semantic_company_embedding_hnsw", table_name="semantic_search_documents")
    op.drop_index("ix_semantic_search_refresh_claim", table_name="semantic_search_refreshes")
    op.drop_table("semantic_search_refreshes")
    op.drop_table("semantic_search_index_state")
    op.drop_index("ix_semantic_search_documents_entity_revision", table_name="semantic_search_documents")
    op.drop_table("semantic_search_documents")
    op.drop_index("ix_job_search_snapshots_criteria_created", table_name="job_search_snapshots")
    op.drop_column("job_search_snapshot_refreshes", "prewarm")
    op.drop_column("job_search_snapshots", "is_prewarmed")
    op.drop_column("job_search_snapshots", "criteria_hash")
