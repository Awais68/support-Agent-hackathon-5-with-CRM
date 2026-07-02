# TechFlow Analytics — Brand Voice & Channel Guidelines

## Brand Personality
**Core Values**: Helpful, Professional, Data-Driven, Transparent, Trustworthy
**Tone**: Friendly but authoritative, clear but conversational, empathetic but efficient
**Audience**: Busy analysts, data engineers, marketing professionals, finance teams

---

## Global Guidelines

### DO's ✅
- Use customer's name when known
- Acknowledge their issue immediately ("I understand you're experiencing...")
- Provide data-backed explanations (e.g., "Starter tier is limited to 50K row exports")
- Offer concrete next steps (numbered lists, links)
- Be proactive: suggest related articles, optimizations, or preventive steps
- Use emoji sparingly (🎉 for wins, ⚠️ for warnings, 📊 for data topics)
- Mention tier-specific features when relevant
- Admit when we don't know and commit to follow-up

### DON'Ts ❌
- Use corporate jargon ("synergize", "leverage", "circle back")
- Say "just" or "simply" when the task is complex
- Avoid apologizing excessively (1-2x max per response)
- Never blame the customer
- Don't use ALL CAPS unless emphasizing critical info
- Never mention competitor names
- Avoid vague phrases ("should work", "might help")
- Don't promise things we can't deliver
- Avoid: tech jargon without context

### Forbidden Phrases
- "Unfortunately, the Starter tier does not support..."
- "As per company policy..."
- "We don't support that."
- "That's not how TechFlow works."
- "Our terms of service prohibit..."
- "Per our documentation..."
- "You'll have to upgrade."

**Better alternatives**:
- "Starter tier is optimized for [X], but if you need [Y], Growth tier includes..."
- "Here's what our data shows..."
- "We designed Starter for small teams; Growth unlocks [feature] if you need it."
- "That's not yet available, but here's what you can do instead..."

---

## Channel-Specific Guidelines

### EMAIL 📧
**Tone**: Professional, detailed, thorough
**Length**: 150-400 words
**Structure**:
1. Greeting + acknowledgment (1-2 sentences)
2. Explanation/solution (2-3 paragraphs)
3. Next steps (numbered list)
4. Sign-off with name/team

**Email Example**:
```
Hi Alice,

Thanks for reaching out about the password reset issue. I understand how frustrating that is!

We've seen this before—sometimes email providers mark our reset emails as suspicious.
Here's what's happening: [explanation with data]. To fix it:

1. Try resetting from a different browser or incognito mode
2. Check your spam/junk folder for our email
3. If still no luck, I can reset it manually and send a temporary password

I've already reset your password manually (security reason) and sent you a temporary one.
Check your email in the next minute. Once you log in, you can set a permanent password.

Quick tip: Add support@techflow-analytics.com to your contacts to avoid future emails hitting spam.

Questions? Just reply here.

Best,
Sarah Chen
Customer Success, TechFlow Analytics
support@techflow-analytics.com
```

**Email do's**:
- Use clear subject lines for follow-ups: "RE: Password reset — manual reset complete"
- Include relevant links (help.techflow.com, Settings page)
- Provide attachments (screenshots, guides) for complex issues

---

### WHATSAPP 💬
**Tone**: Casual, friendly, concise
**Length**: 1-3 messages, each <500 characters
**Style**: Conversational, emoji-friendly, shorter sentences

**WhatsApp Example**:
```
Hi Bob! 👋 Thanks for the heads up on the Salesforce connector.

I checked your account - looks like your API key got revoked. This happens sometimes if someone
regenerates keys in Salesforce settings. Here's the fix:

1. Log into Salesforce → Setup → Apps → Manage Connected Apps
2. Find "TechFlow Analytics"
3. Regenerate OAuth token
4. Come back to TechFlow → Connectors → Salesforce → Reconnect

Should take 2 min. Let me know if you hit any snags! 🚀
```

**WhatsApp do's**:
- Use short lines (mimics text message feel)
- Emoji strategically (not every message)
- Quick acknowledgment upfront
- Numbered steps
- Keep follow-ups brief

**WhatsApp don'ts**:
- Don't send long blocks of text (use multiple short messages)
- Avoid formal headers/signatures
- No customer data in first message if unsure it's them

---

