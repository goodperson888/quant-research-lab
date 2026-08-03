from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import uuid
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from quant_lab.domain.licensing import (
    LICENSE_PRODUCT_ID,
    LICENSE_SCHEMA_VERSION,
    LICENSE_SIGNATURE_ALGORITHM,
    LicensePayload,
    LicenseStatus,
    LicenseValidationError,
    format_utc_datetime,
    parse_utc_datetime,
)


DEFAULT_LICENSE_FEATURES = (
    "backtest",
    "batch_trials",
    "local_ai",
    "reports",
    "research",
)
COMMERCIAL_LICENSE_FILE = "commercial-license.qllicense"
COMMERCIAL_LICENSE_STATE_FILE = "commercial-license-state.json"
CLOCK_ROLLBACK_TOLERANCE = timedelta(hours=24)


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_signature(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_signature(value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise LicenseValidationError(
            "invalid_license_signature", "许可证签名格式无效"
        )
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise LicenseValidationError(
            "invalid_license_signature", "许可证签名格式无效"
        ) from exc


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise LicenseValidationError(
            "invalid_private_key", "无法读取 Ed25519 私钥"
        ) from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise LicenseValidationError(
            "invalid_private_key", "私钥类型必须是 Ed25519"
        )
    return loaded


def load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        loaded = serialization.load_pem_public_key(path.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise LicenseValidationError(
            "invalid_public_key", "无法读取 Ed25519 公钥"
        ) from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise LicenseValidationError(
            "invalid_public_key", "公钥类型必须是 Ed25519"
        )
    return loaded


def generate_keypair(*, private_key_path: Path, public_key_path: Path) -> None:
    private_key_path = private_key_path.expanduser().resolve()
    public_key_path = public_key_path.expanduser().resolve()
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.parent.mkdir(parents=True, exist_ok=True)
    if private_key_path.exists() or public_key_path.exists():
        raise FileExistsError("密钥文件已存在，拒绝覆盖")
    private_key = Ed25519PrivateKey.generate()
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_key_path, 0o600)
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    os.chmod(public_key_path, 0o644)


def public_key_fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def issue_license_document(
    *,
    private_key: Ed25519PrivateKey,
    customer_id: str,
    plan: str,
    device_code: str,
    expires_at: datetime,
    features: tuple[str, ...] = DEFAULT_LICENSE_FEATURES,
    issued_at: datetime | None = None,
    not_before: datetime | None = None,
    previous_license_id: str | None = None,
    license_id: str | None = None,
) -> dict[str, Any]:
    issued = (issued_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    valid_from = (not_before or issued).astimezone(timezone.utc)
    payload = LicensePayload.from_mapping(
        {
            "schema_version": LICENSE_SCHEMA_VERSION,
            "product_id": LICENSE_PRODUCT_ID,
            "license_id": license_id or f"lic_{uuid.uuid4().hex}",
            "customer_id": customer_id,
            "plan": plan,
            "device_code": device_code,
            "features": list(features),
            "issued_at": format_utc_datetime(issued),
            "not_before": format_utc_datetime(valid_from),
            "expires_at": format_utc_datetime(expires_at),
            "previous_license_id": previous_license_id,
        }
    )
    payload_dict = payload.as_dict()
    signature = private_key.sign(canonical_payload_bytes(payload_dict))
    return {
        "schema_version": LICENSE_SCHEMA_VERSION,
        "payload": payload_dict,
        "signature": {
            "algorithm": LICENSE_SIGNATURE_ALGORITHM,
            "value": _encode_signature(signature),
        },
    }


def verify_license_document(
    document: Mapping[str, Any], *, public_key: Ed25519PublicKey
) -> LicensePayload:
    if not isinstance(document, Mapping):
        raise LicenseValidationError(
            "invalid_license_document", "许可证文件必须是 JSON 对象"
        )
    if set(document) != {"schema_version", "payload", "signature"}:
        raise LicenseValidationError(
            "invalid_license_document", "许可证文件结构无效"
        )
    if document.get("schema_version") != LICENSE_SCHEMA_VERSION:
        raise LicenseValidationError(
            "unsupported_license_version", "许可证文件版本不受支持"
        )
    signature = document.get("signature")
    if not isinstance(signature, Mapping):
        raise LicenseValidationError(
            "invalid_license_signature", "许可证缺少签名"
        )
    if signature.get("algorithm") != LICENSE_SIGNATURE_ALGORITHM:
        raise LicenseValidationError(
            "invalid_license_signature", "许可证签名算法不受支持"
        )
    payload_raw = document.get("payload")
    if not isinstance(payload_raw, Mapping):
        raise LicenseValidationError(
            "invalid_license_payload", "许可证 payload 必须是对象"
        )
    payload = LicensePayload.from_mapping(payload_raw)
    try:
        public_key.verify(
            _decode_signature(signature.get("value")),
            canonical_payload_bytes(payload.as_dict()),
        )
    except InvalidSignature as exc:
        raise LicenseValidationError(
            "invalid_license_signature", "许可证签名无效或内容已被修改"
        ) from exc
    return payload


def device_code_from_identity(identity: str) -> str:
    if not identity.strip():
        raise ValueError("device identity cannot be empty")
    digest = hashlib.sha256(
        f"{LICENSE_PRODUCT_ID}:{identity.strip()}".encode("utf-8")
    ).hexdigest().upper()[:25]
    return "QL-" + "-".join(digest[index : index + 5] for index in range(0, 25, 5))


def local_device_identity() -> str:
    system = platform.system().lower()
    if system == "windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
            if machine_guid:
                return f"windows:{machine_guid}"
        except (ImportError, OSError):
            pass
    if system == "darwin":
        try:
            completed = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', completed.stdout)
            if match:
                return f"macos:{match.group(1)}"
        except (OSError, subprocess.SubprocessError):
            pass
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return f"linux:{value}"
    fallback = f"{system}:{platform.machine()}:{platform.node()}:{uuid.getnode()}"
    return "fallback:" + hashlib.sha256(fallback.encode("utf-8")).hexdigest()


class OfflineLicenseService:
    def __init__(
        self,
        root: Path,
        *,
        enforcement_mode: str = "development_disabled",
        public_key_path: Path | None = None,
        device_identity_provider: Callable[[], str] = local_device_identity,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if enforcement_mode not in {"development_disabled", "commercial_required"}:
            raise ValueError("invalid license enforcement mode")
        self.root = root.resolve()
        self.enforcement_mode = enforcement_mode
        self.public_key_path = (
            public_key_path.expanduser().resolve()
            if public_key_path is not None
            else self.root / "configs" / "licensing" / "public-key.pem"
        )
        self.license_path = (
            self.root / "runtime" / "app" / COMMERCIAL_LICENSE_FILE
        )
        self.state_path = (
            self.root / "runtime" / "app" / COMMERCIAL_LICENSE_STATE_FILE
        )
        self.device_identity_provider = device_identity_provider
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_environment(cls, root: Path) -> "OfflineLicenseService":
        raw_mode = os.environ.get(
            "QUANT_LAB_LICENSE_ENFORCEMENT", "development_disabled"
        ).strip().lower()
        if raw_mode in {"1", "true", "required", "commercial_required"}:
            mode = "commercial_required"
        elif raw_mode in {
            "0",
            "false",
            "disabled",
            "development",
            "development_disabled",
        }:
            mode = "development_disabled"
        else:
            raise ValueError(
                "QUANT_LAB_LICENSE_ENFORCEMENT 配置无效；"
                "商业构建拒绝在未知授权模式下启动"
            )
        configured_key = os.environ.get("QUANT_LAB_LICENSE_PUBLIC_KEY_PATH")
        return cls(
            root,
            enforcement_mode=mode,
            public_key_path=Path(configured_key) if configured_key else None,
        )

    @property
    def device_code(self) -> str:
        return device_code_from_identity(self.device_identity_provider())

    def status(self, *, update_clock_state: bool = True) -> LicenseStatus:
        if self.enforcement_mode == "development_disabled":
            return self._status(
                state="development_unrestricted",
                message="当前为开发模式，商业授权门禁未启用。",
                write_allowed=True,
                public_key_configured=self.public_key_path.is_file(),
                license_present=self.license_path.is_file(),
            )
        if not self.public_key_path.is_file():
            return self._status(
                state="license_system_misconfigured",
                message="商业版本缺少许可证公钥，请联系服务提供方。",
                write_allowed=False,
                public_key_configured=False,
                license_present=self.license_path.is_file(),
            )
        if not self.license_path.is_file():
            return self._status(
                state="license_missing",
                message="尚未激活。请把设备码发给服务提供方并导入许可证。",
                write_allowed=False,
                public_key_configured=True,
                license_present=False,
            )
        try:
            document = json.loads(self.license_path.read_text(encoding="utf-8"))
            payload = verify_license_document(
                document, public_key=load_public_key(self.public_key_path)
            )
            return self._evaluate_payload(
                payload, update_clock_state=update_clock_state
            )
        except json.JSONDecodeError:
            return self._status(
                state="invalid_license_document",
                message="许可证文件不是有效 JSON。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
            )
        except LicenseValidationError as exc:
            return self._status(
                state=exc.code,
                message=str(exc),
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
            )
        except OSError:
            return self._status(
                state="license_unreadable",
                message="许可证文件无法读取。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
            )

    def import_license(self, raw_document: str) -> LicenseStatus:
        if not self.public_key_path.is_file():
            raise LicenseValidationError(
                "license_system_misconfigured",
                "许可证公钥尚未配置，无法验证许可证。",
            )
        try:
            loaded = json.loads(raw_document)
        except json.JSONDecodeError as exc:
            raise LicenseValidationError(
                "invalid_license_document", "许可证文件不是有效 JSON"
            ) from exc
        payload = verify_license_document(
            loaded, public_key=load_public_key(self.public_key_path)
        )
        evaluated = self._evaluate_payload(payload, update_clock_state=False)
        if evaluated.state != "active":
            raise LicenseValidationError(evaluated.state, evaluated.message)
        serialized = json.dumps(
            loaded, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        self._atomic_write(self.license_path, serialized, mode=0o600)
        return self.status(update_clock_state=True)

    def _evaluate_payload(
        self, payload: LicensePayload, *, update_clock_state: bool
    ) -> LicenseStatus:
        now = self.now_provider().astimezone(timezone.utc)
        valid_from = parse_utc_datetime(payload.not_before, field_name="not_before")
        expires = parse_utc_datetime(payload.expires_at, field_name="expires_at")
        fields = {
            "license_id": payload.license_id,
            "customer_id": payload.customer_id,
            "plan": payload.plan,
            "features": payload.features,
            "issued_at": payload.issued_at,
            "not_before": payload.not_before,
            "expires_at": payload.expires_at,
            "days_remaining": max((expires.date() - now.date()).days, 0),
        }
        if payload.device_code != self.device_code:
            return self._status(
                state="device_mismatch",
                message="许可证与当前设备不匹配，请申请换机授权。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
                **fields,
            )
        if now < valid_from:
            return self._status(
                state="license_not_yet_valid",
                message="许可证尚未到生效时间，请检查系统时间。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
                **fields,
            )
        if now >= expires:
            return self._status(
                state="license_expired",
                message="许可证已到期。历史结果仍可查看和导出，请续费后导入新许可证。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
                **fields,
            )
        last_seen = self._last_seen_at()
        if last_seen is not None and now + CLOCK_ROLLBACK_TOLERANCE < last_seen:
            return self._status(
                state="clock_rollback_detected",
                message="检测到系统时间明显回退，已暂停新的研究操作。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
                **fields,
            )
        research_enabled = bool(
            {"research", "all"}.intersection(payload.features)
        )
        if not research_enabled:
            return self._status(
                state="active_read_only",
                message="许可证有效，但当前套餐不包含新建研究权限。",
                write_allowed=False,
                public_key_configured=True,
                license_present=True,
                **fields,
            )
        if update_clock_state:
            self._record_last_seen(now, payload.license_id)
        return self._status(
            state="active",
            message="许可证有效，可以在授权范围内进行本地研究。",
            write_allowed=True,
            public_key_configured=True,
            license_present=True,
            **fields,
        )

    def _last_seen_at(self) -> datetime | None:
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            return parse_utc_datetime(
                loaded.get("last_seen_at", ""), field_name="last_seen_at"
            )
        except (OSError, json.JSONDecodeError, LicenseValidationError, AttributeError):
            return None

    def _record_last_seen(self, now: datetime, license_id: str) -> None:
        current = self._last_seen_at()
        if current is not None and now <= current + timedelta(minutes=5):
            return
        payload = {
            "schema_version": 1,
            "license_id": license_id,
            "last_seen_at": format_utc_datetime(now),
        }
        self._atomic_write(
            self.state_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

    def _atomic_write(self, path: Path, content: str, *, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _status(
        self,
        *,
        state: str,
        message: str,
        write_allowed: bool,
        public_key_configured: bool,
        license_present: bool,
        license_id: str | None = None,
        customer_id: str | None = None,
        plan: str | None = None,
        features: tuple[str, ...] = (),
        issued_at: str | None = None,
        not_before: str | None = None,
        expires_at: str | None = None,
        days_remaining: int | None = None,
    ) -> LicenseStatus:
        return LicenseStatus(
            enforcement_mode=self.enforcement_mode,
            state=state,
            message=message,
            read_allowed=True,
            export_allowed=True,
            write_allowed=write_allowed,
            device_code=self.device_code,
            public_key_configured=public_key_configured,
            license_present=license_present,
            license_id=license_id,
            customer_id=customer_id,
            plan=plan,
            features=features,
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            days_remaining=days_remaining,
        )
