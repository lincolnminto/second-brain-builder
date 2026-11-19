import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { t } from '../../i18n/en';
import { Button } from './button';

export function Dialog({ open, onOpenChange, title, description, children, className = '' }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string; children: ReactNode; className?: string;
}) {
  return <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
    <DialogPrimitive.Portal><DialogPrimitive.Overlay className="dialog-overlay" />
      <DialogPrimitive.Content className={`dialog ${className}`}>
        <DialogPrimitive.Title className="dialog-title">{title}</DialogPrimitive.Title>
        <DialogPrimitive.Description className="muted">{description}</DialogPrimitive.Description>
        <DialogPrimitive.Close asChild><Button variant="ghost" size="icon" className="dialog-close" aria-label={t('close')}><X size={18} /></Button></DialogPrimitive.Close>
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  </DialogPrimitive.Root>;
}
export function ConfirmDialog({ open, onOpenChange, title, description, onConfirm, pending, confirmLabel = t('continue'), children }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: string; description: string;
  onConfirm: () => void; pending?: boolean; confirmLabel?: string; children?: ReactNode;
}) {
  return <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description}>
    {children}<div className="actions"><Button variant="secondary" onClick={() => onOpenChange(false)} disabled={pending}>{t('cancel')}</Button>
      <Button onClick={onConfirm} disabled={pending}>{pending ? t('loading') : confirmLabel}</Button></div>
  </Dialog>;
}
