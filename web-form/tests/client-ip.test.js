// npm test: the preload must replace any client-sent address.
'use strict';

require('../client-ip.js');

const assert = require('node:assert/strict');
const http = require('node:http');
const { test } = require('node:test');

function serveOnce(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function get(port, headers, localAddress) {
  return new Promise((resolve, reject) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: '/', headers, localAddress },
      (res) => {
        let body = '';
        res.on('data', (chunk) => (body += chunk));
        res.on('end', () => resolve(JSON.parse(body)));
      }
    );
    req.on('error', reject);
  });
}

async function seenHeaders(headers, localAddress) {
  const server = await serveOnce((req, res) => {
    res.end(JSON.stringify(req.headers));
  });
  try {
    return await get(server.address().port, headers, localAddress);
  } finally {
    server.close();
  }
}

test('a spoofed X-Forwarded-For is replaced by the TCP peer', async () => {
  const seen = await seenHeaders({
    'X-Forwarded-For': '192.0.2.1, 198.51.100.7',
    'X-Real-IP': '192.0.2.2',
    Forwarded: 'for=192.0.2.3',
  });
  assert.equal(seen['x-forwarded-for'], '127.0.0.1');
  assert.equal(seen['x-real-ip'], undefined);
  assert.equal(seen['forwarded'], undefined);
});

test('a request without the header gets the peer address', async () => {
  const seen = await seenHeaders({});
  assert.equal(seen['x-forwarded-for'], '127.0.0.1');
});

test('two clients are told apart by their own address', async (t) => {
  let other;
  try {
    other = await seenHeaders({ 'X-Forwarded-For': '127.0.0.1' }, '127.0.0.2');
  } catch {
    t.skip('127.0.0.2 is not usable on this host');
    return;
  }
  assert.equal(other['x-forwarded-for'], '127.0.0.2');
});
