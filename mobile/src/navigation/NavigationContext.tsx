import React, { createContext, useContext, useState } from 'react';

export type MainTab = 'home' | 'tasks' | 'calendar' | 'asta' | 'profile';

export type ActiveModal =
  | null
  | { type: 'create_task'; initialDate?: string; initialCategory?: string }
  | { type: 'task_details'; taskId: string }
  | { type: 'focus_mode'; taskId?: string }
  | { type: 'notes' }
  | { type: 'analytics' }
  | { type: 'reminders' }
  | { type: 'settings' };

interface NavigationContextType {
  currentTab: MainTab;
  activeModal: ActiveModal;
  switchTab: (tab: MainTab) => void;
  openModal: (modal: ActiveModal) => void;
  closeModal: () => void;
}

const NavigationContext = createContext<NavigationContextType | null>(null);

export const NavigationProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [currentTab, setCurrentTab] = useState<MainTab>('home');
  const [activeModal, setActiveModal] = useState<ActiveModal>(null);

  const switchTab = (tab: MainTab) => {
    setCurrentTab(tab);
  };

  const openModal = (modal: ActiveModal) => {
    setActiveModal(modal);
  };

  const closeModal = () => {
    setActiveModal(null);
  };

  return (
    <NavigationContext.Provider
      value={{
        currentTab,
        activeModal,
        switchTab,
        openModal,
        closeModal,
      }}
    >
      {children}
    </NavigationContext.Provider>
  );
};

export function useNavigation(): NavigationContextType {
  const context = useContext(NavigationContext);
  if (!context) {
    throw new Error('useNavigation must be used within a NavigationProvider');
  }
  return context;
}
