// Primitivas de UI reutilizables (solo tema claro, radios 6-8px, español).

import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function Button({
  className = '',
  variant = 'primary',
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'danger' }) {
  const base =
    'inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ' +
    'transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none'
  const styles = {
    primary: 'bg-accent text-accentink hover:opacity-90',
    ghost: 'bg-transparent text-muted hover:bg-panel',
    danger: 'bg-accent text-accentink hover:opacity-90',
  }[variant]
  return <button className={`${base} ${styles} ${className}`} {...rest} />
}

export function Card({
  className = '',
  id,
  children,
}: {
  className?: string
  id?: string
  children: ReactNode
}) {
  return (
    <div id={id} className={`oc-card ${className}`}>
      {children}
    </div>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      className={`oc-spin inline-block h-4 w-4 rounded-full border-2 border-line border-t-ink ${className}`}
      aria-label="cargando"
    />
  )
}

export function Badge({
  children,
  tone = 'muted',
}: {
  children: ReactNode
  tone?: 'muted' | 'ok' | 'warn' | 'danger'
}) {
  const map = {
    muted: 'bg-panel text-muted',
    ok: 'bg-panel text-success',
    warn: 'bg-panel text-amber-600',
    danger: 'bg-panel text-danger',
  }[tone]
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${map}`}>
      {children}
    </span>
  )
}
