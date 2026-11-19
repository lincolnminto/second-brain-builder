import { useCurrentBrain } from '../../app/context';
import { GraphView } from './graph-view';

export function GraphPlaceholder() {
  const { brainId } = useCurrentBrain();
  return <GraphView key={brainId} brainId={brainId} />;
}