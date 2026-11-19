import { createContext, useContext } from 'react';
import type { Schema } from '../api/client';
export type BrainContextValue = {
  brainId: string; brain?: Schema['BrainView'];
  events: Schema['JobEventView'][]; connected: boolean;
};
export const BrainContext = createContext<BrainContextValue>({ brainId: '', events: [], connected: false });
export function useCurrentBrain() { return useContext(BrainContext); }
