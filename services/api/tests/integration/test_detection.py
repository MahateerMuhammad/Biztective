"""Detection on generated data: no fabricated incidents on clean data, planted spikes found."""

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.analytics.config import load_config
from app.analytics.evidence import sha256_of
from app.analytics.service import compute_analysis
from app.core.db import set_business_context
from scripts.generate_dataset import Options, generate_with_manifest
from tests.helpers import Tenant, create_tenant, ingest_files, login


def options(seed: int, *scenarios: str, spike_rate: float = 0.6) -> Options:
    return Options(
        seed=seed,
        weeks=26,
        customers=1500,
        products=120,
        orders_per_day=45,
        start=date(2026, 1, 5),
        noise=0.0,
        pii_column=False,
        scenarios=scenarios,
        spike_return_rate=spike_rate,
    )


async def analyse(
    app: FastAPI, client: httpx.AsyncClient, tenant: Tenant, opts: Options
) -> tuple[dict[str, Any], dict[str, str], str]:
    files, manifest = generate_with_manifest(opts)
    payload = {name.removesuffix(".csv"): body for name, body in files.items()}
    detail = await ingest_files(app, client, tenant.business_id, payload)
    assert detail["status"] == "ready", detail
    base = f"/api/v1/businesses/{tenant.business_id}/datasets/{detail['id']}"
    analysis = (await client.get(f"{base}/analysis")).json()
    return analysis, manifest, base


@pytest.fixture
async def owner(client: httpx.AsyncClient, owner_engine: Engine) -> Tenant:
    tenant = create_tenant(owner_engine, "alice")
    assert (await login(client, tenant.email)).status_code == 200
    return tenant


@pytest.mark.parametrize("seed", [101, 202, 303, 404, 505])
async def test_clean_data_produces_no_incidents(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, seed: int
) -> None:
    analysis, _, _ = await analyse(app, client, owner, options(seed))
    assert analysis["status"] == "completed"
    assert analysis["incidents"] == [], analysis["incidents"]
    detection = analysis["summary"]["detection"]
    assert detection["slices_tested"] > 50  # it really looked, it just found nothing
    assert detection["flags_before_consolidation"] == 0


async def test_twenty_control_seeds_meet_published_false_positive_budget(
    app: FastAPI,
    client: httpx.AsyncClient,
    owner_engine: Engine,
    record_property: Callable[[str, object], None],
) -> None:
    # AC-6.1 permits <=1 false positive per control on average. The original five-seed
    # zero-incident regression above remains unchanged; calibration uses the published gate.
    counts: dict[str, int] = {}
    keys: dict[str, list[str]] = {}
    for seed in (101 * i for i in range(1, 21)):
        tenant = create_tenant(owner_engine, f"control{seed}")
        assert (await login(client, tenant.email)).status_code == 200
        opts = replace(options(seed), start=date(2026, 1, 5) + timedelta(weeks=seed % 12))
        analysis, _, _ = await analyse(app, client, tenant, opts)
        assert analysis["status"] == "completed"
        assert analysis["summary"]["detection"]["slices_tested"] > 50
        counts[str(seed)] = len(analysis["incidents"])
        keys[str(seed)] = [i["key"] for i in analysis["incidents"]]
    record_property("control_false_positive_counts", counts)
    record_property("control_false_positive_keys", keys)
    assert sum(counts.values()) <= len(counts), (counts, keys)


@pytest.mark.parametrize("seed", range(7, 27))
async def test_planted_return_spike_is_detected_with_evidence(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine, seed: int
) -> None:
    analysis, manifest, base = await analyse(
        app,
        client,
        owner,
        replace(options(seed, "return_spike"), start=date(2026, 1, 5) + timedelta(weeks=seed % 12)),
    )
    assert analysis["status"] == "completed"
    planted = {("product", manifest["planted_product"]), ("category", manifest["planted_category"])}
    refund = [i for i in analysis["incidents"] if i["metric"] == "refund_rate"]
    # Broader slices that genuinely contain the excess refunds (e.g. a customer segment) may
    # rank above it by impact; the planted product or its category must be among them.
    matches = [i for i in refund if (i["dimension"], i["value"]) in planted]
    assert matches, analysis["incidents"]
    top = matches[0]
    assert top["severity"] in {"critical", "high", "medium", "low"}
    assert top["affected"]["returns"] > 0
    assert top["affected"]["order_lines"] > 0
    assert {e["kind"] for e in top["evidence"]} == {
        "incident_detection",
        "source_records",
        "return_reason_mix",
    }

    detail = (await client.get(f"{base}/incidents/{top['id']}")).json()
    evidence = {e["kind"]: e for e in detail["evidence"]}
    for item in detail["evidence"]:
        assert sha256_of(item["result"]) == item["result_hash"]
    stats = evidence["incident_detection"]["result"]
    assert stats["z"] is None or stats["z"] >= 3.0
    assert len(stats["baseline_weekly"]) == 12
    assert len(stats["current_weekly"]) == 4
    mix = {r["reason_code"]: r for r in evidence["return_reason_mix"]["result"]["reasons"]}
    assert float(mix["defective"]["share_change"]) > 0.1  # the planted mechanism is visible

    sample = evidence["source_records"]["result"]["sample"]
    assert 0 < len(sample) <= 25
    with owner_engine.connect() as conn:  # every source reference resolves to a stored row
        for ref in sample:
            row = conn.execute(
                text(
                    "SELECT return_id, refund_amount FROM returns WHERE source_row = :r "
                    "AND dataset_id = (SELECT dataset_id FROM analysis_revisions WHERE id = :rev)"
                ),
                {"r": ref["source_row"], "rev": analysis["id"]},
            ).one()
            assert row.return_id == ref["key"]
            assert f"{row.refund_amount}" == ref["amount"]


