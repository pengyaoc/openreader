export type ViewSelection =
  | { kind: 'saved'; view: 'all' | 'unread' | 'starred' }
  | { kind: 'source'; sourceId: number; title: string }
  | { kind: 'folder'; folder: string }
