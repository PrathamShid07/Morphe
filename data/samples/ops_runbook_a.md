# Operations runbook A

## On-call rotation

The on-call engineer acknowledges a page within 15 minutes and posts updates in the incident channel every 30 minutes until the incident is resolved. Escalate to the team lead if the incident is not mitigated within 60 minutes.

## Backup policy

Production databases are backed up nightly at 02:00 UTC and each backup is kept for 35 days. Restore tests run on the first Monday of every month and the result is recorded in the compliance log.

## Alert thresholds

Page the on-call engineer when CPU stays above 85 percent for 10 minutes, when disk usage passes 90 percent, or when the error rate exceeds 2 percent of requests for 5 minutes.

## Deployment checklist

Before every deployment confirm that the build is green. It is important to note that the build must be green. Confirm that database migrations were reviewed. Basically, check that the rollback plan is documented in the ticket. After the deployment watch the dashboards for 30 minutes. It is important to note that the dashboards must be watched for 30 minutes.

## Log retention

Application logs are kept for 30 days in hot storage and compressed archives are kept for 12 months. Access to archives requires an approved ticket.
