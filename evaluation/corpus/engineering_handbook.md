# Engineering Handbook

How the Northwind engineering team builds, ships and operates software.

## Code review

Every change to a production service needs at least one approving review from an engineer who did not write it. Changes to authentication, payments or encryption need two approvals, one of them from the security team. Reviews should be completed within one working day; ping the author's team channel if a review is waiting longer.

## Deployments

Services are deployed through the CI pipeline only; manual deployments to production are not allowed. Deployments are frozen from 15 December to 2 January, except for fixes approved by the engineering director. Every deployment must be reversible, and database migrations must be backwards compatible with the previous release.

## On-call rotation

Each product team runs a weekly on-call rotation that starts on Monday at 10:00. On-call engineers receive an allowance of $300 per week. Engineers may swap shifts with a teammate if they update the rota in PagerDuty before the shift starts.

## Postmortems

A blameless postmortem is written for every SEV1 and SEV2 incident within five working days. The postmortem lists the timeline, root cause, impact on customers and follow-up actions with owners. Postmortems are shared with the whole engineering organisation.

## Testing

New code should include automated tests. Pull requests that reduce test coverage of a service below 70% are blocked by the CI pipeline. Flaky tests must be fixed or quarantined within two working days.

## Service ownership

Every production service has an owning team listed in the service catalogue. The owning team is responsible for monitoring, alerts, documentation and on-call support for the service.
