import type { ReactNode } from 'react';
export function FeaturePlaceholder({ title, description, icon, children }: { title: string; description: string; icon: ReactNode; children?: ReactNode }) {
  return <div className="page placeholder-page"><div className="placeholder-mark">{icon}</div><h1>{title}</h1><p className="muted">{description}</p>{children}</div>;
}
