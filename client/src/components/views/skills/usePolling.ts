import { useEffect, useRef } from 'react';
import { startPolling } from './asyncTasks';

export const usePolling = (enabled: boolean, load: () => Promise<unknown>, interval = 2000) => {
  const latest = useRef(load);
  latest.current = load;
  useEffect(() => {
    if (enabled) return startPolling(() => latest.current(), interval);
  }, [enabled, interval]);
};
