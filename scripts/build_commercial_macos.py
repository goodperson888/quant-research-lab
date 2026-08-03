from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "apps" / "web"
DEFAULT_OUTPUT = ROOT / "dist" / "commercial"
APP_NAME = "Quant Research Lab"
EXECUTABLE_NAME = "QuantResearchLab"
BUNDLE_ID = "com.quantresearchlab.desktop"
NODE_VERSION = "24.10.0"
NODE_SHA256 = {
    "arm64": "fbc3d6e1e1d962450d058e918214373872cc4c46e08673f31c35932afac4a8c5",
    "x64": "627b884f66db0dd35f4b46fb9e994774ce560a7fb60798ba1ab81e867a73687d",
}

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
    print("+", " ".join(command), flush=True)
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


def machine_architecture() -> str:
    value = platform.machine().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64"}:
        return "x64"
    raise BuildError(f"暂不支持的 macOS 架构：{value}")


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
        run(["npm", "ci"], cwd=WEB_ROOT, env=env)
    run(["npm", "run", "build"], cwd=WEB_ROOT, env=env)
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


def download_node(resources: Path, architecture: str) -> None:
    archive_name = f"node-v{NODE_VERSION}-darwin-{architecture}.tar.gz"
    cache_dir = ROOT / ".cache" / "commercial" / "node"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / archive_name
    expected_hash = NODE_SHA256[architecture]
    if not archive.is_file() or sha256_file(archive) != expected_hash:
        archive.unlink(missing_ok=True)
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/{archive_name}"
        print(f"下载固定 Node 运行时：{url}", flush=True)
        partial = archive.with_suffix(archive.suffix + ".partial")
        run(
            [
                "/usr/bin/curl",
                "--fail",
                "--location",
                "--continue-at",
                "-",
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
    if actual != expected_hash:
        raise BuildError(
            f"Node 运行时校验失败：expected={expected_hash} actual={actual}"
        )
    with tempfile.TemporaryDirectory(prefix="quant-node-") as temporary:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(temporary, filter="data")
        extracted = Path(temporary) / archive_name.removesuffix(".tar.gz")
        copy_file(extracted / "bin" / "node", resources / "node" / "bin" / "node")
        copy_file(extracted / "LICENSE", resources / "node" / "LICENSE")
    node = resources / "node" / "bin" / "node"
    node.chmod(node.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_backend(contents: Path, build_root: Path, *, jobs: int) -> None:
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
        f"--output-dir={output}",
        f"--output-filename={EXECUTABLE_NAME}",
        str(ROOT / "src" / "quant_lab" / "commercial_runtime.py"),
    ]
    run(command, env=env)
    distributions = list(output.glob("*.dist"))
    if len(distributions) != 1:
        raise BuildError("Nuitka standalone 输出目录数量不正确")
    copy_tree(distributions[0], contents / "MacOS")
    executable = contents / "MacOS" / EXECUTABLE_NAME
    if not executable.is_file():
        raise BuildError("Nuitka 未生成商业运行主程序")
    executable.chmod(
        executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )


def write_info_plist(contents: Path, *, version: str) -> None:
    payload: dict[str, Any] = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": APP_NAME,
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(payload, output, sort_keys=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_build_manifest(
    resources: Path,
    *,
    version: str,
    architecture: str,
    public_key_fingerprint: str,
    dirty: bool,
) -> None:
    revision = capture(["git", "rev-parse", "HEAD"])
    payload = {
        "schema_version": 1,
        "product": "quant-research-lab",
        "version": version,
        "git_revision": revision,
        "source_dirty": dirty,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "platform": "macos",
        "architecture": architecture,
        "license_enforcement": "commercial_required",
        "license_public_key_fingerprint": public_key_fingerprint,
        "frontend_api_url": "http://127.0.0.1:8100",
        "customer_data_home": "~/Library/Application Support/QuantResearchLab",
        "live_trading_enabled": False,
    }
    (resources / "build-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_customer_files(image_root: Path, app_path: Path) -> None:
    instructions = """Quant Research Lab 商业安装包（macOS）

1. 把“Quant Research Lab.app”拖到 Applications。
2. 双击启动，浏览器会打开本地研究工作台。
3. 第一次启动会在“本地设置”显示设备码。
4. 把设备码发给服务提供方，收到 .qllicense 后在设置页导入。
5. 客户数据保存在：
   ~/Library/Application Support/QuantResearchLab

许可证到期后历史研究仍可查看和导出。
本版本不包含实盘交易接口。

若 macOS 提示开发者身份未验证，说明此构建尚未使用 Apple Developer ID 签名和公证；
正式对外发行前应完成签名与公证。
"""
    (image_root / "安装与激活说明.txt").write_text(instructions, encoding="utf-8")
    stop_script = f"""#!/bin/bash
set -euo pipefail
APP="{app_path.name}"
if [[ -x "$(dirname "$0")/$APP/Contents/MacOS/{EXECUTABLE_NAME}" ]]; then
  "$(dirname "$0")/$APP/Contents/MacOS/{EXECUTABLE_NAME}" --stop
else
  "/Applications/$APP/Contents/MacOS/{EXECUTABLE_NAME}" --stop
fi
"""
    stop_path = image_root / "停止 Quant Research Lab.command"
    stop_path.write_text(stop_script, encoding="utf-8")
    stop_path.chmod(0o755)
    applications = image_root / "Applications"
    applications.symlink_to("/Applications")


def create_archives(
    *,
    image_root: Path,
    app_path: Path,
    output_dir: Path,
    version: str,
    architecture: str,
    skip_dmg: bool,
) -> list[Path]:
    base = f"QuantResearchLab-{version}-macos-{architecture}"
    zip_path = output_dir / f"{base}.zip"
    zip_path.unlink(missing_ok=True)
    run(
        [
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(app_path),
            str(zip_path),
        ]
    )
    artifacts = [zip_path]
    if not skip_dmg:
        dmg_path = output_dir / f"{base}.dmg"
        dmg_path.unlink(missing_ok=True)
        run(
            [
                "/usr/bin/hdiutil",
                "create",
                "-volname",
                APP_NAME,
                "-srcfolder",
                str(image_root),
                "-format",
                "UDZO",
                "-ov",
                str(dmg_path),
            ]
        )
        artifacts.append(dmg_path)
    checksum_path = output_dir / f"{base}.sha256"
    checksum_path.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in artifacts),
        encoding="utf-8",
    )
    artifacts.append(checksum_path)
    return artifacts


def codesign_app(app_path: Path, *, sign_identity: str) -> None:
    sign_command = [
        "/usr/bin/codesign",
        "--force",
        "--deep",
    ]
    if sign_identity != "-":
        sign_command.extend(["--options", "runtime", "--timestamp"])
    sign_command.extend(["--sign", sign_identity, str(app_path)])
    run(sign_command)
    run(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=2",
            str(app_path),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构建 Quant Research Lab macOS 商业安装包"
    )
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--version", default=project_version())
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--jobs", type=int, default=max(1, min(os.cpu_count() or 1, 4)))
    parser.add_argument("--sign-identity", default="-")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--skip-npm-ci", action="store_true")
    parser.add_argument("--skip-dmg", action="store_true")
    parser.add_argument("--keep-build", action="store_true")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        raise BuildError("macOS 商业包必须在 macOS 构建机上生成")
    version = safe_version(args.version)
    architecture = machine_architecture()
    dirty = source_dirty()
    if dirty and not args.allow_dirty:
        raise BuildError(
            "工作区存在未提交修改；正式商业包要求干净提交。"
            "本地 QA 可显式使用 --allow-dirty"
        )
    public_key_path = Path(args.public_key)
    _public_key, fingerprint = load_public_key(public_key_path)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    build_root = output_dir / "build" / f"macos-{architecture}"
    image_root = build_root / "image"
    if build_root.exists():
        shutil.rmtree(build_root)
    contents = image_root / f"{APP_NAME}.app" / "Contents"
    resources = contents / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)

    copy_file(public_key_path.resolve(), resources / "licensing" / "public-key.pem")
    prepare_project_template(resources / "project-template")
    write_build_manifest(
        resources,
        version=version,
        architecture=architecture,
        public_key_fingerprint=fingerprint,
        dirty=dirty,
    )
    build_web(resources, skip_npm_ci=args.skip_npm_ci)
    download_node(resources, architecture)
    build_backend(contents, build_root, jobs=args.jobs)
    write_info_plist(contents, version=version)
    app_path = image_root / f"{APP_NAME}.app"
    write_customer_files(image_root, app_path)

    codesign_app(app_path, sign_identity=args.sign_identity)
    artifacts = create_archives(
        image_root=image_root,
        app_path=app_path,
        output_dir=output_dir,
        version=version,
        architecture=architecture,
        skip_dmg=args.skip_dmg,
    )
    print(
        json.dumps(
            {
                "status": "built",
                "version": version,
                "architecture": architecture,
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
                "customer_data_home": (
                    "~/Library/Application Support/QuantResearchLab"
                ),
                "notarized": False,
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
