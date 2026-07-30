"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { href: "/studio", label: "研究工作台", caption: "策略与研究助手" },
  { href: "/strategies", label: "策略版本", caption: "来源、基准与候选" },
  { href: "/jobs", label: "后台任务", caption: "进度、日志与停止原因" },
  { href: "/reports", label: "研究报告", caption: "回测与实验结果" },
  { href: "/data", label: "市场数据", caption: "范围、质量与缺口" },
  { href: "/settings", label: "本地设置", caption: "模型、版本与安全" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <aside className="border-b border-white/10 bg-[#08131b]/95 p-5 lg:min-h-screen lg:border-b-0 lg:border-r">
        <div className="mb-7">
          <div className="mb-2 inline-flex rounded-full border border-emerald-300/20 bg-emerald-300/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.2em] text-emerald-200">
            仅限本地研究
          </div>
          <div className="text-lg font-semibold tracking-tight">个人量化研究室</div>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            策略优先 · 过程可审计
          </p>
        </div>
        <nav className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-1">
          {navigation.map((item) => {
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`rounded-xl border px-3 py-2.5 transition ${
                  active
                    ? "border-emerald-300/30 bg-emerald-300/10 text-white"
                    : "border-transparent text-slate-300 hover:border-white/10 hover:bg-white/5"
                }`}
              >
                <div className="text-sm font-medium">{item.label}</div>
                <div className="mt-0.5 text-[11px] text-slate-500">{item.caption}</div>
              </Link>
            );
          })}
        </nav>
        <div className="mt-7 hidden rounded-xl border border-amber-300/15 bg-amber-300/5 p-3 text-xs leading-5 text-amber-100/80 lg:block">
          实盘接口不存在。冻结基准和接受策略修改均需人工确认。
        </div>
      </aside>
      <main className="min-w-0">{children}</main>
    </div>
  );
}
