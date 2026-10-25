import os

from fastapi.testclient import TestClient

from app.ingest.parse import parse_document
from app.ingest.runner import run_job
from app.main import create_app
from app.temporal import utc

os.environ["INGEST_INLINE"] = "1"


def client():
    app = create_app("sqlite:///:memory:")
    from app.db import session
    from app.seed import seed

    with session() as db:
        seed(db)
    return TestClient(app), app


def test_parser_reads_decagon_document_ids_and_received_dates():
    po = parse_document(
        "DECAGON\nPURCHASE ORDER\nPURCHASE ORDER PO-C01-1123\nSupplier\nMock Inference Cloud\nAuthorized amount USD\n145.00\n",
        utc("2025-09-30T00:00:00+00:00"),
    )
    assert po[0]["external_id"] == "PO-C01-1123"
    assert str(po[0]["amount"]) == "145.00"
    assert po[0]["kind"] == "purchase_order"
    invoice = parse_document(
        "INVOICE\nVENDOR INVOICE INV-C01-381\nVendor\nMock Inference Cloud\nInvoice date\n2025-09-28\nReceived by AP\n2025-12-10T10:00:00Z\nInvoice total USD\n158.00\n",
        utc("2025-09-30T00:00:00+00:00"),
    )
    assert invoice[0]["external_id"] == "INV-C01-381"
    assert str(invoice[0]["amount"]) == "158.00"
    assert invoice[0]["available_from"].isoformat().startswith("2025-12-10")
    email = parse_document(
        "From: accounting@vend-001.example\nSubject: revised estimate\nMessage-ID: <MSG-0001@synthetic-mail.example>\n\nFor PO-C01-1123, our estimate is USD 160.00.\n",
        utc("2025-09-30T00:00:00+00:00"),
    )
    assert email[0]["kind"] == "email"
    assert email[0]["external_id"] == "MSG-0001"
    assert str(email[0]["amount"]) == "160.00"


def test_parser_keeps_every_printed_amount():
    records = parse_document(
        "Invoice INV-9\nVendor: Northwind Hosting\nDate: 2025-09-01\nTotal $10.00\nA separate filing fee of $2.50 applies.\n",
        utc("2025-09-01T00:00:00+00:00"),
    )
    amounts = sorted(str(record["amount"]) for record in records)
    assert amounts == ["10.00", "2.50"]


def test_sample_packet_hides_the_invoice_until_it_was_received():
    api, _app = client()
    response = api.post("/api/v1/ingestions/sample", params={"available_from": "2025-09-30"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert "po-101.txt" in [step["label"] for step in body["steps"] if step["name"] == "read"]
    assert {item["amount"] for item in body["transactions"]} == {"145.00", "160.00", "150.00", "158.00"}
    invoice = next(item for item in body["transactions"] if item["id"] == "INV-381")
    assert invoice["available_from"].startswith("2025-12-10")
    assert all(item["episode_id"] for item in body["transactions"])
    case_id = next(step["detail"]["cases"][0] for step in body["steps"] if step["name"] == "cases")
    september = api.post("/api/v1/snapshots", json={"case_id": case_id, "actor_id": "actor-maya", "as_of": "2025-09-30T23:59:00+00:00", "mode": "audit"})
    graph = api.get(f"/api/v1/snapshots/{september.json()['id']}/graph")
    amounts = {node["amount"] for node in graph.json()["nodes"]}
    assert {"145.00", "160.00", "150.00"} <= amounts
    assert "158.00" not in amounts
    december = api.post("/api/v1/snapshots", json={"case_id": case_id, "actor_id": "actor-jordan", "as_of": "2025-12-15T12:00:00+00:00", "mode": "audit"})
    later = api.get(f"/api/v1/snapshots/{december.json()['id']}/graph?view=auditor")
    assert "158.00" in {node["amount"] for node in later.json()["nodes"]}


def test_pause_resume_and_cancel_keep_the_finished_steps():
    _api, app = client()
    from app.db import session
    from app.ingest.service import create_job, parse_available_from, sample_files
    from app.models import IngestJob, IngestStep

    with session() as db:
        job = create_job(db, "org-acme", parse_available_from("2025-09-30"), sample_files())

        def pause(database, step):
            if step.name == "parse":
                database.get(IngestJob, job.id).status = "paused"
                database.commit()

        assert run_job(db, job.id, pause) == "paused"
        steps = db.query(IngestStep).filter(IngestStep.job_id == job.id).all()
        assert any(step.status == "completed" for step in steps)
        assert any(step.status == "pending" for step in steps)
        database_job = db.get(IngestJob, job.id)
        database_job.status = "cancelled"
        db.commit()
        assert run_job(db, job.id) == "cancelled"
        done_before = {step.id for step in steps if step.status == "completed"}
        db.expire_all()
        still = db.query(IngestStep).filter(IngestStep.job_id == job.id, IngestStep.status == "completed").all()
        assert done_before <= {step.id for step in still}

    with session() as db:
        replacement = create_job(db, "org-acme", parse_available_from("2025-09-30"), sample_files()[:1])

        def pause_once(database, step):
            if step.name == "link":
                database.get(IngestJob, replacement.id).status = "paused"
                database.commit()

        assert run_job(db, replacement.id, pause_once) == "paused"
        replacement = db.get(IngestJob, replacement.id)
        replacement.status = "running"
        db.commit()
        assert run_job(db, replacement.id) == "completed"


def test_unreadable_document_fails_one_step():
    _api, _app = client()
    from app.db import session
    from app.ingest.service import create_job, parse_available_from
    from app.models import IngestStep

    with session() as db:
        job = create_job(db, "org-acme", parse_available_from("2025-09-30"), [("blank.txt", "text/plain", b"   ")])
        assert run_job(db, job.id) == "failed"
        failed = db.query(IngestStep).filter_by(job_id=job.id, status="failed").one()
        assert failed.name == "read"
