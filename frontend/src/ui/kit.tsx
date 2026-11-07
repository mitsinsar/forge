// The shared UI kit. Every screen builds from these; styles live only in theme.css.

import {
  useEffect,
  useId,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { Link } from "react-router-dom";

const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(" ");

// ---- layout ------------------------------------------------------------------

type Gap = 1 | 2 | 3 | 4 | 5;

export function Stack({ gap = 3, children, className }: { gap?: Gap; children: ReactNode; className?: string }) {
  return <div className={cx("stack", `gap-${gap}`, className)}>{children}</div>;
}

export function Row({
  gap = 2,
  align,
  justify,
  children,
  grow,
}: {
  gap?: Gap;
  align?: "top";
  justify?: "between" | "end";
  grow?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cx("row", `gap-${gap}`, align === "top" && "row--top", justify && `row--${justify}`, grow && "grow")}>
      {children}
    </div>
  );
}

/** Gives its content a fixed or minimum width (filters, small numeric inputs, thumbnails). */
export function Box({ width, minWidth, children }: { width?: number; minWidth?: number; children: ReactNode }) {
  return <div style={{ width, minWidth }}>{children}</div>;
}

export function Grow({ children }: { children?: ReactNode }) {
  return <div className="grow">{children}</div>;
}

export function Grid({ cols = 2, min, children }: { cols?: number; min?: number; children: ReactNode }) {
  const style = (min ? { "--grid-min": `${min}px` } : { "--grid-cols": cols }) as unknown as CSSProperties;
  return (
    <div className={cx("grid", !!min && "grid--auto")} style={style}>
      {children}
    </div>
  );
}

export function Text({
  muted,
  faint,
  small,
  strong,
  mono,
  wrap,
  children,
}: {
  muted?: boolean;
  faint?: boolean;
  small?: boolean;
  strong?: boolean;
  mono?: boolean;
  wrap?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        muted && "text-muted",
        faint && "text-faint",
        small && "text-sm",
        strong && "text-strong",
        mono && "mono",
        wrap && "text-wrap",
      )}
    >
      {children}
    </span>
  );
}

/** A link that opens in a new tab (files, source pages). */
export function ExternalLink({ href, mono, small, children }: { href: string; mono?: boolean; small?: boolean; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className={cx(mono && "mono", small && "text-sm")}>
      {children}
    </a>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="section-title">{children}</h3>;
}

// ---- Button ------------------------------------------------------------------

type Variant = "primary" | "secondary" | "danger" | "ghost";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  busy?: boolean;
};

export function Button({ variant = "secondary", size = "md", busy, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      className={cx("btn", variant !== "secondary" && `btn--${variant}`, size === "sm" && "btn--sm", className)}
      disabled={disabled || busy}
      {...rest}
    >
      {busy && <span className="spinner" style={{ width: 14, height: 14 }} />}
      {children}
    </button>
  );
}

/** A link that looks like a Button (internal routes, or files with `download`). */
export function LinkButton({
  to,
  href,
  download,
  variant = "secondary",
  size = "md",
  children,
}: {
  to?: string;
  href?: string;
  download?: boolean;
  variant?: Variant;
  size?: "sm" | "md";
  children: ReactNode;
}) {
  const className = cx("btn", variant !== "secondary" && `btn--${variant}`, size === "sm" && "btn--sm");
  if (to)
    return (
      <Link className={className} to={to}>
        {children}
      </Link>
    );
  return (
    <a className={className} href={href} download={download || undefined}>
      {children}
    </a>
  );
}

// ---- Card --------------------------------------------------------------------

export function Card({
  title,
  subtitle,
  actions,
  to,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  to?: string;
  children?: ReactNode;
}) {
  const header =
    title || actions ? (
      <div className="card__header">
        <div>
          {title && <h2 className="card__title">{title}</h2>}
          {subtitle && <div className="card__subtitle">{subtitle}</div>}
        </div>
        {actions && <div className="row gap-2">{actions}</div>}
      </div>
    ) : null;
  if (to)
    return (
      <Link className="card card--interactive" to={to}>
        {header}
        {children}
      </Link>
    );
  return (
    <section className="card">
      {header}
      {children}
    </section>
  );
}

// ---- PageHeader --------------------------------------------------------------

export function PageHeader({
  title,
  subtitle,
  actions,
  back,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  back?: { to: string; label: string };
}) {
  return (
    <header className="page-header">
      <div>
        {back && (
          <Link className="page-header__back" to={back.to}>
            ← {back.label}
          </Link>
        )}
        <h1 className="page-header__title">{title}</h1>
        {subtitle && <div className="page-header__subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

// ---- Tabs --------------------------------------------------------------------

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: { value: T; label: ReactNode; count?: number }[];
  value: T;
  onChange: (value: T) => void;
  label?: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          className="tabs__tab"
          aria-selected={item.value === value}
          onClick={() => onChange(item.value)}
        >
          {item.label}
          {item.count !== undefined && <span className="tabs__count">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}

// ---- form controls -----------------------------------------------------------

/** A labelled form field. `children` gets the id for its control; with `group`, the label names a group of controls. */
export function Field({
  label,
  hint,
  error,
  group,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  group?: boolean;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  if (group)
    return (
      <div className="field" role="group" aria-labelledby={`${id}-label`}>
        <span className="field__label" id={`${id}-label`}>
          {label}
        </span>
        {children(id)}
        {hint && <span className="field__hint">{hint}</span>}
        {error && <span className="field__error">{error}</span>}
      </div>
    );
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint && <span className="field__hint">{hint}</span>}
      {error && <span className="field__error">{error}</span>}
    </div>
  );
}

export function Input({ className, mono, ...rest }: InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }) {
  return (
    <input
      className={cx("input", rest.type === "file" && "input--file", mono && "mono", className)}
      name={rest.name ?? rest.id}
      {...rest}
    />
  );
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx("textarea", className)} name={rest.name ?? rest.id} {...rest} />;
}

