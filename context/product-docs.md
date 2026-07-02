# TechFlow Analytics — Product Documentation & FAQs

## Account Management

### Q: How do I reset my password?
**A**: Click "Forgot Password" on the login page. You'll receive an email with a reset link valid for 24 hours. If you don't see it, check your spam folder. If using SSO (Enterprise), contact your IT admin.

### Q: How do I upgrade or downgrade my plan?
**A**: Go to Settings → Billing → Plan. Select your new tier. Upgrades take effect immediately; downgrades on next billing cycle. You'll see prorated charges reflected in your next invoice.

### Q: What payment methods do you accept?
**A**: We accept all major credit cards (Visa, Mastercard, American Express), bank transfers (ACH for US customers), and wire transfers (Enterprise only). Invoicing available for annual plans.

### Q: Can I have multiple workspaces?
**A**: Starter tier users get 1 workspace. Growth tier: 5 workspaces. Enterprise: unlimited. Each workspace has separate data, users, and billing.

### Q: How do I invite team members?
**A**: Settings → Users → Invite. Starter allows 2 users, Growth allows 10, Enterprise unlimited. Assign roles: Viewer (read-only), Editor (dashboard editing), Admin (full access).

### Q: Is SSO available on my plan?
**A**: SSO (SAML 2.0, Okta) is Enterprise-only. Growth customers can request it for +$500/month add-on. Uses Okta, Azure AD, or Google Workspace directories.

---

## Data Connectors & Integration

### Q: What data sources can I connect?
**A**: Starter: 5 connectors max (Google Analytics, Salesforce, HubSpot, Mixpanel, Stripe, etc.). Growth & Enterprise: Unlimited. We support 50+ integrations; custom connectors available for Enterprise.

### Q: Why is my connector showing "Failed" status?
**A**: Common causes: (1) Invalid API key/credentials, (2) Revoked permissions, (3) Source account disabled, (4) IP whitelisting (add 203.0.113.42 to allowlist). Reconnect via Connectors → [Name] → Reconnect.

### Q: How often does data refresh?
**A**: Starter: Daily (overnight). Growth: Every 6 hours. Enterprise: Real-time (webhooks) or hourly. Configure refresh time in Connector settings.

### Q: Can I pull custom fields from Salesforce?
**A**: Yes. When setting up Salesforce connector, choose "Advanced Mapping" and select custom fields. Note: Some field types (rich text, encrypted) not supported. Contact support for workarounds.

### Q: What should I do if a connector keeps disconnecting?
**A**: (1) Verify credentials haven't changed. (2) Check if source system revoked permissions (check Salesforce/HubSpot app settings). (3) Reconnect: Connectors → Refresh. If issue persists, delete and recreate connector.

### Q: Do you support incremental syncs or full refresh only?
**A**: Growth & Enterprise support incremental syncs (faster, less API quota used). Starter forces daily full refresh. Enterprise can customize sync behavior (delta detection by timestamp/hash).

---

## Dashboards & Analytics

### Q: How do I create a new dashboard?
**A**: Dashboards → New Dashboard → Add widgets. Choose: metric cards, line charts, bar charts, tables, heatmaps, gauges. Drag to arrange. Save → name it → choose privacy (Personal/Team/Public).

### Q: Can I share dashboards externally?
**A**: Growth & Enterprise only. Dashboards → [Name] → Share → Generate public link (view-only, no data download). Set expiration date if desired. Starter customers can export as PDF instead.

### Q: What's the difference between a metric and a dimension?
**A**: **Metrics** are numeric values you measure (revenue, clicks, signups). **Dimensions** are categorical attributes (region, product, customer tier). Dimensions let you slice metrics (e.g., revenue by region).

### Q: How do I calculate a custom metric?
**A**: Metric builder → Custom formula. Use operators (+, -, *, /) and functions (SUM, AVG, COUNT, IF). Example: `(revenue - costs) / revenue * 100` for profit margin. Works on real-time data for Growth+.

### Q: Why is my dashboard showing stale data?
**A**: Data refreshes on the schedule set per connector. Force refresh: Dashboard → Refresh button. Check Connectors page for last sync timestamp. Enterprise customers can enable real-time syncs.

### Q: Can I set up alerts on metric thresholds?
**A**: Growth & Enterprise only. Metric → Alert → Condition (e.g., "revenue < $5000"). Choose frequency (daily, hourly) and notification (email, Slack). Starter customers can view trends but not automated alerts.

---

## Performance & Optimization

### Q: My dashboard is loading slowly. What can I do?
**A**: (1) Reduce date range (e.g., last 30 days vs. last 2 years). (2) Limit dimensions (fewer slice options = faster query). (3) Pre-aggregate data (use Summary tables). (4) Contact us for query optimization guidance.

### Q: What's the data storage limit for my plan?
**A**: Starter: 1GB, Growth: 100GB, Enterprise: 1TB+ (custom). Storage resets monthly. Exceeding limit? Upgrade plan or archive old data (Starter: 30 days, Growth: 90 days auto-retention).

### Q: How many users can access simultaneously?
**A**: No hard seat limit; depends on plan tier (Starter: 2 users, Growth: 10, Enterprise: unlimited). Concurrent dashboard queries: up to 10 per workspace (Starter), 50 (Growth), unlimited (Enterprise).