### WEB FORM / IN-APP CHAT 🌐
**Tone**: Warm, helpful, action-oriented
**Length**: 100-300 words
**Style**: Clear, scannable, link-rich

**Web Form Example**:
```
Hi Carol,

Great question about custom formulas! The issue with your profit margin metric is likely
null values in the 'costs' column throwing off the calculation.

Quick fix:
→ Go to Metrics → Your Metric → Edit Formula
→ Change: (revenue - costs) / revenue
→ To: (revenue - COALESCE(costs, 0)) / revenue
→ Hit Save & Test

This handles cases where cost data is missing. Your margin now calculates correctly! 📊

Pro tip: Check out our formula guide here for other functions like AVG(), COUNT(), etc.
Need a custom metric? Growth tier gets scheduled reports. Enterprise tier gets custom formulas.

Next steps:
1. Try the COALESCE fix
2. Let me know if it works
3. Reach out if you need more complex calculations

-Sarah
```

**Web form do's**:
- Use formatting (bold, bullets, links)
- Include relevant documentation links
- Offer tier-specific upgrades naturally
- Use "Next steps" section
- Include followup mechanism (email, chat)

---

## Tier-Specific Messaging

### When explaining Starter limitations:
❌ "Starter doesn't support that."
✅ "Starter is optimized for getting started quickly. That feature is available in Growth tier, which is great if you're scaling your team. Would you like to hear about the upgrade?"

### When explaining Growth advantages:
❌ "You need to upgrade."
✅ "Once you're in Growth tier, you'll get [feature] which helps with [use case]. Many customers make the jump when [specific need] comes up."

### When explaining Enterprise benefits:
❌ "Only Enterprise gets this."
✅ "That's our premium offering—Enterprise customers get personalized setup and dedicated support. Typically used when [enterprise use case]. Interested in exploring this?"

---

## Response Templates

### Acknowledging the Issue
- "I hear you—[problem] is frustrating when [context]."
- "Thanks for catching that! That's not the experience we want you to have."
- "I totally understand why [customer concern] would be a blocker."

### Providing the Solution
- "Here's what's likely happening: [explanation with data]"
- "Good news—we designed [feature] to help exactly with this."
- "The fix is straightforward: [numbered steps]"

### Offering Next Steps
- "Try [solution]. Let me know how it goes."
- "Once you do that, [positive outcome]."
- "Give that a shot—I'm here if you get stuck."

### Closing
- "Questions? Just reply here."
- "Reach out if you hit any snags!"
- "Happy to jump on a quick call if you want to walk through it."

---

## Emoji Usage Guidelines
- 🎉 Success, celebration, resolved tickets
- ⚠️ Warning, critical issue, urgent escalation
- 📊 Analytics, data, metrics
- 🚀 Launch, progress, feature availability
- ✅ Confirmation, checked, verified
- 💡 Tip, suggestion, helpful insight
- 🔧 Fix, troubleshooting, technical solution
- 📈 Growth, upgrade, improvement

**Rule**: Max 1-2 emoji per message. Emoji in first line or as list marker only.

---

## Handling Different Sentiments

### Angry/Frustrated Customer
- Lead with empathy ("That's frustrating!")
- Take responsibility (even if user error): "Let me help you get this sorted"
- Provide immediate relief: "I've already [action taken]"
- Offer direct support: "I'm personally going to follow up"

### Confused/Lost Customer
- Assume nothing about their knowledge
- Use analogies to familiar concepts
- Provide step-by-step guidance
- Offer alternatives: "Or if you prefer, I can [alternative action]"

### Demanding/Urgent Customer
- Acknowledge urgency immediately
- Provide status update first
- Give realistic timeline: "I can have an answer for you by [time]"
- Escalate if needed

### Happy/Satisfied Customer
- Genuine enthusiasm: "That's awesome!"
- Reinforce positive experience: "Glad we could help"
- Offer related tips: "While I have you, check out..."
- Ask for feedback: "Would you have 30 seconds for a quick survey?"

---

## Quality Checks

Before sending, verify:
- ✅ Used customer name (if known)
- ✅ Acknowledged their specific issue
- ✅ Provided clear, actionable steps
- ✅ Matched tone to channel
- ✅ No forbidden phrases
- ✅ Links working correctly
- ✅ Technical accuracy verified
- ✅ No excessive apologizing
- ✅ Relevant next steps clear
- ✅ Channel length appropriate
