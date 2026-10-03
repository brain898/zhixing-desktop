/** Latest request wins, including transports that resolve after abort. */
export const createLatestCheck = <T, R>(
  request: (value: T, signal: AbortSignal) => Promise<R>,
  handlers: { result: (result: R) => void; error: (error: unknown) => void; busy: (busy: boolean) => void },
) => {
  let sequence = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  const invalidate = () => {
    sequence += 1;
    clearTimeout(timer);
    controller?.abort();
  };
  return {
    schedule(value: T, delay: number) {
      invalidate();
      const current = sequence;
      const active = new AbortController();
      controller = active;
      handlers.busy(true);
      const run = async () => {
        try {
          const result = await request(value, active.signal);
          if (current === sequence && !active.signal.aborted) handlers.result(result);
        } catch (error) {
          if (current === sequence && !active.signal.aborted) handlers.error(error);
        } finally {
          if (current === sequence) handlers.busy(false);
        }
      };
      if (delay === 0) void run();
      else timer = setTimeout(run, delay);
    },
    cancel() {
      invalidate();
      handlers.busy(false);
    },
  };
};

/** Schedule after completion so failures retry without overlapping polls. */
export const startPolling = (load: () => Promise<unknown>, interval: number) => {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  const tick = async () => {
    try {
      await load();
    } catch {
      // The caller presents request errors; a failed request still needs another poll.
    } finally {
      if (!stopped) timer = setTimeout(tick, interval);
    }
  };
  timer = setTimeout(tick, interval);
  return () => { stopped = true; clearTimeout(timer); };
};
