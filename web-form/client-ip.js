// Preloaded before Next.js (`node -r ./client-ip.js server.js`, see the
// Dockerfile and `npm start`). Next only fills X-Forwarded-For when the
// header is missing (`??=` in base-server), so a browser could send its own
// value and the route handlers would pass it to the API, which trusts this
// container as a proxy (TRUSTED_PROXIES) and keys its rate limits on it.
// Every request therefore gets X-Forwarded-For overwritten with the TCP peer
// before Next sees it, and the other client-address headers are dropped.
'use strict';

const http = require('http');

const emit = http.Server.prototype.emit;

function peerAddress(socket) {
  const address = (socket && socket.remoteAddress) || '';
  return address.startsWith('::ffff:') ? address.slice(7) : address;
}

http.Server.prototype.emit = function emitWithClientIp(event, req, ...rest) {
  if (event === 'request' && req && req.headers) {
    req.headers['x-forwarded-for'] = peerAddress(req.socket);
    delete req.headers['x-real-ip'];
    delete req.headers['forwarded'];
  }
  return emit.call(this, event, req, ...rest);
};

module.exports = { peerAddress };
