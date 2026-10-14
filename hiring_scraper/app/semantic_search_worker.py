"""Local multilingual embedding service and asynchronous search indexer."""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from tokenizers import Tokenizer

from hiring_scraper.app.database import IS_SQLITE, SessionLocal
from hiring_scraper.app.models import (
    Company, Job, SemanticSearchDocument, SemanticSearchIndexState,
    SemanticSearchRefresh, utcnow,
)


LOG = logging.getLogger(__name__)
MODEL_ID = "intfloat/multilingual-e5-small"
MODEL_REVISION = "ccc66d3bcd826f577e26b9a4072cc5fe3a7ad6a3"
MODEL_KEY = f"{MODEL_ID}@{MODEL_REVISION}"
DIMENSIONS = 384
MODEL_DIR = Path(os.getenv("SEMANTIC_MODEL_DIR", "/opt/models/multilingual-e5-small"))
INDEX_BATCH_SIZE = max(2, int(os.getenv("SEMANTIC_INDEX_BATCH_SIZE", "32")))
INDEX_POLL_SECONDS = max(1, float(os.getenv("SEMANTIC_INDEX_POLL_SECONDS", "2")))
QUEUE_LEASE_SECONDS = 15 * 60
_embedder: LocalE5 | None = None


class EmbedRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class LocalE5:
    """Small CPU ONNX runtime for the pinned multilingual E5 model."""

    def __init__(self) -> None:
        self.tokenizer = Tokenizer.from_file(str(self._model_file("tokenizer.json")))
        self._tokenizer_lock = threading.Lock()
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(os.getenv("SEMANTIC_EMBEDDING_THREADS", "2")))
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(self._model_file("onnx/model_O4.onnx")), sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    @staticmethod
    def _model_file(filename: str) -> Path:
        local_file = MODEL_DIR / filename
        if local_file.is_file():
            return local_file
        return Path(hf_hub_download(
            repo_id=MODEL_ID, filename=filename, revision=MODEL_REVISION,
            cache_dir=os.getenv("HF_HOME", "/tmp/huggingface"),
        ))

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Tokenizer configuration is shared, so serialize tokenization only.
        # ONNX Runtime supports concurrent Run calls; leaving inference outside
        # this lock lets interactive queries run alongside the backfill worker.
        with self._tokenizer_lock:
            encodings = self.tokenizer.encode_batch(texts, add_special_tokens=True)
        width = max(len(item.ids) for item in encodings)
        token_ids = np.zeros((len(encodings), width), dtype=np.int64)
        masks = np.zeros((len(encodings), width), dtype=np.int64)
        for index, item in enumerate(encodings):
            length = len(item.ids)
            token_ids[index, :length] = item.ids
            masks[index, :length] = item.attention_mask
        inputs: dict[str, np.ndarray] = {}
        if "input_ids" in self.input_names:
            inputs["input_ids"] = token_ids
        if "attention_mask" in self.input_names:
            inputs["attention_mask"] = masks
        if "token_type_ids" in self.input_names:
            inputs["token_type_ids"] = np.zeros_like(token_ids)
        hidden = np.asarray(self.session.run(None, inputs)[0], dtype=np.float32)
        weighted = hidden * masks[:, :, None].astype(np.float32)
        pooled = weighted.sum(axis=1) / np.maximum(masks.sum(axis=1, keepdims=True), 1)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        normalized = pooled / np.maximum(norms, 1e-12)
        return normalized.tolist()


def _company_text(company: Company) -> str:
    values = [f"Company: {company.name}"]
    for label, value in (("industry", company.category), ("website", company.domain),
                         ("location", company.location_label), ("region", company.state)):
        if value:
            values.append(f"{label}: {value}")
    return "passage: " + ". ".join(values)


def _job_text(job: Job) -> str:
    values = [f"Job title: {job.title}"]
    for label, value in (("description", job.description), ("location", job.location_text),
                         ("employment type", job.employment_type), ("schedule", job.schedule),
                         ("work arrangement", job.work_arrangement), ("department", job.department),
                         ("seniority", job.seniority)):
        if value:
            values.append(f"{label}: {str(value)[:12000]}")
    return "passage: " + ". ".join(values)


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _claim_refreshes() -> tuple[str, list[tuple[str, int]]] | None:
    token = str(uuid4())
    now = utcnow()
    with SessionLocal.begin() as session:
        rows = session.execute(select(
            SemanticSearchRefresh.entity_type, SemanticSearchRefresh.entity_id,
        ).where(or_(SemanticSearchRefresh.lease_until.is_(None),
                    SemanticSearchRefresh.lease_until <= now))
            .order_by(SemanticSearchRefresh.queued_at, SemanticSearchRefresh.entity_type,
                      SemanticSearchRefresh.entity_id)
            .with_for_update(skip_locked=True).limit(INDEX_BATCH_SIZE)).all()
        if not rows:
            return None
        for entity_type, entity_id in rows:
            session.execute(update(SemanticSearchRefresh).where(
                SemanticSearchRefresh.entity_type == entity_type,
                SemanticSearchRefresh.entity_id == entity_id,
            ).values(lease_token=token, lease_until=now + timedelta(seconds=QUEUE_LEASE_SECONDS)))
        return token, [(row[0], row[1]) for row in rows]


