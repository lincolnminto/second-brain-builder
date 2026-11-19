import { lazy, Suspense } from 'react';
import { useCurrentBrain } from '../../app/context';
import { GraphSkeleton } from './skeleton';

const GraphView = lazy(() => import('./graph-view').then(module => ({ default: module.GraphView })));

/** A live, 280px-high graph with no legend, picker, or zoom controls.
 * Defaults to the current Brain; an explicit id can be used on Brain home. */
export function GraphPreview({ brainId }: { brainId?: string } = {}) {
  const current = useCurrentBrain();
  const id = brainId ?? current.brainId;
  return <Suspense fallback={<GraphSkeleton compact />}><GraphView key={id} brainId={id} compact /></Suspense>;
}