/**
 * SpecifyPlus integration client (Node.js).
 *
 * Zero dependencies — uses native fetch (Node 18+). Works in browsers too if
 * the API allows CORS.
 *
 * Usage (ESM):
 *   import { SpecifyPlusClient } from "./specifyplus-client.mjs";
 *   const client = new SpecifyPlusClient("http://localhost:8000", "test-key-12345");
 *   const ticket = await client.createTicket({
 *     name: "Ali", email: "ali@example.com",
 *     subject: "Cannot reset password", message: "Need a password reset.",
 *     category: "onboarding", priority: "high",
 *   });
 *
 * Webhook endpoints (/webhooks/*, /health) skip API-key auth. Everything else
 * requires the X-API-Key header.
 */

export class SpecifyPlusClient {
  constructor(baseUrl, apiKey, timeout = 30000) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
    this.timeout = timeout;
  }

  async _request(method, path, { query, json } = {}) {
    const url = new URL(this.baseUrl + path);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, value);
      }
    }
    const headers = { "Content-Type": "application/json" };
    if (this.apiKey) headers["X-API-Key"] = this.apiKey;

    const res = await fetch(url, {
      method,
      headers,
      body: json ? JSON.stringify(json) : undefined,
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`SpecifyPlus ${method} ${path} failed (${res.status}): ${body}`);
    }
    return res.json();
  }

  // --- System ---

  /** GET /health — system status. No API key needed. */
  health() {
    return this._request("GET", "/health");
  }

  /** GET /metrics/dashboard — aggregated KPIs. */
  dashboardMetrics() {
    return this._request("GET", "/metrics/dashboard");
  }

  // --- Tickets ---

  /** POST /tickets — create a new support ticket. */
  createTicket({ name, email, subject, message, category = "general", priority = "medium" }) {
    return this._request("POST", "/tickets", {
      json: { name, email, subject, message, category, priority },
    });
  }

  /** GET /tickets — list tickets with optional status filter. */
  listTickets({ status, limit = 20, offset = 0 } = {}) {
    return this._request("GET", "/tickets", { query: { status, limit, offset } });
  }

  /** GET /tickets/{id} — full ticket incl. messages + agent runs. */
  getTicket(ticketId) {
    return this._request("GET", `/tickets/${ticketId}`);
  }

  /** PATCH /tickets/{id}/status — open|in_progress|resolved|escalated|closed. */
  updateStatus(ticketId, status) {
    return this._request("PATCH", `/tickets/${ticketId}/status`, { json: { status } });
  }

  /** POST /tickets/{id}/reply — append a message to the conversation. */
  replyToTicket(ticketId, message) {
    return this._request("POST", `/tickets/${ticketId}/reply`, { json: { message } });
  }

  /** GET /tickets/{id}/messages — conversation history. */
  ticketMessages(ticketId, limit = 50) {
    return this._request("GET", `/tickets/${ticketId}/messages`, { query: { limit } });
  }

  // --- Customers & Knowledge Base ---

  /** GET /customers/{email}/history — past interactions for a customer. */
  customerHistory(email, { limit = 10, includeResolved = true } = {}) {
    return this._request("GET", `/customers/${encodeURIComponent(email)}/history`, {
      query: { limit, includeResolved },
    });
  }

  /** GET /knowledge-base — pgvector similarity search. */
  searchKnowledgeBase(query, { category, limit = 5, customerTier = "starter" } = {}) {
    return this._request("GET", "/knowledge-base", {
      query: { q: query, category, limit, customer_tier: customerTier },
    });
  }

  /** POST /webhooks/webform — public submission, no API key needed. */
  async submitWebform({ name, email, subject, message, category = "general", priority = "medium" }) {
    const res = await fetch(this.baseUrl + "/webhooks/webform", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, subject, message, category, priority }),
    });
    return res.json();
  }
}

// Run demo directly: node specifyplus-client.mjs
if (process.argv[1] === new URL(import.meta.url).pathname) {
  const client = new SpecifyPlusClient(
    process.env.SPECIFYPLUS_URL || "http://localhost:8000",
    process.env.SPECIFYPLUS_API_KEY || "test-key-12345",
  );

  const run = async () => {
    console.log("health:", await client.health());
    const ticket = await client.createTicket({
      name: "Ali",
      email: "ali@example.com",
      subject: "Cannot reset password",
      message: "I need help resetting my account password.",
      category: "onboarding",
      priority: "high",
    });
    console.log("created:", ticket);
    console.log("status update:", await client.updateStatus(ticket.ticket_id, "in_progress"));
    console.log("reply:", await client.replyToTicket(ticket.ticket_id, "We've emailed you a reset link."));
    console.log("kb search:", await client.searchKnowledgeBase("reset password"));
  };

  run().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}