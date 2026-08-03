from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_lab.domain.licensing import LicenseValidationError
from quant_lab.infrastructure.offline_licensing import (
    OfflineLicenseService,
    device_code_from_identity,
    generate_keypair,
    issue_license_document,
    load_private_key,
    load_public_key,
    verify_license_document,
)
from quant_lab.interfaces.api.app import create_app
from quant_lab.interfaces.cli.licensing_commands import (
    _refuse_private_key_inside_project,
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def build_keys(tmp_path: Path) -> tuple[Path, Path]:
    private_key = tmp_path / "admin" / "license-private.pem"
    public_key = tmp_path / "configs" / "licensing" / "public-key.pem"
    generate_keypair(
        private_key_path=private_key,
        public_key_path=public_key,
    )
    return private_key, public_key


def signed_document(
    *,
    private_key_path: Path,
    device_code: str,
    now: datetime,
    expires_at: datetime | None = None,
    features: tuple[str, ...] = ("research", "reports"),
) -> dict[str, object]:
    return issue_license_document(
        private_key=load_private_key(private_key_path),
        customer_id="customer_001",
        plan="personal",
        device_code=device_code,
        issued_at=now,
        not_before=now - timedelta(days=1),
        expires_at=expires_at or now + timedelta(days=30),
        features=features,
    )


def test_signed_license_rejects_tampering(tmp_path: Path) -> None:
    private_key_path, public_key_path = build_keys(tmp_path)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    document = signed_document(
        private_key_path=private_key_path,
        device_code="QL-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE",
        now=now,
    )

    verified = verify_license_document(
        document, public_key=load_public_key(public_key_path)
    )

    assert verified.customer_id == "customer_001"
    assert verified.plan == "personal"
    tampered = json.loads(json.dumps(document))
    tampered["payload"]["plan"] = "pro"
    with pytest.raises(
        LicenseValidationError, match="签名无效或内容已被修改"
    ):
        verify_license_document(
            tampered, public_key=load_public_key(public_key_path)
        )


def test_commercial_license_import_binds_device_and_expires(tmp_path: Path) -> None:
    private_key_path, public_key_path = build_keys(tmp_path)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    clock = MutableClock(now)
    identity = "test-machine-a"
    service = OfflineLicenseService(
        tmp_path,
        enforcement_mode="commercial_required",
        public_key_path=public_key_path,
        device_identity_provider=lambda: identity,
        now_provider=clock,
    )
    document = signed_document(
        private_key_path=private_key_path,
        device_code=device_code_from_identity(identity),
        now=now,
    )

    missing = service.status()
    assert missing.state == "license_missing"
    assert missing.write_allowed is False

    active = service.import_license(json.dumps(document))
    assert active.state == "active"
    assert active.write_allowed is True
    assert active.plan == "personal"
    assert service.license_path.is_file()
    assert service.state_path.is_file()

    other_device = OfflineLicenseService(
        tmp_path,
        enforcement_mode="commercial_required",
        public_key_path=public_key_path,
        device_identity_provider=lambda: "test-machine-b",
        now_provider=clock,
    )
    mismatch = other_device.status(update_clock_state=False)
    assert mismatch.state == "device_mismatch"
    assert mismatch.write_allowed is False

    clock.value = now + timedelta(days=31)
    expired = service.status(update_clock_state=False)
    assert expired.state == "license_expired"
    assert expired.read_allowed is True
    assert expired.export_allowed is True
    assert expired.write_allowed is False


def test_license_without_research_feature_is_read_only(tmp_path: Path) -> None:
    private_key_path, public_key_path = build_keys(tmp_path)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    identity = "reports-only-device"
    service = OfflineLicenseService(
        tmp_path,
        enforcement_mode="commercial_required",
        public_key_path=public_key_path,
        device_identity_provider=lambda: identity,
        now_provider=lambda: now,
    )
    document = signed_document(
        private_key_path=private_key_path,
        device_code=device_code_from_identity(identity),
        now=now,
        features=("reports",),
    )

    with pytest.raises(LicenseValidationError, match="不包含新建研究权限"):
        service.import_license(json.dumps(document))


def test_clock_rollback_blocks_new_research(tmp_path: Path) -> None:
    private_key_path, public_key_path = build_keys(tmp_path)
    initial = datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc)
    clock = MutableClock(initial)
    identity = "clock-test-device"
    service = OfflineLicenseService(
        tmp_path,
        enforcement_mode="commercial_required",
        public_key_path=public_key_path,
        device_identity_provider=lambda: identity,
        now_provider=clock,
    )
    document = issue_license_document(
        private_key=load_private_key(private_key_path),
        customer_id="customer_clock",
        plan="personal",
        device_code=device_code_from_identity(identity),
        issued_at=initial - timedelta(days=10),
        not_before=initial - timedelta(days=10),
        expires_at=initial + timedelta(days=30),
        features=("research",),
    )
    assert service.import_license(json.dumps(document)).state == "active"

    clock.value = initial - timedelta(days=2)
    rolled_back = service.status(update_clock_state=False)

    assert rolled_back.state == "clock_rollback_detected"
    assert rolled_back.write_allowed is False


