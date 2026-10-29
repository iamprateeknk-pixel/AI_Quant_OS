# P01: Repo, dev platform, CI/CD, and environments

## Current disposition (2026-10-29)

P01 has verified local and hosted engineering evidence, and `main` now enforces
the hosted CI gates. P01 is **not accepted**: a staging target and owner have not
been selected, and no independent reviewer is assigned to this phase. This handoff
records implementation evidence; it is not a signed phase acceptance.

## Acceptance evidence

| Requirement | Status | Evidence and limits |
| --- | --- | --- |
| Fresh clone boots | Verified in hosted CI | Run [40836433785](https://github.com/iamprateeknk-pixel/AI_Quant_OS/actions/runs/40836433785) checked out the repository, installed the pinned Windows/Linux Python profiles, installed the package, built the actual web app, and passed the required local-application integration jobs. This does not verify an external staging deployment. |
| CI blocks failing tests | Configured | `main` requires all 17 GitHub Actions checks, requires the branch to be up to date, enforces rules for administrators, disallows force-push/deletion, and requires one approval after the latest push. Verified through the GitHub branch-protection API on 2026-10-29. |
| Secrets scanner passes | Verified | The `secrets` job passed in run 40836433785 using the pinned Gitleaks image. |
| Rollback to prior build is documented | Verified as policy | See `RELEASE_AND_ROLLBACK.md`. The document requires revision-pinned rollback and preservation/reconciliation of state; a staging rollback drill is not recorded. |
| One-command local startup | Implemented and CI-tested | See `LOCAL_APPLICATION_HANDOFF.md` and its hosted Linux/Windows local-application jobs. Production services and staging are outside this verification. |
| Dependency locks | Verified in CI | See `DEPENDENCY_LOCK_HANDOFF.md`; the hosted locked Linux and Windows jobs passed. |
| Staging deployment path | Open | `ENVIRONMENT_STRATEGY.md` says AWS development/staging is not selected or provisioned. No owner, account/region, network/data-flow review, budget, retention/restore plan, or teardown plan is recorded. |
| Independent reviewer and full Definition of Done | Open | No independent reviewer identity or signed P01 acceptance is present. See `DEFINITION_OF_DONE.md`. |

## Hosted verification

PR run [40836433785](https://github.com/iamprateeknk-pixel/AI_Quant_OS/actions/runs/40836433785)
passed all 17 jobs on code revision `0dfd881`: full Python 3,631 passed/71
skipped; locked Linux 3,633 passed/69 skipped; locked Windows 3,638 passed/64
skipped. The identical push run [40836428561](https://github.com/iamprateeknk-pixel/AI_Quant_OS/actions/runs/40836428561)
also passed all 17 jobs. The current branch has documentation-only descendants;
their CI runs are tracked on PR #8.

## Risks and maintenance

- Required status contexts are the current 17 job names in `.github/workflows/ci.yml`.
  Update branch protection when a required job is renamed or removed; otherwise the
  merge gate can remain pending indefinitely.
- Strict up-to-date enforcement can require a PR branch update after `main` moves.
- The approval rule does not assign a reviewer. A human owner must assign and
  obtain an independent review before P01 acceptance.
- Do not configure remote staging until its owner, region, data flow, secrets,
  budget, retention, restore, and teardown plan are approved.

## Rollback

If a required status context becomes obsolete, an administrator should update the
`main` branch-protection contexts to match the active workflow. Keep required CI
checks and independent approval in place. No application data or runtime state was
changed by this P01 increment.
