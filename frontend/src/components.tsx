import { Rosette, GuillocheBorder } from "./art/guilloche";
import { FoilSurface } from "./art/FoilSurface";
import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { errorMessage, safeUrl } from "./api";
import {
  editors,
  enums,
  fieldTitles,
  formatDate,
  initialValue,
  isObject,
  labels,
  normalizeIsin,
  validIsin,
} from "./fields";
import type { EditorNode } from "./fields";
import { pollJob } from "./polling";
import type { Header, Job, Json } from "./types";

const statuses: Record<string, [string, string, string]> = {
  verified_iss: ["Сверено с ISS", "good", "✓"],
  mismatch_iss: ["Расхождение с ISS", "danger", "≠"],
  extracted: ["Извлечено", "info", "◆"],
  iss_unconfirmed: ["Из ISS, без подтверждения", "info", "◆"],
  not_found: ["Не найдено", "neutral", "—"],
  needs_review: ["Нужна проверка", "warn", "!"],
  confirmed: ["Подтверждено человеком", "human", "✓"],
  corrected: ["Исправлено человеком", "human", "✎"],
  ready: ["Готово", "good", "✓"],
  succeeded: ["Завершено", "good", "✓"],
  failed: ["Ошибка", "danger", "!"],
  queued: ["В очереди", "neutral", "◷"],
  running: ["Выполняется", "info", "◷"],
  processing: ["Обработка", "info", "◷"],
  empty: ["Не найдено", "neutral", "—"],
  blocked: ["Доступ ограничен", "warn", "!"],
  official_access_required: ["Нужен официальный доступ", "warn", "!"],
  adapter_not_implemented: ["Договорной API не подключён", "warn", "!"],
};
export function StatusBadge({ status }: { status: string }) {
  const [label, color, icon] = statuses[status] ?? [
    "Статус неизвестен",
    "neutral",
    "?",
  ];
  return (
    <span className={`badge ${color}`}>
      <span aria-hidden="true">{icon}</span> {label}
    </span>
  );
}
export function ErrorNotice({
  message,
  retry,
}: {
  message: string | null;
  retry?: () => void;
}) {
  return message ? (
    <div className="notice danger" role="alert">
      <span>{message}</span>
      {retry && (
        <button type="button" className="small" onClick={retry}>
          Повторить проверку
        </button>
      )}
    </div>
  ) : null;
}
export function ExternalLink({
  url,
  children,
}: {
  url?: string | null;
  children: React.ReactNode;
}) {
  const href = safeUrl(url);
  return href ? (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
      <span aria-label="в новой вкладке"> ↗</span>
    </a>
  ) : (
    <span>{children}</span>
  );
}
export function IsinForm({
  initial = "",
  onSubmit,
  label = "Открыть выпуск",
  busy = false,
}: {
  initial?: string;
  onSubmit: (isin: string) => void;
  label?: string;
  busy?: boolean;
}) {
  const id = useId();
  const [value, setValue] = useState(initial);
  const [error, setError] = useState(false);
  function submit(e: FormEvent) {
    e.preventDefault();
    if (!validIsin(value)) {
      setError(true);
      return;
    }
    setError(false);
    onSubmit(normalizeIsin(value));
  }
  return (
    <form className="isin-form" onSubmit={submit} noValidate>
      <label htmlFor={id}>ISIN выпуска</label>
      <div className="input-row">
        <input
          id={id}
          className="isin-input"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setError(false);
          }}
          placeholder="RU000A10ATW2"
          autoCapitalize="characters"
          spellCheck={false}
          autoComplete="off"
          maxLength={16}
          aria-invalid={error}
          aria-describedby={id + "-hint"}
        />
        <button disabled={busy} type="submit">
          {busy ? "Загрузка…" : label}
        </button>
      </div>
      <p id={id + "-hint"} className={error ? "error-text" : "muted"}>
        {error
          ? "Введите российский ISIN: RU, затем 9 букв или цифр и последняя цифра."
          : "12 символов. Например, RU000A10ATW2"}
      </p>
    </form>
  );
}
export function IssueHeader({
  isin,
  header,
  fields,
  fetchedAt,
}: {
  isin: string;
  header: Header;
  fields?: Record<string, Json | undefined>;
  fetchedAt?: string | null;
}) {
  return (
    <FoilSurface as="section" className="panel issue-header" label="Параметры выпуска">
      <GuillocheBorder seed={isin} />
      <Rosette seed={isin} className="issue-rosette" />
      <div className="issue-identifier">
        {isin}
        {header.listing_level
          ? ` / Уровень листинга ${header.listing_level}`
          : ""}
      </div>
      <h2>{header.name || header.short_name || "Выпуск облигаций"}</h2>
      <p className="issuer">
        {header.issuer || "Эмитент не определён"}{" "}
        <span className="muted issuer-inn">
          ИНН {header.inn || "не указан"}
        </span>
      </p>
      {fields && (
        <dl className="metrics">
          {[
            "face_value",
            "placement_date",
            "maturity_date",
            "coupon_type",
            "coupon_rate",
          ].map((key) => (
            <div key={key}>
              <dt>{fieldTitles[key as keyof typeof fieldTitles]}</dt>
              <dd>
                {key === "coupon_type" && isObject(fields[key]) ? (
                  <>
                    <ValueView value={fields[key].type} />
                    {fields[key].inferred && (
                      <small className="caption"> (по графику ISS)</small>
                    )}
                  </>
                ) : key === "coupon_rate" ? (
                  <CouponBrief value={fields[key]} />
                ) : (
                  <ValueView value={fields[key] ?? null} compact />
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {fetchedAt && (
        <p className="caption">Снимок MOEX ISS: {formatDate(fetchedAt)}</p>
      )}
    </FoilSurface>
  );
}
export function CouponBrief({ value }: { value: Json | undefined }) {
  if (!isObject(value)) return <span className="muted">Нет данных</span>;
  if (isObject(value.formula) && value.formula.text)
    return <span>{String(value.formula.text)}</span>;
  if (value.current_rate_percent != null)
    return (
      <span>
        {String(value.current_rate_percent)}%{" "}
        <small className="caption">(текущая ставка ISS)</small>
      </span>
    );
  const periods = Array.isArray(value.periods)
    ? value.periods.filter(isObject)
    : [];
  const known = periods.filter(
    (p) => p.rate_percent != null || p.amount != null,
  );
  const last = known.at(-1);
  return (
    <span>
      {last ? (
        <>
          {last.rate_percent != null
            ? `${last.rate_percent}%`
            : `${last.amount} ₽`}
          <small className="caption">
            {" "}
            (последний известный купон № {String(last.to)})
          </small>
        </>
      ) : (
        <span className="muted">Ставка пока не определена</span>
      )}
    </span>
  );
}

const stepLabels: Record<string, string> = {
  queued: "Ожидание воркера",
  resolve_iss: "Обновление MOEX ISS",
  prime: "Поиск на ПРАЙМ",
  skrin: "Поиск на СКРИН",
  edisclosure: "Проверка e-disclosure",
  ingest: "Приём документов",
  parse: "Распознавание PDF",
  index: "Индексация документов",
  retrieve: "Поиск фрагментов",
  extract_fields: "Извлечение и проверка полей",
  verify: "Проверка цитат и ISS",
  finalize: "Завершение",
};
export function JobProgress({
  id,
  onDone,
  onUpdate,
}: {
  id: string;
  onDone: (job: Job) => void;
  onUpdate?: (job: Job) => void;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    pollJob(
      id,
      (value) => {
        setJob(value);
        setError(null);
        onUpdate?.(value);
      },
      controller.signal,
      setRetry,
    )
      .then((value) => {
        if (!controller.signal.aborted) onDone(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      });
    return () => controller.abort();
  }, [id, onDone, onUpdate, attempt]);
  return (
    <section className="panel job-panel" aria-label="Прогресс задачи">
      <div className="section-heading">
        <h3>
          {job
            ? (stepLabels[job.step] ?? "Обработка задачи")
            : "Подключение к задаче…"}
        </h3>
        {job && <StatusBadge status={job.status} />}
      </div>
      <progress
        max={100}
        value={job?.progress ?? 0}
        aria-label="Готовность задачи"
      />
      <p className="caption" role="status">
        {job?.progress ?? 0}%
        {job?.total_fields
          ? `; Полей обработано: ${job.processed_fields ?? 0} из ${job.total_fields}`
          : ""}
        . Можно закрыть страницу: обработка продолжится на сервере.
      </p>
      {retry && (
        <p className="notice warn" role="status">
          {retry}
        </p>
      )}
      <ErrorNotice
        message={error}
        retry={() => {
          setError(null);
          setAttempt((n) => n + 1);
        }}
      />
      {job?.status === "failed" && (
        <ErrorNotice
          message={job.error?.message ?? "Задача завершилась с ошибкой."}
        />
      )}
    </section>
  );
}

export function ValueView({
  value,
  compact = false,
  expanded = false,
}: {
  expanded?: boolean;
  value: Json | undefined;
  compact?: boolean;
}) {
  if (value == null) return <span className="muted">Нет данных</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">Список пуст</span>;
    if (value.length > 6 && !expanded)
      return (
        <details className="schedule-details">
          <summary>Показать полный график ({value.length} записей)</summary>
          <ValueView value={value} expanded />
        </details>
      );
    if (value.every(isObject)) {
      const keys = [
        ...new Set(value.flatMap((row) => Object.keys(row))),
      ].filter((key) => value.some((row) => row[key] != null));
      return (
        <div
          className="table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Таблица значений"
        >
          <table>
            <thead>
              <tr>
                {keys.map((key) => (
                  <th key={key}>{labels[key] ?? key}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {value.map((row, index) => (
                <tr key={index}>
                  {keys.map((key) => (
                    <td key={key}>
                      <ValueView value={row[key]} compact />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            <ValueView value={item} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    if ("amount" in value && "currency" in value)
      return (
        <span>
          {String(value.amount ?? "—")} {String(value.currency ?? "")}
        </span>
      );
    if (Object.keys(value).length === 1 && "date" in value)
      return <span>{formatDate(String(value.date))}</span>;
    const entries = Object.entries(value).filter(
      ([key, item]) => item != null && key !== "inferred",
    );
    return (
      <dl className={compact ? "value-list compact" : "value-list"}>
        {entries.map(([key, item]) => (
          <div key={key}>
            <dt>{labels[key] ?? key}</dt>
            <dd>
              <ValueView value={item} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  if (typeof value === "boolean") return <span>{value ? "Да" : "Нет"}</span>;
  return (
    <span>
      {typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
        ? formatDate(value)
        : (enums[String(value)] ?? String(value))}
    </span>
  );
}

export function ValueEditor({
  node,
  value,
  onChange,
  title,
  path = "value",
}: {
  node: EditorNode;
  value: Json;
  onChange: (value: Json) => void;
  title: string;
  path?: string;
}) {
  const id = useId();
  if (node.kind === "object") {
    if (value === null && node.optional)
      return (
        <button
          type="button"
          className="small secondary"
          onClick={() => onChange(initialValue({ ...node, optional: false }))}
        >
          Добавить: {title}
        </button>
      );
    const record = isObject(value) ? value : {};
    return (
      <fieldset className="editor-group">
        <legend>{title}</legend>
        {node.optional && (
          <button
            type="button"
            className="small secondary"
            onClick={() => onChange(null)}
          >
            Не указывать
          </button>
        )}
        {Object.entries(node.children).map(([key, child]) => (
          <ValueEditor
            key={key}
            node={child}
            value={record[key] ?? null}
            title={labels[key] ?? key}
            path={path + "." + key}
            onChange={(next) => onChange({ ...record, [key]: next })}
          />
        ))}
      </fieldset>
    );
  }
  if (node.kind === "list") {
    const items = Array.isArray(value) ? value : [];
    return (
      <fieldset className="editor-group">
        <legend>{title}</legend>
        {items.map((item, i) => (
          <div className="editor-item" key={i}>
            <ValueEditor
              node={node.item}
              value={item}
              onChange={(next) =>
                onChange(items.map((old, index) => (i === index ? next : old)))
              }
              title={`${title} ${i + 1}`}
              path={path + "." + i}
            />
            <button
              type="button"
              className="small secondary"
              aria-label={`Удалить ${title} ${i + 1}`}
              onClick={() => onChange(items.filter((_, index) => index !== i))}
            >
              Удалить
            </button>
          </div>
        ))}
        <button
          type="button"
          className="small secondary"
          onClick={() => onChange([...items, initialValue(node.item)])}
        >
          Добавить: {title}
        </button>
      </fieldset>
    );
  }
  return (
    <div className="editor-control">
      <label htmlFor={id}>
        {title}
        {node.optional ? " (необязательно)" : ""}
      </label>
      {node.kind === "select" ? (
        <select
          id={id}
          value={String(value ?? "")}
          required={!node.optional}
          onChange={(e) => onChange(e.target.value)}
        >
          {!value && <option value="">Выберите…</option>}
          {node.options.map((option) => (
            <option key={option} value={option}>
              {enums[option] ?? option}
            </option>
          ))}
        </select>
      ) : node.kind === "text" && path.split(".").at(-1) !== "currency" ? (
        <textarea
          id={id}
          rows={2}
          maxLength={12000}
          required={!node.optional}
          value={String(value ?? "")}
          onChange={(e) =>
            onChange(e.target.value || (node.optional ? null : ""))
          }
        />
      ) : (
        <input
          id={id}
          type={
            node.kind === "date"
              ? "date"
              : node.kind === "integer"
                ? "number"
                : "text"
          }
          min={node.kind === "integer" ? 1 : undefined}
          step={node.kind === "integer" ? 1 : undefined}
          inputMode={node.kind === "decimal" ? "decimal" : undefined}
          required={!node.optional}
          pattern={
            node.kind === "decimal"
              ? "-?[0-9]+([.,][0-9]+)?"
              : path.endsWith(".currency")
                ? "[A-Z]{3}"
                : undefined
          }
          title={
            path.endsWith(".currency")
              ? "Три заглавные буквы, например RUB"
              : undefined
          }
          value={String(value ?? "")}
          onChange={(e) =>
            onChange(
              e.target.value === ""
                ? node.optional
                  ? null
                  : ""
                : node.kind === "integer"
                  ? Number(e.target.value)
                  : e.target.value,
            )
          }
        />
      )}
    </div>
  );
}
export function fieldEditor(code: keyof typeof editors) {
  return editors[code];
}

export function Skeleton({
  label = "Загружаем документы",
}: {
  label?: string;
}) {
  return (
    <div className="skeleton" role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      <i />
      <i />
      <i />
      <i />
    </div>
  );
}
