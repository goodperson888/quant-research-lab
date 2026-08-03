from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from quant_lab.domain.licensing import (
    LicenseValidationError,
    format_utc_datetime,
    parse_utc_datetime,
)
from quant_lab.infrastructure.offline_licensing import (
    DEFAULT_LICENSE_FEATURES,
    OfflineLicenseService,
    generate_keypair,
    issue_license_document,
    load_private_key,
    load_public_key,
    public_key_fingerprint,
    verify_license_document,
)


def add_licensing_parsers(subparsers: Any) -> None:
    keygen = subparsers.add_parser("license-keygen")
    keygen.add_argument("--private-key", required=True)
    keygen.add_argument("--public-key", required=True)

    device = subparsers.add_parser("license-device-code")
    device.add_argument("--json", action="store_true")

    issue = subparsers.add_parser("license-issue")
    issue.add_argument("--private-key", required=True)
    issue.add_argument("--customer-id", required=True)
    issue.add_argument("--plan", required=True)
    issue.add_argument("--device-code", required=True)
    issue.add_argument("--days", type=int)
    issue.add_argument("--expires-at")
    issue.add_argument("--feature", action="append", dest="features")
    issue.add_argument("--output", required=True)

    renew = subparsers.add_parser("license-renew")
    renew.add_argument("--private-key", required=True)
    renew.add_argument("--license", required=True)
    renew.add_argument("--days", type=int, required=True)
    renew.add_argument("--output", required=True)

    inspect = subparsers.add_parser("license-inspect")
    inspect.add_argument("--license", required=True)
    inspect.add_argument("--public-key", required=True)


def handle_licensing_command(
    args: argparse.Namespace, *, root: Path
) -> int | None:
    if args.command == "license-keygen":
        private_path = Path(args.private_key).expanduser().resolve()
        _refuse_private_key_inside_project(private_path, root)
        public_path = Path(args.public_key).expanduser().resolve()
        generate_keypair(
            private_key_path=private_path,
            public_key_path=public_path,
        )
        print(
            json.dumps(
                {
                    "private_key": str(private_path),
                    "public_key": str(public_path),
                    "public_key_fingerprint": public_key_fingerprint(
                        load_public_key(public_path)
                    ),
                    "warning": "私钥不得复制到项目、安装包、网盘或 Git。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "license-device-code":
        status = OfflineLicenseService(root).status(update_clock_state=False)
        if args.json:
            print(
                json.dumps(
                    {"device_code": status.device_code},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(status.device_code)
        return 0
    if args.command == "license-issue":
        private_key_path = Path(args.private_key).expanduser().resolve()
        _refuse_private_key_inside_project(private_key_path, root)
        expires_at = _resolve_expiration(
            days=args.days, expires_at=args.expires_at
        )
        document = issue_license_document(
            private_key=load_private_key(private_key_path),
            customer_id=args.customer_id,
            plan=args.plan,
            device_code=args.device_code,
            expires_at=expires_at,
            features=tuple(args.features or DEFAULT_LICENSE_FEATURES),
        )
        _write_license(Path(args.output), document)
        _print_issue_summary(document, Path(args.output))
        return 0
    if args.command == "license-renew":
        if args.days < 1:
            raise ValueError("--days 必须大于 0")
        private_key_path = Path(args.private_key).expanduser().resolve()
        _refuse_private_key_inside_project(private_key_path, root)
        private_key = load_private_key(private_key_path)
        source = json.loads(Path(args.license).expanduser().read_text(encoding="utf-8"))
        payload = verify_license_document(
            source, public_key=private_key.public_key()
        )
        current_expiration = parse_utc_datetime(
            payload.expires_at, field_name="expires_at"
        )
        now = datetime.now(timezone.utc)
        base = max(current_expiration, now)
        document = issue_license_document(
            private_key=private_key,
            customer_id=payload.customer_id,
            plan=payload.plan,
            device_code=payload.device_code,
            expires_at=base + timedelta(days=args.days),
            features=payload.features,
            previous_license_id=payload.license_id,
        )
        _write_license(Path(args.output), document)
        _print_issue_summary(document, Path(args.output))
        return 0
    if args.command == "license-inspect":
        loaded = json.loads(
            Path(args.license).expanduser().read_text(encoding="utf-8")
        )
        payload = verify_license_document(
            loaded,
            public_key=load_public_key(Path(args.public_key).expanduser()),
        )
        print(
            json.dumps(
                {
                    "valid_signature": True,
                    "payload": payload.as_dict(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return None


def _resolve_expiration(*, days: int | None, expires_at: str | None) -> datetime:
    if (days is None) == (expires_at is None):
        raise ValueError("--days 和 --expires-at 必须且只能提供一个")
    if days is not None:
        if days < 1:
            raise ValueError("--days 必须大于 0")
        return datetime.now(timezone.utc) + timedelta(days=days)
    return parse_utc_datetime(str(expires_at), field_name="expires_at")


def _refuse_private_key_inside_project(path: Path, root: Path) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return
    raise ValueError("私钥路径不能位于项目仓库内部")


def _write_license(path: Path, document: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists():
        raise FileExistsError("许可证输出文件已存在，拒绝覆盖")
    resolved.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _print_issue_summary(document: dict[str, Any], output: Path) -> None:
    payload = document["payload"]
    print(
        json.dumps(
            {
                "output": str(output.expanduser().resolve()),
                "license_id": payload["license_id"],
                "customer_id": payload["customer_id"],
                "plan": payload["plan"],
                "device_code": payload["device_code"],
                "expires_at": payload["expires_at"],
                "features": payload["features"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
