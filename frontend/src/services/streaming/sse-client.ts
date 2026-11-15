import { API_BASE_URL, getActiveCsrfToken } from '../api/client';
import type {
  ChatRequest,
  SSEStartData,
  SSEDeltaData,
  SSEDoneData,
  SSEErrorData,
} from '../../types/api.types';

export interface SSEStreamHandlers {
  onStart?: (data: SSEStartData) => void;
  onDelta?: (text: string) => void;
  onDone?: (data: SSEDoneData) => void;
  onError?: (error: SSEErrorData) => void;
}

export class ChatSSEClient {
  private abortController: AbortController | null = null;

  public cancel(): void {
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
  }

  public async streamChat(
    payload: ChatRequest,
    handlers: SSEStreamHandlers
  ): Promise<void> {
    this.cancel(); // Cancel any existing stream
    this.abortController = new AbortController();

    const url = `${API_BASE_URL}/api/chat/stream`;
    const csrfToken = getActiveCsrfToken();

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    };

    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers,
        credentials: 'include', // Sends asta_session cookie
        body: JSON.stringify(payload),
        signal: this.abortController.signal,
      });
    } catch (err: any) {
      if (err.name === 'AbortError') {
        return; // Normal cancellation
      }
      handlers.onError?.({
        code: 'network_error',
        message: err.message || 'Failed to connect to streaming backend.',
      });
      return;
    }

    if (!response.ok) {
      let errorMessage = `HTTP error ${response.status}`;
      try {
        const errorJson = await response.json();
        errorMessage = errorJson.detail || errorMessage;
      } catch {
        // Fallback to text
      }

      // An expired or missing session should return the user to the login
      // view rather than leaving them on a chat screen that cannot send.
      if (response.status === 401 && typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('asta:unauthorized'));
      }

      handlers.onError?.({
        code: `http_${response.status}`,
        message: errorMessage,
      });
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      handlers.onError?.({
        code: 'no_stream_body',
        message: 'Streaming response body is unavailable.',
      });
      return;
    }

    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Normalize CRLF to LF for consistent SSE frame splitting
        buffer = buffer.replace(/\r\n/g, '\n');

        // SSE frames are delimited by double newlines (\n\n)
        let frameEndIndex: number;
        while ((frameEndIndex = buffer.indexOf('\n\n')) !== -1) {
          const rawFrame = buffer.slice(0, frameEndIndex).trim();
          buffer = buffer.slice(frameEndIndex + 2);

          if (rawFrame) {
            this.parseAndDispatchFrame(rawFrame, handlers);
          }
        }
      }

      // Process any remaining tail buffer
      if (buffer.trim()) {
        this.parseAndDispatchFrame(buffer.trim(), handlers);
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        // Stream aborted gracefully
        return;
      }
      handlers.onError?.({
        code: 'stream_read_error',
        message: err.message || 'Stream connection interrupted.',
      });
    } finally {
      this.abortController = null;
    }
  }

  private parseAndDispatchFrame(
    frame: string,
    handlers: SSEStreamHandlers
  ): void {
    const lines = frame.split('\n');
    let eventName = 'message';
    let dataString = '';

    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataString = line.slice(5).trim();
      }
    }

    if (!dataString) return;

    try {
      const parsedData = JSON.parse(dataString);

      switch (eventName) {
        case 'start':
          handlers.onStart?.(parsedData as SSEStartData);
          break;

        case 'delta':
          handlers.onDelta?.((parsedData as SSEDeltaData).text || '');
          break;

        case 'done':
          handlers.onDone?.(parsedData as SSEDoneData);
          break;

        case 'error':
          handlers.onError?.(parsedData as SSEErrorData);
          break;

        default:
          // Unrecognized event
          break;
      }
    } catch {
      // In case data was non-JSON raw text
      if (eventName === 'delta') {
        handlers.onDelta?.(dataString);
      }
    }
  }
}

export const chatSSEClient = new ChatSSEClient();
export default chatSSEClient;
