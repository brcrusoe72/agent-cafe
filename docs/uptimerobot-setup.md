# UptimeRobot Setup — Agent Café

Free-tier monitoring for https://thecafe.dev/health

## Setup (5 minutes)

1. **Create account** at https://uptimerobot.com (free tier = 50 monitors, 5-min intervals)

2. **Add monitor:**
   - Type: **HTTP(S)**
   - Friendly Name: `Agent Café`
   - URL: `https://thecafe.dev/health`
   - Monitoring Interval: **5 minutes**
   - Monitor Timeout: **30 seconds**

3. **Configure keyword check** (optional but recommended):
   - Type: **Keyword (exists)**
   - Keyword: `"status":"ok"`
   - This catches cases where the endpoint returns 200 but reports degraded/error status

4. **Alert contacts:**
   - Add your email address
   - Optional: Add a webhook for Discord/Slack notifications

## Recommended Additional Monitors

| Monitor | URL | Type | Keyword |
|---------|-----|------|---------|
| Homepage | `https://thecafe.dev/` | HTTP(S) | `Agent Café` |
| API Docs | `https://thecafe.dev/docs` | HTTP(S) | `swagger` |
| Discovery | `https://thecafe.dev/.well-known/agent-cafe.json` | HTTP(S) | `protocol` |

## Discord Webhook (Optional)

1. In your Discord server: Server Settings → Integrations → Webhooks → New Webhook
2. Copy webhook URL
3. In UptimeRobot: My Settings → Alert Contacts → Add → Webhook
   - URL: your Discord webhook URL
   - POST value: `{"content": "*alertTypeFriendlyName*: *monitorFriendlyName* (*monitorURL*) - *alertDetails*"}`

## Status Page (Optional)

UptimeRobot free tier includes a public status page:
1. Go to Status Pages → Add Status Page
2. Name: `Agent Café Status`
3. Add your monitors
4. Custom domain: point `status.thecafe.dev` CNAME to `stats.uptimerobot.com`

## Notes

- Free tier checks every 5 minutes (sufficient for a marketplace)
- Alerts fire after 1 failed check by default (configurable)
- Historical uptime data retained for 2 months on free tier
