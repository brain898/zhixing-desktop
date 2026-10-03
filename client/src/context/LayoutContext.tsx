import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';

const SIDEBAR_STORAGE_KEY = 'zhixing.sidebarCollapsed';

interface LayoutContextType {
  /** 用户手动选择的侧栏折叠状态 */
  sidebarCollapsed: boolean;
  /** 专注模式（如章节集中核对）会临时强制收起侧栏 */
  focusMode: boolean;
  /** 实际是否收起：手动折叠或处于专注模式 */
  isSidebarRail: boolean;
  toggleSidebar: () => void;
  setFocusMode: (value: boolean) => void;
}

const LayoutContext = createContext<LayoutContextType | undefined>(undefined);

const readStoredCollapsed = (): boolean => {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
};

export const LayoutProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(readStoredCollapsed);
  const [focusMode, setFocusModeState] = useState(false);

  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarCollapsed ? '1' : '0');
    } catch {
      // 本地存储不可用时仅保留会话内状态
    }
  }, [sidebarCollapsed]);

  const toggleSidebar = useCallback(() => setSidebarCollapsed((prev) => !prev), []);
  const setFocusMode = useCallback((value: boolean) => setFocusModeState(value), []);

  return (
    <LayoutContext.Provider
      value={{
        sidebarCollapsed,
        focusMode,
        isSidebarRail: sidebarCollapsed || focusMode,
        toggleSidebar,
        setFocusMode,
      }}
    >
      {children}
    </LayoutContext.Provider>
  );
};

export const useLayout = (): LayoutContextType => {
  const ctx = useContext(LayoutContext);
  if (!ctx) {
    throw new Error('useLayout must be used within LayoutProvider');
  }
  return ctx;
};