def _unindexed_ids(session, entity_type: str, limit: int,
                   excluded: list[int] | None = None) -> list[int]:
    if limit <= 0:
        return []
    model = Company if entity_type == "company" else Job
    statement = select(model.id).where(
        ~exists(select(1).where(
            SemanticSearchDocument.entity_type == entity_type,
            SemanticSearchDocument.entity_id == model.id,
            SemanticSearchDocument.model_revision == MODEL_KEY,
        )),
        ~exists(select(1).where(
            SemanticSearchRefresh.entity_type == entity_type,
            SemanticSearchRefresh.entity_id == model.id,
        )),
    )
    if excluded:
        statement = statement.where(model.id.not_in(excluded))
    return [int(value) for value in session.scalars(
        statement.order_by(model.id).limit(limit))]


def _find_unindexed() -> list[tuple[str, int]]:
    """Backfill both entity types in every batch instead of starving jobs."""
    with SessionLocal() as session:
        company_ids = _unindexed_ids(session, "company", INDEX_BATCH_SIZE // 2)
        job_ids = _unindexed_ids(session, "job", INDEX_BATCH_SIZE - INDEX_BATCH_SIZE // 2)
        remaining = INDEX_BATCH_SIZE - len(company_ids) - len(job_ids)
        while remaining:
            progressed = False
            order = sorted(("company", "job"), key=lambda kind: len(company_ids if kind == "company" else job_ids))
            for entity_type in order:
                current = company_ids if entity_type == "company" else job_ids
                more = _unindexed_ids(session, entity_type, remaining, current)
                if more:
                    current.extend(more)
                    remaining -= len(more)
                    progressed = True
                if not remaining:
                    break
            if not progressed:
                break
        return ([*(('company', value) for value in company_ids),
                 *(('job', value) for value in job_ids)])


def _has_pending_refreshes(entity_type: str | None = None) -> bool:
    with SessionLocal() as session:
        statement = select(1)
        if entity_type is None:
            statement = statement.where(SemanticSearchRefresh.entity_type.in_(("company", "job")))
        else:
            statement = statement.where(SemanticSearchRefresh.entity_type == entity_type)
        return bool(session.scalar(select(exists(statement))))


def _mark_backfill_complete(entity_type: str) -> None:
    now = utcnow()
    with SessionLocal.begin() as session:
        statement = insert(SemanticSearchIndexState).values(
            entity_type=entity_type, model_revision=MODEL_KEY,
            backfill_complete=True, updated_at=now,
        )
        session.execute(statement.on_conflict_do_update(
            index_elements=["entity_type", "model_revision"],
            set_={"backfill_complete": True, "updated_at": now},
        ))


def _source_payloads(rows: list[tuple[str, int]]) -> dict[tuple[str, int], tuple[str, str]]:
    result: dict[tuple[str, int], tuple[str, str]] = {}
    by_type: dict[str, list[int]] = {"company": [], "job": []}
    for entity_type, entity_id in rows:
        by_type.setdefault(entity_type, []).append(entity_id)
    with SessionLocal() as session:
        for company in session.scalars(select(Company).where(Company.id.in_(by_type["company"]))):
            value = _company_text(company)
            result[("company", company.id)] = value, _content_hash(value)
        for job in session.scalars(select(Job).where(Job.id.in_(by_type["job"]))):
            value = _job_text(job)
            result[("job", job.id)] = value, _content_hash(value)
    return result


def _save_vectors(rows: list[tuple[str, int]], token: str | None,
                  vectors: list[list[float]], payloads: dict[tuple[str, int], tuple[str, str]]) -> None:
    now = utcnow()
    values = [{
        "entity_type": entity_type,
        "entity_id": entity_id,
        "content_hash": payloads[(entity_type, entity_id)][1],
        "model_revision": MODEL_KEY,
        "embedding": embedding,
        "indexed_at": now,
    } for (entity_type, entity_id), embedding in zip(rows, vectors)
      if (entity_type, entity_id) in payloads]
    if not values:
        return
    with SessionLocal.begin() as session:
        company_ids = [value["entity_id"] for value in values if value["entity_type"] == "company"]
        job_ids = [value["entity_id"] for value in values if value["entity_type"] == "job"]
        # Hold key-share locks through the vector upsert. If a source delete
        # races this batch, it either becomes visible here or its purge trigger
        # runs after this transaction and removes the just-written vector.
        live_companies = set(session.scalars(select(Company.id).where(
            Company.id.in_(company_ids)).with_for_update(read=True, key_share=True))) if company_ids else set()
        live_jobs = set(session.scalars(select(Job.id).where(
            Job.id.in_(job_ids)).with_for_update(read=True, key_share=True))) if job_ids else set()
        values = [value for value in values if
                  (value["entity_type"] == "company" and value["entity_id"] in live_companies) or
                  (value["entity_type"] == "job" and value["entity_id"] in live_jobs)]
        if not values:
            if token:
                for entity_type, entity_id in rows:
                    session.execute(delete(SemanticSearchRefresh).where(
                        SemanticSearchRefresh.entity_type == entity_type,
                        SemanticSearchRefresh.entity_id == entity_id,
                        SemanticSearchRefresh.lease_token == token,
                    ))
            return
        statement = insert(SemanticSearchDocument).values(values)
        session.execute(statement.on_conflict_do_update(
            index_elements=["entity_type", "entity_id"],
            set_={
                "content_hash": statement.excluded.content_hash,
                "model_revision": statement.excluded.model_revision,
                "embedding": statement.excluded.embedding,
                "indexed_at": statement.excluded.indexed_at,
            },
        ))
        if token:
            for entity_type, entity_id in rows:
                session.execute(delete(SemanticSearchRefresh).where(
                    SemanticSearchRefresh.entity_type == entity_type,
                    SemanticSearchRefresh.entity_id == entity_id,
                    SemanticSearchRefresh.lease_token == token,
                ))


def process_index_batch() -> bool:
    claimed = _claim_refreshes()
    token: str | None = None
    if claimed:
        token, rows = claimed
    else:
        rows = _find_unindexed()
        for entity_type in ("company", "job"):
            if not any(row[0] == entity_type for row in rows) and not _has_pending_refreshes(entity_type):
                _mark_backfill_complete(entity_type)
    if not rows:
        return False
    payloads = _source_payloads(rows)
    if not payloads:
        if token:
            with SessionLocal.begin() as session:
                for entity_type, entity_id in rows:
                    session.execute(delete(SemanticSearchRefresh).where(
                        SemanticSearchRefresh.entity_type == entity_type,
                        SemanticSearchRefresh.entity_id == entity_id,
                        SemanticSearchRefresh.lease_token == token,
                    ))
        return True
    missing = [item for item in rows if item not in payloads]
    if token and missing:
        with SessionLocal.begin() as session:
            for entity_type, entity_id in missing:
                session.execute(delete(SemanticSearchRefresh).where(
                    SemanticSearchRefresh.entity_type == entity_type,
                    SemanticSearchRefresh.entity_id == entity_id,
                    SemanticSearchRefresh.lease_token == token,
                ))
    work = [(entity_type, entity_id) for entity_type, entity_id in rows
            if (entity_type, entity_id) in payloads]
    embedder = _embedder
    if embedder is None:
        raise RuntimeError("Local embedding model is not ready")
    vectors = embedder.encode([payloads[key][0] for key in work])
    _save_vectors(work, token, vectors, payloads)
    LOG.info("indexed %s semantic documents", len(work))
    return True


def _index_loop() -> None:
    while True:
        if IS_SQLITE:
            time.sleep(INDEX_POLL_SECONDS)
            continue
        try:
            if not process_index_batch():
                time.sleep(INDEX_POLL_SECONDS)
        except Exception:
            LOG.exception("Semantic search indexing batch failed")
            time.sleep(INDEX_POLL_SECONDS)


app = FastAPI(title="Local semantic search embedding worker")


@app.on_event("startup")
def start_model_and_indexer() -> None:
    def load_and_index() -> None:
        global _embedder
        while _embedder is None:
            try:
                _embedder = LocalE5()
                LOG.info("loaded %s for CPU semantic search", MODEL_KEY)
            except Exception:
                LOG.exception("Could not load local multilingual embedding model")
                time.sleep(30)
        threading.Thread(target=_index_loop, name="semantic-indexer", daemon=True).start()

    threading.Thread(target=load_and_index, name="semantic-model-loader", daemon=True).start()


@app.get("/health/ready")
def ready() -> dict[str, str]:
    if _embedder is None:
        raise HTTPException(status_code=503, detail="Local embedding model is loading")
    return {"status": "ready", "model": MODEL_KEY}


@app.post("/embed")
def embed(request: EmbedRequest) -> dict[str, list[float]]:
    embedder = _embedder
    if embedder is None:
        raise HTTPException(status_code=503, detail="Local embedding model is loading")
    vector = embedder.encode(["query: " + request.text.strip()])[0]
    return {"embedding": vector}


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("SEMANTIC_EMBEDDING_PORT", "8001")),
                log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
