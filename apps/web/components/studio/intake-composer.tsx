import { StrategyDraft } from "@/lib/api";

export function IntakeComposer({
  title,
  sourceType,
  content,
  activeDraft,
  notice,
  pending,
  error,
  onTitleChange,
  onSourceTypeChange,
  onContentChange,
  onSubmit,
}: {
  title: string;
  sourceType: "natural_language" | "pine" | "file";
  content: string;
  activeDraft: StrategyDraft | null;
  notice: string;
  pending: boolean;
  error: string | null;
  onTitleChange: (value: string) => void;
  onSourceTypeChange: (value: "natural_language" | "pine" | "file") => void;
  onContentChange: (value: string) => void;
  onSubmit: () => void;
}) {
  return (
    <section className="order-2 flex min-h-[520px] min-w-0 flex-col rounded-2xl border border-white/10 bg-[#0a151e]/90 xl:order-1 xl:col-start-1">
      <div className="border-b border-white/10 px-5 py-4">
        <div className="text-sm font-semibold">策略对话与原始输入</div>
        <div className="mt-1 text-xs text-slate-500">
          输入是入口；结构化状态、审批和审计事件才是权威记录。
        </div>
      </div>
      <div className="flex-1 space-y-4 p-5">
        <div className="max-w-[88%] rounded-2xl rounded-tl-sm border border-emerald-300/15 bg-emerald-300/[0.06] p-4 text-sm leading-6 text-slate-300">
          描述策略规则、粘贴 Pine Script，或预留文件来源。网页模型未配置时，系统只保存原始内容或研究指令，不伪造回复。
        </div>
        {activeDraft ? (
          <div className="ml-auto max-w-[88%] rounded-2xl rounded-tr-sm bg-slate-100 p-4 text-sm leading-6 text-slate-900">
            {activeDraft.raw_content}
          </div>
        ) : null}
        <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3 text-xs leading-5 text-slate-400">
          {notice}
        </div>
      </div>
      <form
        className="border-t border-white/10 p-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <div className="mb-3 grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1fr)_180px]">
          <input
            value={title}
            onChange={(event) => onTitleChange(event.target.value)}
            className="min-w-0 rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600"
            placeholder="研究会话标题"
          />
          <select
            value={sourceType}
            onChange={(event) =>
              onSourceTypeChange(
                event.target.value as "natural_language" | "pine" | "file",
              )
            }
            className="min-w-0 rounded-xl border border-white/10 bg-[#071017] px-3 py-2.5 text-sm text-slate-200"
          >
            <option value="natural_language">自然语言</option>
            <option value="pine">Pine Script</option>
            <option value="file">文件来源占位</option>
          </select>
        </div>
        <textarea
          value={content}
          onChange={(event) => onContentChange(event.target.value)}
          className="min-h-32 w-full resize-y rounded-xl border border-white/10 bg-[#071017] p-3 text-sm leading-6 text-slate-100 placeholder:text-slate-600"
          placeholder="输入原始策略，不要先为了盈利而改写……"
        />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-slate-500">
            文件上传接口尚未启用；不接受任意路径。
          </div>
          <button
            type="submit"
            disabled={pending}
            className="rounded-xl bg-emerald-300 px-4 py-2.5 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {pending ? "保存中…" : "保存为策略草稿"}
          </button>
        </div>
        {error ? <div className="mt-3 text-sm text-rose-300">{error}</div> : null}
      </form>
    </section>
  );
}
