// scripts/local_worker_server.js
// ==============================
// Local isolated runtime server executing src/worker.js with local D1 SQLite binding.
// Supports [SBK-FIX-E | REAL JS WORKER E2E].

const http = require('http');
const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');

// Suppress non-fatal undici lazy wasm instantiation attempts in 2GB virtual memory container
process.on('unhandledRejection', (err) => {
  if (err && err.message && err.message.includes('WebAssembly')) return;
  console.error('Unhandled Rejection:', err);
});

const REPO_ROOT = path.resolve(__dirname, '..');
const PORT = parseInt(process.env.PORT || '8787', 10);
const HOST = process.env.HOST || '127.0.0.1';

async function startServer() {
  const workerModule = await import('../src/worker.js');
  const worker = workerModule.default;

  // Initialize SQLite in-memory D1 database with production schema and seeds
  const db = new DatabaseSync(':memory:');
  const schemaPath = path.join(REPO_ROOT, 'migrations', '0001_initial_schema.sql');
  const seedsPath = path.join(REPO_ROOT, 'migrations', 'seeds.sql');

  if (fs.existsSync(schemaPath)) {
    db.exec(fs.readFileSync(schemaPath, 'utf-8'));
  }
  if (fs.existsSync(seedsPath)) {
    db.exec(fs.readFileSync(seedsPath, 'utf-8'));
  }

  function createStatement(sql, params = []) {
    return {
      bind(...newParams) {
        return createStatement(sql, newParams);
      },
      async first() {
        const stmt = db.prepare(sql);
        const row = stmt.get(...params);
        return row || null;
      },
      async all() {
        const stmt = db.prepare(sql);
        const results = stmt.all(...params);
        return { results: results || [] };
      },
      async run() {
        const stmt = db.prepare(sql);
        const info = stmt.run(...params);
        return { success: true, meta: { changes: info.changes, last_row_id: info.lastInsertRowid } };
      }
    };
  }

  const d1 = {
    prepare(sql) { return createStatement(sql); },
    async batch(statements) {
      const results = [];
      db.exec('BEGIN IMMEDIATE TRANSACTION');
      try {
        for (const s of statements) {
          results.push(await s.run());
        }
        db.exec('COMMIT');
        return results;
      } catch (err) {
        db.exec('ROLLBACK');
        throw err;
      }
    },
    exec(sql) { return db.exec(sql); }
  };

  const MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.xml': 'application/xml',
    '.txt': 'text/plain; charset=utf-8'
  };

  const env = {
    DB: d1,
    SBK_ADMIN_KEY: process.env.SBK_ADMIN_KEY || 'test_admin_secret_key_fixed_length_32',
    SBK_ATTRIBUTION_SECRET: process.env.SBK_ATTRIBUTION_SECRET || 'test_attribution_secret_key_32c',
    SBK_AES_PII_KEY: process.env.SBK_AES_PII_KEY || '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
    SBK_HMAC_SALT: process.env.SBK_HMAC_SALT || 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210',
    SBK_OPERATOR_APPROVAL: process.env.SBK_OPERATOR_APPROVAL || '1',
    ASSETS: {
      async fetch(req) {
        const u = new URL(req.url);
        let relPath = u.pathname;
        if (relPath === '/') relPath = '/index.html';
        let filePath = path.join(REPO_ROOT, relPath.replace(/^\//, ''));

        if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
          filePath = path.join(filePath, 'index.html');
        }

        if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
          const ext = path.extname(filePath).toLowerCase();
          const contentType = MIME_TYPES[ext] || 'application/octet-stream';
          const content = fs.readFileSync(filePath);
          return new Response(content, {
            status: 200,
            headers: { 'Content-Type': contentType }
          });
        }
        return new Response('Not Found', { status: 404 });
      }
    }
  };

  const server = http.createServer(async (req, res) => {
    try {
      const url = `http://${HOST}:${PORT}${req.url}`;
      const headers = new Headers();
      for (const [k, v] of Object.entries(req.headers)) {
        if (Array.isArray(v)) {
          for (const item of v) headers.append(k, item);
        } else if (v !== undefined) {
          headers.set(k, v);
        }
      }

      const chunks = [];
      for await (const chunk of req) {
        chunks.push(chunk);
      }

      const init = {
        method: req.method,
        headers
      };

      if (req.method !== 'GET' && req.method !== 'HEAD' && chunks.length > 0) {
        init.body = Buffer.concat(chunks);
      }

      const webReq = new Request(url, init);
      const ctx = {
        waitUntil(promise) {
          if (promise && typeof promise.then === 'function') {
            promise.catch(e => console.error('ctx.waitUntil error:', e));
          }
        }
      };

      const webRes = await worker.fetch(webReq, env, ctx);
      res.statusCode = webRes.status;
      for (const [k, v] of webRes.headers.entries()) {
        res.setHeader(k, v);
      }
      const buf = Buffer.from(await webRes.arrayBuffer());
      res.end(buf);
    } catch (e) {
      console.error('Server error processing request:', e);
      res.statusCode = 500;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ error: e.message || 'Internal Server Error' }));
    }
  });

  server.listen(PORT, HOST, () => {
    console.log(`[+] SlotBridge Local Worker Server is LIVE on http://${HOST}:${PORT}`);
  });

  return server;
}

if (require.main === module) {
  startServer().catch(err => {
    console.error('[-] Failed to start SlotBridge Worker server:', err);
    process.exit(1);
  });
}

module.exports = { startServer };
