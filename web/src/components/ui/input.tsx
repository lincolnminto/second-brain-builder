import { cloneElement, useId, type InputHTMLAttributes, type ReactElement, type SelectHTMLAttributes } from 'react';
import { cn } from './utils';
export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn('input', className)} {...props} />;
}
export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn('input select', className)} {...props} />;
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactElement<{ id?: string; 'aria-describedby'?: string }> }) {
  const generatedId = useId();
  const id = children.props.id ?? generatedId;
  return <div className="field"><label htmlFor={id}>{label}</label>{cloneElement(children, { id, 'aria-describedby': hint ? `${id}-hint` : children.props['aria-describedby'] })}{hint && <small id={`${id}-hint`}>{hint}</small>}</div>;
}
