from __future__ import annotations

import json
from pathlib import Path

import quant_lab.commercial_runtime as commercial_runtime
from quant_lab.commercial_runtime import (
    bundle_contents_dir,
    configure_environment,
    seed_project_home,
)


def build_fake_bundle(tmp_path: Path) -> tuple[Path, Path]:
    contents = tmp_path / "Quant Research Lab.app" / "Contents"
    resources = contents / "Resources"
    (resources / "licensing").mkdir(parents=True)
    (resources / "licensing" / "public-key.pem").write_text(
        "test-public-key",
        encoding="utf-8",
    )
    template = resources / "project-template"
    (template / "configs" / "workers").mkdir(parents=True)
    (template / "configs" / "workers" / "local.yaml").write_text(
        "schema_version: 1\n",
        encoding="utf-8",
    )
    (resources / "build-manifest.json").write_text(
        json.dumps({"schema_version": 1, "version": "test-build"}),
        encoding="utf-8",
    )
    return contents, resources


def test_commercial_runtime_forces_license_gate(
    tmp_path: Path, monkeypatch
) -> None:
    _contents, resources = build_fake_bundle(tmp_path)
    data_home = tmp_path / "customer-data"
    monkeypatch.setenv("QUANT_LAB_LICENSE_ENFORCEMENT", "development_disabled")
    monkeypatch.setenv("QUANT_LAB_LICENSE_PUBLIC_KEY_PATH", "/tmp/other.pem")

    env = configure_environment(
        data_home=data_home,
        resources=resources,
        api_port=18100,
        web_port=13100,
    )

    assert env["QUANT_LAB_LICENSE_ENFORCEMENT"] == "commercial_required"
    assert env["QUANT_LAB_LICENSE_PUBLIC_KEY_PATH"] == str(
        resources / "licensing" / "public-key.pem"
    )
    assert env["QUANT_LAB_HOME"] == str(data_home)
    assert env["NEXT_PUBLIC_QUANT_LAB_API_URL"] == "http://127.0.0.1:18100"


def test_first_launch_seeds_customer_home_without_overwriting(
    tmp_path: Path,
) -> None:
    _contents, resources = build_fake_bundle(tmp_path)
    data_home = tmp_path / "customer-data"

    seed_project_home(data_home, resources)
    worker_config = data_home / "configs" / "workers" / "local.yaml"
    worker_config.write_text("customer_override: true\n", encoding="utf-8")
    (
        resources
        / "project-template"
        / "configs"
        / "workers"
        / "local.yaml"
    ).write_text("template_update: true\n", encoding="utf-8")

    seed_project_home(data_home, resources)

    assert worker_config.read_text(encoding="utf-8") == (
        "customer_override: true\n"
    )
    assert (data_home / "runtime" / "app" / "quant_lab.sqlite3").is_file()
    assert (data_home / "factor_library" / "registry.sqlite3").is_file()
    assert json.loads(
        (data_home / "runtime" / "product-build.json").read_text(
            encoding="utf-8"
        )
    )["version"] == "test-build"


def test_bundle_contents_can_be_injected_for_packaged_qa(
    tmp_path: Path, monkeypatch
) -> None:
    contents, _resources = build_fake_bundle(tmp_path)
    monkeypatch.setenv("QUANT_LAB_BUNDLE_CONTENTS", str(contents))

    assert bundle_contents_dir() == contents.resolve()


def test_stop_uses_cross_platform_request_file(
    tmp_path: Path, monkeypatch
) -> None:
    data_home = tmp_path / "customer-data"
    state_path = data_home / commercial_runtime.STATE_FILE
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "supervisor_pid": 12345,
                "executable": str(Path(commercial_runtime.sys.executable).resolve()),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(commercial_runtime, "_process_matches", lambda *_args: True)
    alive = iter((True, False))
    monkeypatch.setattr(
        commercial_runtime,
        "_pid_alive",
        lambda _pid: next(alive, False),
    )
    written: list[Path] = []
    original_write = commercial_runtime._atomic_json_write

    def tracking_write(path: Path, payload: dict[str, object]) -> None:
        written.append(path)
        original_write(path, payload)

    monkeypatch.setattr(
        commercial_runtime,
        "_atomic_json_write",
        tracking_write,
    )

    assert commercial_runtime._stop_from_state(data_home) == 0
    assert written == [data_home / commercial_runtime.STOP_REQUEST_FILE]
    assert not (data_home / commercial_runtime.STOP_REQUEST_FILE).exists()
