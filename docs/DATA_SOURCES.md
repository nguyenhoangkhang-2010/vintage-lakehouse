# Data sources and the week-0 gate

The project needs data where **earlier published values are preserved** after revision, so the
state of knowledge at any past date can be reconstructed. Nothing below has been verified yet.
The `feature/week0-data-gate` branch records the findings.

## Gate criteria

A source passes only if all of the following hold:

| # | Criterion | Why it matters |
|---|-----------|----------------|
| 1 | Each event_time has several published versions with their issue dates | Needed for `as_of` |
| 2 | At least 3 years of history, ideally more | Enough forecast origins for stable estimates |
| 3 | Access is reproducible without paid keys | Others must be able to rerun results |
| 4 | Licence allows the use (and, ideally, redistribution of derived results) | Public repository |
| 5 | Rate limits allow a full historical pull in reasonable time | Practical ingestion |

## Candidates (unverified)

| Candidate | Domain | What to check |
|-----------|--------|---------------|
| Delphi Epidata (CMU) | Epidemiology | Whether versions and issue dates can be queried; which signals; terms of use |
| ALFRED (St. Louis Fed) | Economics | Vintage coverage per series; access method; terms of use |
| A regional health or hydrology bulletin archive | Vietnam (stretch) | Whether past bulletins are archived so vintages can be reconstructed; legality of scraping |

## Findings

Fill this table in during the gate. Do not remove failed candidates; record why they failed.

| Candidate | Checked on | Result | Notes |
|-----------|-----------|--------|-------|
| – | – | – | – |
