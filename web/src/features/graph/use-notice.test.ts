import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NOTICE_MS, useNotice } from './use-notice';

afterEach(() => vi.useRealTimers());

describe('status notices', () => {
  it('clear themselves, and a repeated message gets a new id so it is announced again', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useNotice());
    act(() => result.current[1]('Link copied.'));
    const first = result.current[0];
    expect(first?.text).toBe('Link copied.');

    act(() => vi.advanceTimersByTime(NOTICE_MS - 1000));
    act(() => result.current[1]('Link copied.'));
    expect(result.current[0]?.text).toBe('Link copied.');
    expect(result.current[0]?.id).not.toBe(first?.id);

    // The second message restarts the timer.
    act(() => vi.advanceTimersByTime(NOTICE_MS - 1));
    expect(result.current[0]).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(result.current[0]).toBeNull();
  });
});
