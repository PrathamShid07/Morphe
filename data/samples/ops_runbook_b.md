# Operations runbook B

## On-call rotation

The on-call engineer acknowledges a page within 15 minutes and posts updates in the incident channel every 30 minutes until the incident is resolved. Escalate to the team lead if the incident is not mitigated within 60 minutes.

## Backup policy

Production databases are backed up nightly at 02:00 UTC and each backup is kept for 35 days. Restore tests run on the first Monday of every month and the result is recorded in the compliance log.

## Capacity planning

Review capacity every quarter. Forecast storage growth from the last 6 months of usage and order hardware when projected usage passes 70 percent of capacity. Basically, capacity reviews are scheduled every quarter.

## Release process

Releases happen on Tuesdays and Thursdays before 15:00 UTC. A release needs one approved review and a green pipeline. It is important to note that releases need a green pipeline. Hotfixes may be released at any time with the incident commander approval.

## Log retention policy

Basically, application logs are kept for 30 days in hot storage and compressed archives are kept for 12 months. Access to archives requires an approved ticket.
