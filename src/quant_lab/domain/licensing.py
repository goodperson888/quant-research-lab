from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping


LICENSE_SCHEMA_VERSION = 1
LICENSE_PRODUCT_ID = "quant-research-lab"
LICENSE_SIGNATURE_ALGORITHM = "ed25519"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_SAFE_FEATURE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class LicenseValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_utc_datetime(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LicenseValidationError(
            "invalid_license_payload", f"{field_name} 必须是 UTC 时间"
        )
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LicenseValidationError(
            "invalid_license_payload", f"{field_name} 不是有效时间"
        ) from exc
    if parsed.tzinfo is None:
        raise LicenseValidationError(
            "invalid_license_payload", f"{field_name} 必须包含时区"
        )
    return parsed.astimezone(timezone.utc)


def format_utc_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC datetime must include timezone")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _identifier(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise LicenseValidationError(
            "invalid_license_payload",
            f"{field_name} 只能包含字母、数字和 ._:@-",
        )
    return value


@dataclass(frozen=True)
class LicensePayload:
    schema_version: int
    product_id: str
    license_id: str
    customer_id: str
    plan: str
    device_code: str
    features: tuple[str, ...]
    issued_at: str
    not_before: str
    expires_at: str
    previous_license_id: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "LicensePayload":
        if not isinstance(value, Mapping):
            raise LicenseValidationError(
                "invalid_license_payload", "许可证 payload 必须是对象"
            )
        allowed = {
            "schema_version",
            "product_id",
            "license_id",
            "customer_id",
            "plan",
            "device_code",
            "features",
            "issued_at",
            "not_before",
            "expires_at",
            "previous_license_id",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise LicenseValidationError(
                "invalid_license_payload",
                "许可证包含未知字段：" + "、".join(unknown),
            )
        if value.get("schema_version") != LICENSE_SCHEMA_VERSION:
            raise LicenseValidationError(
                "unsupported_license_version", "许可证版本不受支持"
            )
        if value.get("product_id") != LICENSE_PRODUCT_ID:
            raise LicenseValidationError(
                "wrong_product", "许可证不属于本产品"
            )
        features_raw = value.get("features")
        if not isinstance(features_raw, list) or not features_raw:
            raise LicenseValidationError(
                "invalid_license_payload", "许可证必须包含至少一个功能权限"
            )
        features: list[str] = []
        for feature in features_raw:
            if not isinstance(feature, str) or not _SAFE_FEATURE.fullmatch(feature):
                raise LicenseValidationError(
                    "invalid_license_payload", "许可证功能权限格式无效"
                )
            if feature not in features:
                features.append(feature)
        issued_at = str(value.get("issued_at", ""))
        not_before = str(value.get("not_before", ""))
        expires_at = str(value.get("expires_at", ""))
        issued = parse_utc_datetime(issued_at, field_name="issued_at")
        valid_from = parse_utc_datetime(not_before, field_name="not_before")
        expires = parse_utc_datetime(expires_at, field_name="expires_at")
        if valid_from > expires:
            raise LicenseValidationError(
                "invalid_license_payload", "许可证生效时间晚于到期时间"
            )
        if issued > expires:
            raise LicenseValidationError(
                "invalid_license_payload", "许可证签发时间晚于到期时间"
            )
        previous = value.get("previous_license_id")
        if previous is not None:
            previous = _identifier(previous, field_name="previous_license_id")
        return cls(
            schema_version=LICENSE_SCHEMA_VERSION,
            product_id=LICENSE_PRODUCT_ID,
            license_id=_identifier(value.get("license_id"), field_name="license_id"),
            customer_id=_identifier(
                value.get("customer_id"), field_name="customer_id"
            ),
            plan=_identifier(value.get("plan"), field_name="plan"),
            device_code=_identifier(
                value.get("device_code"), field_name="device_code"
            ),
            features=tuple(sorted(features)),
            issued_at=format_utc_datetime(issued),
            not_before=format_utc_datetime(valid_from),
            expires_at=format_utc_datetime(expires),
            previous_license_id=previous,
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "product_id": self.product_id,
            "license_id": self.license_id,
            "customer_id": self.customer_id,
            "plan": self.plan,
            "device_code": self.device_code,
            "features": list(self.features),
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
        }
        if self.previous_license_id is not None:
            result["previous_license_id"] = self.previous_license_id
        return result


@dataclass(frozen=True)
class LicenseStatus:
    enforcement_mode: str
    state: str
    message: str
    read_allowed: bool
    export_allowed: bool
    write_allowed: bool
    device_code: str
    public_key_configured: bool
    license_present: bool
    license_id: str | None = None
    customer_id: str | None = None
    plan: str | None = None
    features: tuple[str, ...] = ()
    issued_at: str | None = None
    not_before: str | None = None
    expires_at: str | None = None
    days_remaining: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enforcement_mode": self.enforcement_mode,
            "state": self.state,
            "message": self.message,
            "read_allowed": self.read_allowed,
            "export_allowed": self.export_allowed,
            "write_allowed": self.write_allowed,
            "device_code": self.device_code,
            "public_key_configured": self.public_key_configured,
            "license_present": self.license_present,
            "license_id": self.license_id,
            "customer_id": self.customer_id,
            "plan": self.plan,
            "features": list(self.features),
            "issued_at": self.issued_at,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "days_remaining": self.days_remaining,
        }