@pytest.mark.parametrize("seed", range(7, 27))
async def test_planted_discount_spike_is_detected_with_evidence(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, seed: int
) -> None:
    analysis, manifest, base = await analyse(
        app,
        client,
        owner,
        replace(
            options(seed, "discount_spike"), start=date(2026, 1, 5) + timedelta(weeks=seed % 12)
        ),
    )
    assert analysis["status"] == "completed"
    planted = {("product", manifest["planted_product"]), ("category", manifest["planted_category"])}
    matches = [
        i
        for i in analysis["incidents"]
        if i["metric"] == "discount_rate" and (i["dimension"], i["value"]) in planted
    ]
    assert matches, analysis["incidents"]
    incident = matches[0]
    assert incident["affected"]["order_lines"] > 0
    detail = (await client.get(f"{base}/incidents/{incident['id']}")).json()
    evidence = {item["kind"]: item for item in detail["evidence"]}
    assert {"incident_detection", "source_records"} <= evidence.keys()
    for item in detail["evidence"]:
        assert sha256_of(item["result"]) == item["result_hash"]
    stats = evidence["incident_detection"]["result"]
    assert stats["z"] is None or stats["z"] >= 3
    assert float(stats["delta_abs"]) > 0.02
    assert 0 < len(evidence["source_records"]["result"]["sample"]) <= 25


async def test_analysis_is_reproducible(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    analysis, _, _ = await analyse(app, client, owner, options(7, "return_spike"))
    sessionmaker = app.state.sessionmaker
    dataset_id = (
        uuid.UUID(analysis["summary"]["dataset_id"])
        if "dataset_id" in analysis["summary"]
        else None
    )
    async with sessionmaker() as session, session.begin():
        await set_business_context(session, owner.business_id)
        dataset_id = dataset_id or (
            await session.scalar(
                text("SELECT dataset_id FROM analysis_revisions WHERE id = :r"),
                {"r": analysis["id"]},
            )
        )
        config = await load_config(session)
        runs = [await compute_analysis(session, dataset_id, config) for _ in range(2)]
    digests = [
        sha256_of(
            [
                r.snapshot,
                [p.__dict__ for p in r.profiles],
                [(i.flag.key, i.rank, i.severity, str(i.flag.impact)) for i in r.incidents],
                r.evidence,
                r.affected,
            ]
        )
        for r in runs
    ]
    assert digests[0] == digests[1]
    assert [i.flag.key for i in runs[0].incidents] == [i["key"] for i in analysis["incidents"]]


async def test_rerequest_is_idempotent(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant, owner_engine: Engine
) -> None:
    from tests.helpers import csrf_headers

    analysis, _, base = await analyse(app, client, owner, options(101))
    again = await client.post(f"{base}/analysis", headers=await csrf_headers(client))
    assert again.status_code == 202
    assert again.json() == {"id": analysis["id"], "status": "completed"}
    with owner_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM analysis_revisions")) == 1
        assert conn.scalar(text("SELECT count(*) FROM jobs WHERE type = 'analyze_dataset'")) == 1


async def test_immaterial_anomaly_is_not_reported(
    app: FastAPI, client: httpx.AsyncClient, owner: Tenant
) -> None:
    """Seed 8 at an 18% planted return rate: the product is a clear statistical outlier, but its
    excess refunds are below 1% of current net revenue, so no incident is raised (materiality)."""
    from decimal import Decimal

    from app.analytics import queries
    from app.analytics.detection import Flag, evaluate_slice
    from app.analytics.periods import detection_windows

    analysis, manifest, _ = await analyse(
        app, client, owner, options(8, "return_spike", spike_rate=0.18)
    )
    assert analysis["status"] == "completed"
    planted = ("product", manifest["planted_product"])
    assert all((i["dimension"], i["value"]) != planted for i in analysis["incidents"])
    async with app.state.sessionmaker() as session, session.begin():
        await set_business_context(session, owner.business_id)
        dataset_id = await session.scalar(
            text("SELECT dataset_id FROM analysis_revisions WHERE id = :r"), {"r": analysis["id"]}
        )
        config = await load_config(session)
        bounds = await queries.dataset_bounds(session, dataset_id)
        windows = detection_windows(bounds.first_order, bounds.as_of, 4, 12)  # type: ignore[arg-type]
        assert windows is not None
        weekly = await queries.aggregate(
            session, dataset_id, windows.start, windows.end, dimensions=("product",), by_week=True
        )
    series = {wk: t for (wk, d, v), t in weekly.items() if (d, v) == planted and wk}
    unconstrained = evaluate_slice("refund_rate", planted, series, windows, Decimal(0), config)
    assert isinstance(unconstrained, Flag)
    assert unconstrained.z is not None
    assert unconstrained.z >= 3
    materiality = Decimal(analysis["summary"]["detection"]["materiality"])
    assert unconstrained.impact < materiality