### Q: Can I export large datasets?
**A**: Starter: CSV export max 50K rows. Growth/Enterprise: 10M rows. For larger exports, use API with pagination. Enterprise customers get direct database access (read-only).

### Q: How do I optimize slow queries?
**A**: (1) Add indexes on frequently filtered columns. (2) Denormalize data (join tables beforehand). (3) Use Summary tables for pre-aggregated data. (4) Contact Enterprise support for query analysis.

---

## Anomaly Detection (Growth+)

### Q: How does anomaly detection work?
**A**: ML model analyzes metric history (30-90 day baseline). Detects unusual spikes/dips. Sensitivity: Low (>2σ), Medium (>1.5σ), High (>1σ). Growth customers get email alerts; Enterprise gets Slack + webhooks.

### Q: Can I exclude certain dates (holidays, maintenance)?
**A**: Yes. Metrics → Anomaly Settings → Exclude dates. Add holidays or known outages. Model retrains after 7 days of new data for accuracy.

### Q: False alerts on expected changes?
**A**: (1) Increase sensitivity threshold. (2) Exclude dates if expected (product launch, seasonal). (3) For recurring patterns, contact support for custom model tuning (Enterprise).

---

## Billing & Invoicing

### Q: When am I charged?
**A**: Monthly plans: renewal date is same day each month. Annual plans: once per year (get 2 months free). First charge appears on card after 24 hours.

### Q: Can I get an invoice?
**A**: Yes. Settings → Billing → Invoices → Download. All invoices automatically emailed to billing contact. Format: PDF, includes itemized charges and tax info.

### Q: Do you offer annual discounts?
**A**: Yes. Annual plans get 15% off (e.g., Growth: $5,988/yr vs. $5,988 monthly). Enterprise: custom pricing with volume discounts. Contact sales@techflow-analytics.com.

### Q: What happens if my payment fails?
**A**: We retry for 3 days. Account suspended on day 4 (no data loss). Reactivate by updating payment method. Enterprise customers get manual dunning process.

### Q: Can I get a refund?
**A**: 30-day money-back guarantee for new accounts. After 30 days: pro-rata refunds for downgrade only. Cancellations are processed on next billing cycle (no immediate refund).

---

## API & Integrations

### Q: Where do I find my API key?
**A**: Settings → API Keys → New Key. Give it a name (e.g., "Zapier integration"). Copy immediately; we don't store it. Rotate keys quarterly for security.

### Q: What's the API rate limit?
**A**: Starter: 100 req/min, Growth: 1000 req/min, Enterprise: custom. Check `X-RateLimit-Remaining` header. Hitting limit? Implement exponential backoff (retry after 60s).

### Q: Can I embed TechFlow dashboards on my website?
**A**: Enterprise only. We provide embedded links (whitelisted domains only). Public dashboards available for Growth+. Embed code: `<iframe src="...dashboard-id..."></iframe>`.

### Q: Does the API support webhooks?
**A**: Growth & Enterprise. Webhooks for: metric threshold alerts, data refresh completion, user login events. Retry 5x on failure (backoff: 1s, 10s, 100s, 1000s, 10000s).

### Q: How do I authenticate API requests?
**A**: Bearer token in Authorization header: `Authorization: Bearer sk_live_xxxxx`. HTTPS required. Request/response in JSON. See docs.techflow-analytics.com/api for full reference.

---

## Security & Compliance

### Q: Is my data encrypted?
**A**: Yes. In-transit: TLS 1.3. At-rest: AES-256. Database-level encryption (AWS KMS). Encryption keys: AWS-managed (Growth), customer-managed (Enterprise).

### Q: Is TechFlow SOC 2 Type II certified?
**A**: Yes. Valid through Dec 2025. Audit available to Enterprise customers under NDA. Starter/Growth: SOC 2 summary available on request.

### Q: Can I enable two-factor authentication?
**A**: Yes. Settings → Security → 2FA. Options: authenticator apps (Google Authenticator, Authy), SMS. Backup codes provided. Enterprise: enforced 2FA option.

### Q: What's your data retention policy?
**A**: Active accounts: unlimited. Suspended/deleted: 90-day grace period, then auto-deleted. Enterprise: custom retention. Compliance: SOC 2, GDPR, HIPAA (Enterprise add-on).

### Q: Do you do background checks for support staff?
**A**: Yes. All support/engineering staff: background checked. Data access: role-based, monitored. Request audit logs: security@techflow-analytics.com.

---

## General Support

### Q: How do I contact support?
**A**: Email: support@techflow-analytics.com. Response time: Enterprise 1h, Growth 4h, Starter 48h. For urgent issues, submit via in-app chat (Growth+) or call Enterprise hotline.

### Q: Is there a knowledge base?
**A**: Yes. help.techflow-analytics.com (500+ articles). Search topics: account setup, connectors, dashboards, troubleshooting, API, security. Community forum: community.techflow-analytics.com.

### Q: Do you offer training?
**A**: Growth customers: monthly webinars (free). Enterprise: dedicated onboarding + quarterly business reviews. Custom training: $5000/day (enterprise only).
