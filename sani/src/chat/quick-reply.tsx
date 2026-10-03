import { createContext, useContext } from "react";

/** Lets a message send a tapped choice as the user's next message. */
export interface QuickReply {
  send: (text: string) => void;
  /** False while a turn is running or voice is capturing. */
  enabled: boolean;
}

export const QuickReplyContext = createContext<QuickReply>({ send: () => undefined, enabled: false });
export const useQuickReply = () => useContext(QuickReplyContext);
