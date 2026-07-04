/**
 * OCR Studio - WebSocket Client
 * Manages real-time progress update stream and handles auto-reconnections.
 */

let socket = null;
let reconnectTimeoutId = null;

/**
 * Establish WebSocket connection and hook callbacks.
 * Auto-reconnects if connection drops.
 */
export function connectWs(onMessageCallback) {
  // Clear any pending reconnect attempts
  if (reconnectTimeoutId) {
    clearTimeout(reconnectTimeoutId);
    reconnectTimeoutId = null;
  }

  // Construct WebSocket protocol and URL
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/progress`;

  console.log(`[WS] Connecting to ${wsUrl}...`);
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log('[WS] Connected successfully.');
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessageCallback(data);
    } catch (err) {
      console.error('[WS] Failed to parse message data:', err, event.data);
    }
  };

  socket.onclose = (event) => {
    console.warn(`[WS] Connection closed (code: ${event.code}). Reconnecting in 3 seconds...`);
    triggerReconnect(onMessageCallback);
  };

  socket.onerror = (err) => {
    console.error('[WS] Connection error occurred:', err);
    // Socket close event will follow and handle reconnection
  };
}

/**
 * Schedules a reconnection attempt.
 */
function triggerReconnect(onMessageCallback) {
  if (reconnectTimeoutId) return;

  reconnectTimeoutId = setTimeout(() => {
    reconnectTimeoutId = null;
    connectWs(onMessageCallback);
  }, 3000);
}
