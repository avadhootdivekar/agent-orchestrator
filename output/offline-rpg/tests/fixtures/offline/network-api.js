export const ping = () => fetch('/api/ping');
const xhr = new XMLHttpRequest();
const socket = new WebSocket('ws://localhost:9000');
const events = new EventSource('/stream');
navigator.sendBeacon('/telemetry', 'x');