export interface Option {
  value: string;
  label: string;
}

export function Select({
  options,
  placeholder,
  className,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { options: Option[]; placeholder?: string }) {
  return (
    <select className={cx("select", className)} name={rest.name ?? rest.id} {...rest}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label className="checkbox">
      <input type="checkbox" name={id} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

// ---- Dialog ------------------------------------------------------------------

export function Dialog({
  open,
  title,
  onClose,
  actions,
  wide,
  children,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  actions?: ReactNode;
  wide?: boolean;
  children?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={cx("dialog", wide && "dialog--wide")} role="dialog" aria-modal="true" aria-label={String(title)}>
        <h2 className="dialog__title">{title}</h2>
        {children}
        {actions && <div className="dialog__actions">{actions}</div>}
      </div>
    </div>
  );
}

/** A yes/no Dialog for destructive actions. */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  error?: unknown;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <Stack>
        <Text muted>{body}</Text>
        <ErrorNotice error={error} />
      </Stack>
    </Dialog>
  );
}

// ---- Badge, ProgressBar, Spinner, Notice -------------------------------------

export type Tone = "neutral" | "accent" | "info" | "success" | "warning" | "danger";

export function Badge({ tone = "neutral", dot, pulse, children }: { tone?: Tone; dot?: boolean; pulse?: boolean; children: ReactNode }) {
  return (
    <span className={cx("badge", tone !== "neutral" && `badge--${tone}`)}>
      {dot && <span className={cx("badge__dot", pulse && "badge__dot--pulse")} />}
      {children}
    </span>
  );
}

export function ProgressBar({ value, label }: { value: number; label?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="progress__bar" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />;
}

export function Notice({ tone = "info", title, children }: { tone?: "info" | "warning" | "danger" | "success"; title?: ReactNode; children?: ReactNode }) {
  return (
    <div className={cx("notice", `notice--${tone}`)} role={tone === "danger" ? "alert" : undefined}>
      <div>
        {title && <div className="notice__title">{title}</div>}
        {children && <div className="text-wrap">{children}</div>}
      </div>
    </div>
  );
}

/** Shows an API or network error, or nothing. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <Notice tone="danger" title="That didn't work">
      {message}
    </Notice>
  );
}

// ---- EmptyState --------------------------------------------------------------

export function EmptyState({ title, body, action }: { title: string; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {body && <div className="empty__body">{body}</div>}
      {action}
    </div>
  );
}

export function Loading() {
  return (
    <div className="row gap-2 text-muted">
      <Spinner /> Loading…
    </div>
  );
}

// ---- Stepper -----------------------------------------------------------------

export type StepState = "done" | "current" | "failed" | "todo";

/** A row of steps. A step with ``onClick`` is a button (to show that step again); ``selected`` marks the one on screen. */
export function Stepper({
  steps,
}: {
  steps: { label: string; sub?: string; state: StepState; onClick?: () => void; selected?: boolean }[];
}) {
  return (
    <ol className="stepper" style={{ listStyle: "none", margin: 0 }}>
      {steps.map((step, i) => {
        const className = cx("stepper__step", `stepper__step--${step.state}`, step.selected && "stepper__step--selected");
        const content = (
          <>
            <span className="stepper__num">{step.state === "done" ? "✓" : step.state === "failed" ? "!" : i + 1}</span>
            <span className="stack">
              <span>{step.label}</span>
              {step.sub && <span className="stepper__sub">{step.sub}</span>}
            </span>
          </>
        );
        return (
          <li key={step.label} className="row gap-2" style={{ flex: i < steps.length - 1 ? "1 1 auto" : "0 0 auto" }}>
            {step.onClick ? (
              <button type="button" className={cx(className, "stepper__step--button")} aria-pressed={!!step.selected} onClick={step.onClick}>
                {content}
              </button>
            ) : (
              <span className={className}>{content}</span>
            )}
            {i < steps.length - 1 && <span className="stepper__line" />}
          </li>
        );
      })}
    </ol>
  );
}

// ---- Table -------------------------------------------------------------------

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function TableRow({ onClick, children }: { onClick?: () => void; children: ReactNode }) {
  return (
    <tr data-clickable={onClick ? "true" : undefined} onClick={onClick}>
      {children}
    </tr>
  );
}

// ---- Thumb (selectable image) ------------------------------------------------

export function Thumb({
  src,
  alt,
  selected,
  label,
  onClick,
}: {
  src: string;
  alt: string;
  selected?: boolean;
  label?: ReactNode;
  onClick?: () => void;
}) {
  const content = (
    <>
      <img src={src} alt={alt} loading="lazy" />
      {label && <span className="thumb__label">{label}</span>}
    </>
  );
  if (!onClick) return <div className={cx("thumb thumb--static", selected && "thumb--selected")}>{content}</div>;
  return (
    <button type="button" className="thumb" aria-pressed={!!selected} onClick={onClick}>
      {content}
    </button>
  );
}

// ---- Stage (frame for the 3D viewer and the sprite player) -------------------

/** An empty viewer frame with a message, while a viewer loads. */
export function StageMessage({ children }: { children: ReactNode }) {
  return (
    <div className="stage">
      <div className="stage__overlay">{children}</div>
    </div>
  );
}

// ---- DefList -----------------------------------------------------------------

export function DefList({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="deflist">
      {items.map(([term, value], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
