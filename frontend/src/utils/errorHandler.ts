export interface AppError {
  code: string;
  message: string;
  category?: 'network' | 'validation' | 'permission' | 'system';
  timestamp: string;
}

export function createError(message: string, code = 'GENERIC_ERROR', category: AppError['category'] = 'system'): AppError {
  return {
    code,
    message,
    category,
    timestamp: new Date().toISOString(),
  };
}

export function formatErrorMessage(err: unknown): string {
  if (typeof err === 'string') return err;
  if (err instanceof Error) return err.message;
  return 'An unexpected error occurred';
}
