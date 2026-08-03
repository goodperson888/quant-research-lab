from __future__ import annotations

import importlib.util
from pathlib import Path
import zipfile

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_commercial_windows.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_commercial_windows",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
WINDOWS_BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WINDOWS_BUILD)

MACOS_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_commercial_macos.py"
)
MACOS_SPEC = importlib.util.spec_from_file_location(
    "build_commercial_macos",
    MACOS_SCRIPT_PATH,
)
assert MACOS_SPEC is not None and MACOS_SPEC.loader is not None
MACOS_BUILD = importlib.util.module_from_spec(MACOS_SPEC)
MACOS_SPEC.loader.exec_module(MACOS_BUILD)


def test_windows_packaging_version_is_filename_safe() -> None:
    assert WINDOWS_BUILD.safe_version("0.1.0 beta+1") == "0.1.0-beta-1"
    with pytest.raises(WINDOWS_BUILD.BuildError):
        WINDOWS_BUILD.safe_version("../")


def test_windows_customer_image_rejects_private_or_customer_data(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image"
    image.mkdir()
    public_key = image / "Resources" / "licensing" / "public-key.pem"
    public_key.parent.mkdir(parents=True)
    public_key.write_text("public", encoding="utf-8")
    WINDOWS_BUILD.validate_customer_image(image)

    private_key = image / "quant-lab.license-private.pem"
    private_key.write_text("private", encoding="utf-8")
    with pytest.raises(WINDOWS_BUILD.BuildError):
        WINDOWS_BUILD.validate_customer_image(image)

    private_key.unlink()
    (image / "customer.sqlite3").write_bytes(b"sqlite")
    with pytest.raises(WINDOWS_BUILD.BuildError):
        WINDOWS_BUILD.validate_customer_image(image)


def test_windows_portable_zip_uses_relative_customer_paths(
    tmp_path: Path,
) -> None:
    image = tmp_path / "image"
    app = image / WINDOWS_BUILD.APP_NAME
    app.mkdir(parents=True)
    (app / WINDOWS_BUILD.EXECUTABLE_NAME).write_bytes(b"binary")
    output = tmp_path / "output"
    output.mkdir()

    archive = WINDOWS_BUILD.create_portable_zip(
        image_root=image,
        output_dir=output,
        version="0.1.0",
    )

    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == [
            f"{WINDOWS_BUILD.APP_NAME}/{WINDOWS_BUILD.EXECUTABLE_NAME}"
        ]


def test_windows_installer_definition_is_user_scoped_and_stoppable(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "image"
    app = image / WINDOWS_BUILD.APP_NAME
    app.mkdir(parents=True)
    output = tmp_path / "output"
    output.mkdir()
    build = tmp_path / "build"
    build.mkdir()

    monkeypatch.setattr(
        WINDOWS_BUILD,
        "find_inno_compiler",
        lambda: Path("C:/Inno/ISCC.exe"),
    )

    def fake_compile(_command: list[str]) -> None:
        (
            output
            / "QuantResearchLab-0.1.0-windows-x64-setup.exe"
        ).write_bytes(b"installer")

    monkeypatch.setattr(WINDOWS_BUILD, "run", fake_compile)

    installer = WINDOWS_BUILD.create_installer(
        app_dir=app,
        image_root=image,
        build_root=build,
        output_dir=output,
        version="0.1.0",
    )
    definition = (build / "QuantResearchLab.iss").read_text(
        encoding="utf-8-sig"
    )

    assert installer.is_file()
    assert "DefaultDirName={localappdata}\\Programs\\Quant Research Lab" in definition
    assert "PrivilegesRequired=lowest" in definition
    assert "Parameters: \"--stop\"" in definition
    assert WINDOWS_BUILD.INNO_APP_ID in definition


def test_macos_adhoc_signing_does_not_enable_hardened_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        MACOS_BUILD,
        "run",
        lambda command: commands.append(command),
    )

    MACOS_BUILD.codesign_app(tmp_path / "Quant Research Lab.app", sign_identity="-")

    assert "--options" not in commands[0]
    assert "--timestamp" not in commands[0]
    assert commands[0][commands[0].index("--sign") + 1] == "-"


def test_macos_developer_signing_enables_hardened_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        MACOS_BUILD,
        "run",
        lambda command: commands.append(command),
    )

    MACOS_BUILD.codesign_app(
        tmp_path / "Quant Research Lab.app",
        sign_identity="Developer ID Application: Example",
    )

    assert commands[0][commands[0].index("--options") + 1] == "runtime"
    assert "--timestamp" in commands[0]
