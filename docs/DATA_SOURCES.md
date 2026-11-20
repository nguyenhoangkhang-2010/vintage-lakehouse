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

## Candidates

| Candidate | Domain | What to check |
|-----------|--------|---------------|
| Delphi Epidata, FluView endpoint (CMU) | Epidemiology | Versions and issue dates; access; terms of use |
| ALFRED / FRED API (St. Louis Fed) | Economics | Vintage coverage per series; access method; terms of use |
| A regional health or hydrology bulletin archive | Vietnam (stretch) | Whether past bulletins are archived so vintages can be reconstructed; legality of scraping |

## Findings

Do not remove failed candidates; record why they failed.

| Candidate | Checked on | Result | Notes |
|-----------|-----------|--------|-------|
| Delphi FluView (national wILI) | 2026-11-20 | **Passes criteria 1, 2, 3, 5 on a sample; criterion 4 (licence) still manual** | See "Delphi FluView details" below |
| ALFRED / FRED API | 2026-11-20 | **Not evaluated: blocked on an API key** | The FRED API answered HTTP 400 "api_key is not set" without a key; the ALFRED CSV endpoint reset the connection. Per FRED's documentation the key is free but personal, so the account must be created by the project owner. Revisit only if a second domain is wanted |
| Vietnam bulletin archive | – | Not checked | Stretch goal; needs a feasibility and legality check first |

### Delphi FluView details

Gate output of `vintage-lakehouse gate delphi-fluview` on 2026-11-20 (national region,
weighted ILI, epiweeks 5 and 25 of each year 2019 to 2025, 20 weekly issues each):

| # | Criterion | Status | Detail |
|---|-----------|--------|--------|
| 1 | Several published versions per event time | pass | 14 event times; versions each: min 15, median 21; 100% changed value between versions |
| 2 | At least 3 years of history | pass | 6.4 years sampled (2019-01-27 to 2025-06-15) |
| 3 | Reproducible access without paid keys | pass | anonymous requests succeeded |
| 4 | Licence allows the use | manual | not yet verified, see below |
| 5 | Rate limits allow a full pull | pass (sample only) | 14 requests in 23.1s, 288 rows, no throttling; a full historical pull was not tested |

What this does and does not show:

- Each sampled week was published many times and its value changed between versions, so
  `as_of` reconstruction is possible for early revisions (the first 20 weekly issues).
- Later revisions exist too (for example week 202301 also has an issue from 2024) but were not
  sampled yet.
- **Access policy (from Delphi documentation found by web search, not tested here):**
  anonymous access is rate-limited and restricted to public datasets, with limits on how many
  request parameters may select multiple values. A free key lifts these limits. A full
  historical pull will probably need a free key, which the project owner must register. The
  ingestion branch must read it from an environment variable and never commit it.
- **Licence (criterion 4) is still open.** The Delphi documentation asks users to cite the
  source for non-COVIDcast endpoints, and the underlying FluView data originates from the US
  CDC. The full terms were not read. Before publishing derived results, read the terms linked
  below and record the conclusion here.

References to read for criterion 4:

- Delphi Epidata documentation, API keys and privacy statement pages
- FRED API terms of use (only if ALFRED is revisited)

### Rerun the gate

```text
vintage-lakehouse gate delphi-fluview
pytest -m network
```

The first command makes 14 requests to the live API and exits with 0 (no failed criteria),
1 (a criterion failed) or 2 (the source could not be reached).
