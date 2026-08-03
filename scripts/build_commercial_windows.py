from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "apps" / "web"
DEFAULT_OUTPUT = ROOT / "dist" / "commercial"
APP_NAME = "Quant Research Lab"
EXECUTABLE_NAME = "QuantResearchLab.exe"
NODE_VERSION = "24.10.0"
NODE_ARCHIVE = f"node-v{NODE_VERSION}-win-x64.zip"
NODE_SHA256 = "adc1a2d5ca79c92e94f3a58c3ec0efa76bdb488769ba4d4b50990e4c84896060"
INNO_APP_ID = "{{A7DF3B98-695D-4D93-B2BF-3D9A6E0E8E83}"

TEMPLATE_CONFIG_DIRS = (
    "agent_policies",
    "agents",
    "data_downloads",
    "execution_models",
    "market_profiles",
    "pipelines",
    "regimes",
    "research_authorizations",
    "research_budgets",
    "validation",
    "workers",
)
TEMPLATE_CONFIG_FILES = (
    "freqtrade.example.json",
    "lab.example.json",
    "lab.json",
    "storage-policy.yaml",
    "strategy-intake.example.yaml",
    "versioning-policy.yaml",
)


class BuildError(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def capture(command: list[str], *, cwd: Path = ROOT) -> str:
    return subprocess.check_output(
        command,
        cwd=cwd,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def safe_version(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    if not normalized or normalized.startswith(("-", ".")):
        raise BuildError("版本号格式无效")
    return normalized[:80]


def source_dirty() -> bool:
    return bool(capture(["git", "status", "--porcelain"]))


def project_version() -> str:
    import tomllib

    loaded = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(loaded["project"]["version"])


def load_public_key(path: Path) -> tuple[Ed25519PublicKey, str]:
    resolved = path.expanduser().resolve()
    try:
        loaded = serialization.load_pem_public_key(resolved.read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise BuildError("无法读取 Ed25519 商业授权公钥") from exc
    if not isinstance(loaded, Ed25519PublicKey):
        raise BuildError("商业授权公钥类型必须是 Ed25519")
    raw = loaded.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return loaded, "sha256:" + hashlib.sha256(raw).hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise BuildError(f"缺少目录：{source}")
    shutil.copytree(source, destination, dirs_exist_ok=True)


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise BuildError(f"缺少文件：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def prepare_project_template(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    copy_file(ROOT / "AGENTS.md", destination / "AGENTS.md")
    copy_file(ROOT / "README.md", destination / "README.md")
    copy_tree(ROOT / "docs", destination / "docs")
    copy_tree(
        ROOT / ".agents" / "skills" / "quant-strategy-research",
        destination / ".agents" / "skills" / "quant-strategy-research",
    )
    for directory in TEMPLATE_CONFIG_DIRS:
        copy_tree(
            ROOT / "configs" / directory,
            destination / "configs" / directory,
        )
    for filename in TEMPLATE_CONFIG_FILES:
        copy_file(
            ROOT / "configs" / filename,
            destination / "configs" / filename,
        )
    copy_file(
        ROOT / "configs" / "licensing" / "README.md",
        destination / "configs" / "licensing" / "README.md",
    )
    for relative in (
        "data/README.md",
        "data/catalog/README.md",
        "factor_library/README.md",
        "factor_library/schema.example.json",
        "strategies/README.md",
    ):
        copy_file(ROOT / relative, destination / relative)


def build_web(resources: Path, *, skip_npm_ci: bool) -> None:
    env = os.environ.copy()
    env.update(
        {
            "NODE_ENV": "production",
            "NEXT_PUBLIC_QUANT_LAB_API_URL": "http://127.0.0.1:8100",
        }
    )
    if not skip_npm_ci:
        run(["npm.cmd", "ci"], cwd=WEB_ROOT, env=env)
    run(["npm.cmd", "run", "build"], cwd=WEB_ROOT, env=env)
    standalone = WEB_ROOT / ".next" / "standalone"
    server = standalone / "server.js"
    if not server.is_file():
        candidates = list(standalone.rglob("server.js"))
        if len(candidates) != 1:
            raise BuildError("Next standalone server.js 未生成或位置不唯一")
        server = candidates[0]
        if server.parent != standalone:
            raise BuildError(
                "Next standalone 输出不是可直接嵌入的单根目录；"
                "请检查 outputFileTracingRoot"
            )
    web_destination = resources / "web"
    copy_tree(standalone, web_destination)
    static_dir = WEB_ROOT / ".next" / "static"
    if static_dir.is_dir():
        copy_tree(static_dir, web_destination / ".next" / "static")
    public_dir = WEB_ROOT / "public"
    if public_dir.is_dir():
        copy_tree(public_dir, web_destination / "public")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_node(resources: Path) -> None:
    cache_dir = ROOT / ".cache" / "commercial" / "node"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / NODE_ARCHIVE
    if not archive.is_file() or sha256_file(archive) != NODE_SHA256:
        archive.unlink(missing_ok=True)
        partial = archive.with_suffix(archive.suffix + ".partial")
        partial.unlink(missing_ok=True)
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/{NODE_ARCHIVE}"
        print(f"下载固定 Node 运行时：{url}", flush=True)
        run(
            [
                "curl.exe",
                "--fail",
                "--location",
                "--retry",
                "5",
                "--retry-all-errors",
                "--connect-timeout",
                "30",
                "--max-time",
                "600",
                "--output",
                str(partial),
                url,
            ]
        )
        os.replace(partial, archive)
    actual = sha256_file(archive)
    if actual != NODE_SHA256:
        raise BuildError(
            f"Node 运行时校验失败：expected={NODE_SHA256} actual={actual}"
        )
    with tempfile.TemporaryDirectory(prefix="quant-node-") as temporary:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temporary)
        extracted = Path(temporary) / NODE_ARCHIVE.removesuffix(".zip")
        copy_file(
            extracted / "node.exe",
            resources / "node" / "bin" / "node.exe",
        )
        copy_file(extracted / "LICENSE", resources / "node" / "LICENSE")


def build_backend(app_dir: Path, build_root: Path, *, jobs: int) -> None:
    output = build_root / "nuitka"
    env = os.environ.copy()
    env["NUITKA_CACHE_DIR"] = str(ROOT / ".cache" / "commercial" / "nuitka")
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=standalone",
        f"--jobs={jobs}",
        "--assume-yes-for-downloads",
        "--remove-output",
        "--include-package=quant_lab",
        "--nofollow-import-to=freqtrade",
        "--windows-console-mode=attach",
        f"--output-dir={output}",
        f"--output-filename={EXECUTABLE_NAME}",
        str(ROOT / "src" / "quant_lab" / "commercial_runtime.py"),
    ]
    run(command, env=env)
    distributions = list(output.glob("*.dist"))
    if len(distributions) != 1:
        raise BuildError("Nuitka standalone 输出目录数量不正确")
    copy_tree(distributions[0], app_dir)
    if not (app_dir / EXECUTABLE_NAME).is_file():
        raise BuildError("Nuitka 未生成 Windows 商业运行主程序")


def write_build_manifest(
    resources: Path,
    *,
    version: str,
    public_key_fingerprint: str,
    dirty: bool,
) -> None:
    payload = {
        "schema_version": 1,
        "product": "quant-research-lab",
        "version": version,
        "git_revision": capture(["git", "rev-parse", "HEAD"]),
        "source_dirty": dirty,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "platform": "windows",
        "architecture": "x64",
        "license_enforcement": "commercial_required",
        "license_public_key_fingerprint": public_key_fingerprint,
        "frontend_api_url": "http://127.0.0.1:8100",
        "customer_data_home": r"%LOCALAPPDATA%\QuantResearchLab",
        "live_trading_enabled": False,
    }
    (resources / "build-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_customer_files(image_root: Path, app_dir: Path) -> None:
    instructions = r"""Quant Research Lab 商业安装包（Windows x64）

1. 推荐运行 QuantResearchLab-*-windows-x64-setup.exe 完成安装。
2. 安装后从开始菜单或桌面启动 Quant Research Lab。
3. 浏览器会自动打开本地研究工作台。
4. 第一次启动会在“本地设置”显示设备码。
5. 把设备码发给服务提供方，收到 .qllicense 后在设置页导入。
6. 客户数据保存在：
   %LOCALAPPDATA%\QuantResearchLab

升级或卸载前请先退出 Quant Research Lab。卸载不会删除客户研究数据。
许可证到期后历史研究仍可查看和导出。
本版本不包含实盘交易接口，也不捆绑 Freqtrade。

若 Windows 显示“未知发布者”或 SmartScreen 警告，说明本 MVP 尚未使用商业
Windows 代码签名证书；正式大规模发行前应完成 Authenticode 签名。
"""
    (image_root / "安装与激活说明.txt").write_text(
        instructions,
        encoding="utf-8-sig",
    )
    stop_script = (
        "@echo off\r\n"
        f'"%~dp0{EXECUTABLE_NAME}" --stop\r\n'
        "if errorlevel 1 pause\r\n"
    )
    (app_dir / "停止 Quant Research Lab.cmd").write_text(
        stop_script,
        encoding="utf-8-sig",
    )


def validate_customer_image(image_root: Path) -> None:
    forbidden_suffixes = {
        ".qllicense",
        ".sqlite",
        ".sqlite3",
        ".duckdb",
        ".parquet",
    }
    violations: list[str] = []
    for path in image_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(image_root).as_posix()
        lowered = path.name.lower()
        if path.suffix.lower() in forbidden_suffixes:
            violations.append(relative)
        if "license-private" in lowered or lowered.endswith(".private.pem"):
            violations.append(relative)
        if (
            path.suffix.lower() == ".py"
            and "/Resources/project-template/" in f"/{relative}"
        ):
            violations.append(relative)
    if violations:
        raise BuildError(
            "客户安装包包含禁止分发的源码、数据或授权文件："
            + "、".join(sorted(set(violations))[:20])
        )


def create_portable_zip(
    *,
    image_root: Path,
    output_dir: Path,
    version: str,
) -> Path:
    base = f"QuantResearchLab-{version}-windows-x64"
    zip_path = output_dir / f"{base}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(image_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(image_root))
    return zip_path


def find_inno_compiler() -> Path:
    discovered = shutil.which("ISCC.exe") or shutil.which("iscc")
    candidates = [
        Path(discovered) if discovered else None,
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
        / "Inno Setup 6"
        / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        / "Inno Setup 6"
        / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise BuildError("未找到 Inno Setup 6（ISCC.exe）")


def _inno_quote(value: str) -> str:
    return value.replace('"', '""')


def create_installer(
    *,
    app_dir: Path,
    image_root: Path,
    build_root: Path,
    output_dir: Path,
    version: str,
) -> Path:
    base = f"QuantResearchLab-{version}-windows-x64"
    script_path = build_root / "QuantResearchLab.iss"
    source_pattern = _inno_quote(str(app_dir / "*"))
    instructions = _inno_quote(str(image_root / "安装与激活说明.txt"))
    output = _inno_quote(str(output_dir))
    script_path.write_text(
        f"""[Setup]
AppId={INNO_APP_ID}
AppName={APP_NAME}
AppVersion={version}
AppPublisher=Quant Research Lab
DefaultDirName={{localappdata}}\\Programs\\Quant Research Lab
DefaultGroupName={APP_NAME}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir={output}
OutputBaseFilename={base}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={{app}}\\{EXECUTABLE_NAME}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

[Files]
Source: "{source_pattern}"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{instructions}"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{autoprograms}}\\{APP_NAME}"; Filename: "{{app}}\\{EXECUTABLE_NAME}"
Name: "{{autodesktop}}\\{APP_NAME}"; Filename: "{{app}}\\{EXECUTABLE_NAME}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{EXECUTABLE_NAME}"; Description: "启动 {APP_NAME}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{{app}}\\{EXECUTABLE_NAME}"; Parameters: "--stop"; Flags: runhidden waituntilterminated skipifdoesntexist
""",
        encoding="utf-8-sig",
    )
    run([str(find_inno_compiler()), str(script_path)])
    installer = output_dir / f"{base}-setup.exe"
    if not installer.is_file():
        raise BuildError("Inno Setup 未生成 Windows 安装程序")
    return installer


def write_checksums(
    *,
    output_dir: Path,
    version: str,
    artifacts: list[Path],
) -> Path:
    checksum_path = (
        output_dir / f"QuantResearchLab-{version}-windows-x64.sha256"
    )
    checksum_path.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in artifacts),
        encoding="utf-8",
    )
    return checksum_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建 Quant Research Lab Windows x64 商业安装包"
    )
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--jobs", type=int, default=max(1, min(os.cpu_count() or 1, 4)))
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--skip-npm-ci", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--keep-build", action="store_true")
    args = parser.parse_args()

    if platform.system() != "Windows":
        raise BuildError("Windows 商业包必须在 Windows x64 构建机上生成")
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise BuildError(f"暂不支持的 Windows 架构：{platform.machine()}")
    version = safe_version(args.version)
    dirty = source_dirty()
    if dirty and not args.allow_dirty:
        raise BuildError(
            "工作区存在未提交修改；正式商业包要求干净提交。"
            "本地 QA 可显式使用 --allow-dirty"
        )
    public_key_path = Path(args.public_key).expanduser().resolve()
    _public_key, fingerprint = load_public_key(public_key_path)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    build_root = output_dir / "build" / "windows-x64"
    image_root = build_root / "image"
    app_dir = image_root / APP_NAME
    if build_root.exists():
        shutil.rmtree(build_root)
    resources = app_dir / "Resources"
    resources.mkdir(parents=True, exist_ok=True)

    copy_file(public_key_path, resources / "licensing" / "public-key.pem")
    prepare_project_template(resources / "project-template")
    write_build_manifest(
        resources,
        version=version,
        public_key_fingerprint=fingerprint,
        dirty=dirty,
    )
    build_web(resources, skip_npm_ci=args.skip_npm_ci)
    download_node(resources)
    build_backend(app_dir, build_root, jobs=args.jobs)
    write_customer_files(image_root, app_dir)
    validate_customer_image(image_root)

    artifacts = [
        create_portable_zip(
            image_root=image_root,
            output_dir=output_dir,
            version=version,
        )
    ]
    if not args.skip_installer:
        artifacts.append(
            create_installer(
                app_dir=app_dir,
                image_root=image_root,
                build_root=build_root,
                output_dir=output_dir,
                version=version,
            )
        )
    checksum = write_checksums(
        output_dir=output_dir,
        version=version,
        artifacts=artifacts,
    )
    artifacts.append(checksum)
    print(
        json.dumps(
            {
                "status": "built",
                "version": version,
                "architecture": "x64",
                "source_dirty": dirty,
                "public_key_fingerprint": fingerprint,
                "artifacts": [
                    {
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                    for path in artifacts
                ],
                "customer_data_home": r"%LOCALAPPDATA%\QuantResearchLab",
                "authenticode_signed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.keep_build:
        shutil.rmtree(build_root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, subprocess.CalledProcessError) as exc:
        print(f"商业安装包构建失败：{exc}", file=sys.stderr)
        raise SystemExit(2)
