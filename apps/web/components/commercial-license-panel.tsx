"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ChangeEvent } from "react";

import {
  apiFetch,
  CommercialLicenseStatus,
} from "@/lib/api";

export function CommercialLicensePanel() {
  const queryClient = useQueryClient();
  const [licenseDocument, setLicenseDocument] = useState("");
  const [selectedFile, setSelectedFile] = useState("");
  const [copied, setCopied] = useState(false);
  const status = useQuery({
    queryKey: ["commercial-license"],
    queryFn: () =>
      apiFetch<CommercialLicenseStatus>("/api/license/status"),
  });
  const importLicense = useMutation({
    mutationFn: () =>
      apiFetch<CommercialLicenseStatus>("/api/license/import", {
        method: "POST",
        body: JSON.stringify({ license_document: licenseDocument }),
      }),
    onSuccess: (next) => {
      queryClient.setQueryData(["commercial-license"], next);
      setLicenseDocument("");
      setSelectedFile("");
    },
  });

  if (status.isPending) {
    return (
      <section className="rounded-2xl border border-white/10 bg-white/[0.025] p-5 text-sm text-slate-500">
        正在读取本机授权状态……
      </section>
    );
  }
  if (status.isError) {
    return (
      <section className="rounded-2xl border border-rose-300/20 bg-rose-300/[0.04] p-5 text-sm text-rose-200">
        授权状态暂时无法读取，请确认本地 API 已启动。
      </section>
    );
  }

  const current = status.data;
  const development =
    current.enforcement_mode === "development_disabled";
  const active = current.state === "active";

  async function copyDeviceCode() {
    await navigator.clipboard.writeText(current.device_code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  async function selectLicense(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 100_000) {
      setSelectedFile("文件过大，许可证应小于 100 KB");
      setLicenseDocument("");
      return;
    }
    setSelectedFile(file.name);
    setLicenseDocument(await file.text());
  }

  return (
    <section
      className={`rounded-2xl border p-5 ${
        active
          ? "border-emerald-300/20 bg-emerald-300/[0.035]"
          : development
            ? "border-sky-300/20 bg-sky-300/[0.035]"
            : "border-amber-300/20 bg-amber-300/[0.035]"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500">
            本地商业授权
          </p>
          <h2 className="mt-2 text-lg font-medium text-slate-100">
            {licenseStateLabel(current.state)}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            {current.message}
          </p>
        </div>
        <span
          className={`rounded-full border px-3 py-1 text-xs ${
            current.write_allowed
              ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100"
              : "border-amber-300/20 bg-amber-300/10 text-amber-100"
          }`}
        >
          {current.write_allowed ? "可开始新研究" : "只读与导出"}
        </span>
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <LicenseItem
          label="授权模式"
          value={development ? "开发模式，不强制授权" : "商业模式，离线验证"}
        />
        <LicenseItem
          label="套餐"
          value={current.plan ? planLabel(current.plan) : "尚未激活"}
        />
        <LicenseItem
          label="到期时间"
          value={current.expires_at ? formatDate(current.expires_at) : "未记录"}
        />
        <LicenseItem
          label="剩余时间"
          value={
            current.days_remaining === null
              ? "未记录"
              : `${current.days_remaining} 天`
          }
        />
      </div>

      {current.features.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {current.features.map((feature) => (
            <span
              key={feature}
              className="rounded-full border border-white/10 bg-black/10 px-3 py-1 text-xs text-slate-300"
            >
              {featureLabel(feature)}
            </span>
          ))}
        </div>
      ) : null}

      {!development ? (
        <div className="mt-6 grid gap-5 border-t border-white/[0.08] pt-5 lg:grid-cols-[1fr_1.1fr]">
          <div>
            <h3 className="text-sm font-medium text-slate-200">1. 发送本机设备码</h3>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              把下面的设备码发给服务提供方。设备原始标识不会离开本机，许可证只绑定这个哈希码。
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <code className="rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-xs text-sky-100">
                {current.device_code}
              </code>
              <button
                type="button"
                onClick={copyDeviceCode}
                className="min-h-10 rounded-lg border border-white/10 px-3 text-xs text-slate-200 transition hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
              >
                {copied ? "已复制" : "复制设备码"}
              </button>
            </div>
          </div>

          <div>
            <h3 className="text-sm font-medium text-slate-200">2. 导入许可证文件</h3>
            <p className="mt-2 text-xs leading-5 text-slate-500">
              收到 `.qllicense` 文件后在这里导入。验证完全在本机完成，不需要登录或连接授权服务器。
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <label className="inline-flex min-h-10 cursor-pointer items-center rounded-lg border border-white/10 px-3 text-xs text-slate-200 transition hover:bg-white/[0.05] focus-within:ring-2 focus-within:ring-sky-300">
                选择许可证
                <input
                  type="file"
                  accept=".qllicense,application/json"
                  onChange={selectLicense}
                  className="sr-only"
                />
              </label>
              <span className="max-w-56 truncate text-xs text-slate-500">
                {selectedFile || "尚未选择文件"}
              </span>
              <button
                type="button"
                disabled={!licenseDocument || importLicense.isPending}
                onClick={() => importLicense.mutate()}
                className="min-h-10 rounded-lg bg-sky-300/15 px-4 text-xs text-sky-100 transition hover:bg-sky-300/20 disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300"
              >
                {importLicense.isPending ? "正在验证……" : "验证并导入"}
              </button>
            </div>
            {importLicense.isError ? (
              <p className="mt-3 text-xs leading-5 text-rose-200">
                {importLicense.error instanceof Error
                  ? importLicense.error.message
                  : "许可证导入失败"}
              </p>
            ) : null}
            {importLicense.isSuccess ? (
              <p className="mt-3 text-xs leading-5 text-emerald-200">
                许可证已验证并保存到本机。
              </p>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="mt-5 border-t border-white/[0.08] pt-4 text-xs leading-5 text-slate-500">
          当前源码开发环境默认放行，便于继续开发和测试。未来商业安装包会在构建时启用强制授权，
          不能依赖前端开关决定。
        </p>
      )}
    </section>
  );
}

function LicenseItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-black/10 p-4">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-2 text-sm text-slate-200">{value}</p>
    </div>
  );
}

function licenseStateLabel(state: string) {
  return (
    {
      active: "授权有效",
      development_unrestricted: "开发模式",
      license_missing: "等待激活",
      license_expired: "授权已到期",
      device_mismatch: "设备不匹配",
      clock_rollback_detected: "系统时间异常",
      invalid_license_signature: "许可证已被修改",
      license_system_misconfigured: "商业构建配置不完整",
      active_read_only: "许可证仅允许只读",
    }[state] ?? "授权需要处理"
  );
}

function planLabel(plan: string) {
  return (
    {
      trial: "试用版",
      personal: "个人版",
      pro: "专业版",
    }[plan] ?? plan
  );
}

function featureLabel(feature: string) {
  return (
    {
      all: "全部功能",
      research: "策略研究",
      backtest: "本地回测",
      batch_trials: "批量参数试验",
      reports: "研究报告",
      local_ai: "本地 AI",
    }[feature] ?? feature.replaceAll("_", " ")
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
