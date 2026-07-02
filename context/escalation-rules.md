# TechFlow Analytics — Escalation Rules

## Escalation Logic

When any of the following conditions are met, the ticket should be automatically escalated to a human CS agent via Kafka `escalations` topic.

---

## Rule 1: Payment/Billing Issues
**Trigger**: Customer tier = Enterprise OR (tier = Growth + billing_related = true)
**Priority**: HIGH
**Notify Team**: #billing-team
**Context Fields**: Payment method, invoice, account history, customer lifetime value
**Reason**: Billing issues require human judgment and can affect retention

**Example**: Customer reports unauthorized charge, prorated billing confusion, or payment method declined.

---

## Rule 2: Data Accuracy Concerns
**Trigger**: Category = "analytics" AND (escalation_reason LIKE "%data_mismatch%" OR data_variance > 10%)
**Priority**: HIGH
**Notify Team**: #data-science-team
**Context Fields**: Metric definitions, historical comparison, data source logs, customer tier
**Reason**: Data accuracy concerns can undermine customer trust; require investigation

**Example**: Customer reports Google Analytics vs TechFlow showing different visitor counts (>10% discrepancy).

---

## Rule 3: Custom Development Request
**Trigger**: Category = "connector" AND request_type = "custom" OR feature_request = "new_integration"
**Priority**: MEDIUM
**Notify Team**: #engineering-team
**Context Fields**: Customer tier, technical requirements, estimated effort, integration details
**Reason**: Custom integrations require engineering scope and cost estimation

**Example**: Enterprise customer requests custom connector for proprietary internal database.

---

## Rule 4: Enterprise SLA Breach Risk
**Trigger**: Customer tier = "enterprise" AND response_time > 60 minutes
**Priority**: CRITICAL
**Notify Team**: #cs-manager, #engineering
**Context Fields**: Customer name, SLA commitments, issue severity, account risk
**Reason**: Enterprise SLAs are contractual; breach risks account health

**Example**: Enterprise customer reports data connector failure with 70-minute response time.

---

## Rule 5: Product Bug / Service Degradation
**Trigger**: Keyword match (bug_keywords) AND category != "user_error"
**Keywords**: "broken", "error", "crash", "failed", "not_working", "outage"
**Priority**: HIGH
**Notify Team**: #engineering-team, #product
**Context Fields**: Error message, reproduction steps, affected feature, customer tier
**Reason**: Genuine bugs require engineering investigation and potential hotfix

**Example**: User reports "Dashboard export always fails with 500 error".

---

## Rule 6: Feature Request / Product Feedback
**Trigger**: Request type = "feature" AND customer tier = "enterprise"
**Priority**: MEDIUM
**Notify Team**: #product-team
**Context Fields**: Feature description, business value, competitive context, customer industry
**Reason**: Enterprise feature requests inform product roadmap prioritization

**Example**: Enterprise customer requests real-time anomaly detection with Slack alerts.

---

## Rule 7: Security / Compliance Issue
**Trigger**: Keyword match (security_keywords) OR category = "security"
**Keywords**: "security", "password", "breach", "hacked", "compliance", "audit", "encryption"
**Priority**: CRITICAL
**Notify Team**: #security-team, #legal
**Context Fields**: Issue details, customer data classification, regulatory impact, account value
**Reason**: Security issues require immediate attention and may have legal implications

**Example**: Customer reports suspected account compromise or asks for HIPAA compliance details.

---

## Rule 8: Negative Sentiment / Churn Risk
**Trigger**: Sentiment analysis = "negative" OR satisfaction_score <= 2 OR churn_risk_score > 0.7
**Priority**: MEDIUM
**Notify Team**: #cs-manager
**Context Fields**: Customer history, MRR, feedback details, success metrics, customer notes
**Reason**: Negative sentiment indicates retention risk; human touch required

**Example**: Customer expresses frustration about repeated connector failures and mentions considering competitors.

---

## Escalation Response Time Targets

| Priority | Target Response | Target Resolution | Notify Delay |
|----------|-----------------|-------------------|--------------|
| CRITICAL | 15 minutes | 2 hours | Immediate (Slack + SMS) |
| HIGH | 30 minutes | 4 hours | 2 minutes |
| MEDIUM | 1 hour | 24 hours | 5 minutes |
| LOW | 4 hours | 48 hours | Next business day |

---

## Post-Escalation Workflow

1. **Escalation Triggered**: Message published to `escalations` Kafka topic with full context
2. **Human Assignment**: Assigned to available CS agent by seniority/availability
3. **Context Review**: Agent reviews escalation context and customer history (< 2 min)
4. **Response**: Human agent responds within target time with resolution path
5. **Resolution**: Agent closes ticket or assigns to engineering if needed
6. **Post-Mortem**: Review escalation trigger to improve AI agent accuracy

---

## Escalation Feedback Loop

Every month, review escalation data:
- **Escalations per category**: Identify if certain categories have high escalation rates
- **False escalations**: Tickets escalated but could be handled by AI
- **Missed escalations**: Tickets that should have been escalated but weren't
- **Agent feedback**: CS team feedback on escalation quality and context usefulness

Adjust rules quarterly based on patterns:
- If escalation rate > 15% in a category, improve AI agent training data
- If missed escalations found, tighten trigger thresholds
- If false escalations > 10%, refine keyword lists or conditions