def test_api_enforces_read_only_until_license_is_imported(tmp_path: Path) -> None:
    private_key_path, public_key_path = build_keys(tmp_path)
    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    identity = "api-license-device"
    license_service = OfflineLicenseService(
        tmp_path,
        enforcement_mode="commercial_required",
        public_key_path=public_key_path,
        device_identity_provider=lambda: identity,
        now_provider=lambda: now,
    )
    app = create_app(
        root=tmp_path,
        database_path=tmp_path / "runtime/app/api.sqlite3",
        license_service=license_service,
    )
    client = TestClient(app)

    assert client.get("/api/license/status").json()["state"] == "license_missing"
    assert client.get("/api/research/sessions").status_code == 200
    blocked = client.post(
        "/api/research/sessions",
        json={"title": "blocked before activation"},
        headers={"Origin": "http://127.0.0.1:3100"},
    )
    assert blocked.status_code == 402
    assert blocked.json()["code"] == "license_missing"
    assert blocked.headers["access-control-allow-origin"] == (
        "http://127.0.0.1:3100"
    )

    document = signed_document(
        private_key_path=private_key_path,
        device_code=device_code_from_identity(identity),
        now=now,
    )
    imported = client.post(
        "/api/license/import",
        json={"license_document": json.dumps(document)},
    )
    assert imported.status_code == 200
    assert imported.json()["state"] == "active"

    created = client.post(
        "/api/research/sessions",
        json={"title": "allowed after activation"},
    )
    assert created.status_code == 201


def test_development_mode_remains_unrestricted(tmp_path: Path) -> None:
    service = OfflineLicenseService(
        tmp_path,
        enforcement_mode="development_disabled",
        device_identity_provider=lambda: "developer-machine",
    )
    app = create_app(
        root=tmp_path,
        database_path=tmp_path / "runtime/app/dev.sqlite3",
        license_service=service,
    )
    client = TestClient(app)

    status = client.get("/api/license/status").json()
    assert status["state"] == "development_unrestricted"
    assert status["write_allowed"] is True
    assert client.post(
        "/api/research/sessions", json={"title": "development"}
    ).status_code == 201


def test_unknown_enforcement_mode_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUANT_LAB_LICENSE_ENFORCEMENT", "requried")

    with pytest.raises(ValueError, match="未知授权模式"):
        OfflineLicenseService.from_environment(tmp_path)


def test_private_key_cli_refuses_project_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不能位于项目仓库内部"):
        _refuse_private_key_inside_project(
            tmp_path / "secrets" / "license-private.pem",
            tmp_path,
        )
    _refuse_private_key_inside_project(
        tmp_path.parent / "license-admin" / "license-private.pem",
        tmp_path,
    )


def test_device_code_does_not_expose_raw_identity() -> None:
    raw = "very-sensitive-machine-uuid"
    code = device_code_from_identity(raw)

    assert code.startswith("QL-")
    assert raw not in code
    assert len(code) == 32
